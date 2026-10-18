package online.thrax.switcher.features

import android.app.Notification
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.provider.Settings
import android.service.notification.StatusBarNotification
import android.util.Base64
import android.util.Log
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import kotlinx.coroutines.flow.MutableStateFlow
import online.thrax.switcher.ui.Label
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * The device's notifications on the PC, with "Dismiss on phone" and inline "Reply" there.
 *
 * Android delivers notifications to a [NotificationListener] component the user (or the hub, over ADB)
 * has granted notification access. Turning the feature off disables that component, so Android doesn't
 * bind it at all — no notification reaches the app while the feature is off.
 */
class NotificationsFeature : Feature {
    override val id = "notifications"
    override val title = "Notifications"
    override val summary = "See this device's notifications on the PC; dismiss or reply from there"
    override val icon = Icons.Outlined.Notifications
    override val messageTypes = setOf("notification_action")
    override val status = MutableStateFlow("off")
    private val lastEvent = MutableStateFlow("")

    private var host: FeatureHost? = null
    /** From the PC: apps' switches, and what apps without one do. Filtering happens here, on the device. */
    @Volatile private var showByDefault = true
    @Volatile private var appSwitches: Map<String, Boolean> = emptyMap()
    private val seenApps = mutableSetOf<String>()
    private val iconsSent = mutableSetOf<String>()   // app icons the hub already has (this connection)
    private val lastSent = mutableMapOf<String, String>()  // key -> title+text, to skip no-change updates

    override fun permissions(ctx: Context): List<Need> = listOf(
        Need(
            "Read notifications (notification access)",
            NotificationAccess.granted(ctx),
            Fix.OpenSettings("Open settings") { Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS) },
        ),
    )

    override fun start(host: FeatureHost) {
        this.host = host
        active = this
        NotificationAccess.use(host.context, id, true)
        status.value = if (NotificationAccess.granted(host.context)) "Forwarding notifications" else "Needs notification access"
    }

    override fun stop() {
        host?.let { NotificationAccess.use(it.context, id, false) }
        active = null
        host = null
        iconsSent.clear()
        lastSent.clear()
        status.value = "off"
    }

    override fun onSettings(settings: JSONObject) {
        val before = appSwitches to showByDefault
        showByDefault = settings.optBoolean("show_new_apps", true)
        val apps = settings.optJSONObject("apps")
        appSwitches = apps?.keys()?.asSequence()?.associateWith { apps.getBoolean(it) } ?: emptyMap()
        if (before == (appSwitches to showByDefault)) return
        // Apps switched off: take their notifications off the PC now.
        val l = NotificationAccess.listener ?: return
        runCatching { l.activeNotifications }.getOrNull()?.forEach { sbn ->
            if (!allowed(sbn.packageName) && lastSent.remove(sbn.key) != null) {
                host?.send(JSONObject().put("type", "notification_removed").put("key", sbn.key))
            }
        }
    }

    private fun allowed(pkg: String) = appSwitches[pkg] ?: showByDefault

    override fun onConnected() {
        iconsSent.clear()  // the hub may have restarted; resend icons and what's on screen now
        lastSent.clear()
        host?.let { sendAppList(it) }
        NotificationAccess.listener?.let { l -> runCatching { l.activeNotifications }.getOrNull()?.forEach { posted(l, it, replay = true) } }
    }

    @Composable
    override fun Details() {
        val event by lastEvent.collectAsState()
        if (event.isNotEmpty()) Label("Last: $event")
    }

    /** "dismiss" or "reply" pressed on the PC. */
    override fun onMessage(msg: JSONObject) {
        val l = NotificationAccess.listener ?: return
        val key = msg.optString("key")
        val sbn = runCatching { l.activeNotifications }.getOrNull()?.firstOrNull { it.key == key } ?: return
        when (msg.optString("action")) {
            "dismiss" -> {
                l.cancelNotification(key)
                lastEvent.value = "Dismissed from the PC"
            }
            "reply" -> {
                if (reply(l, sbn, msg.optString("text"))) lastEvent.value = "Replied from the PC"
            }
        }
    }

    // --- from the listener -------------------------------------------------------------------------------

    /**
     * [replay]: re-sent because the connection or listener came back, not because something new arrived.
     * The hub uses replays only to keep notifications it already shows up to date — never to pop up again.
     */
    fun posted(l: NotificationListener, sbn: StatusBarNotification, replay: Boolean = false) {
        val h = host ?: return
        val n = sbn.notification
        if (sbn.packageName == l.packageName) return
        if (n.flags and (Notification.FLAG_ONGOING_EVENT or Notification.FLAG_FOREGROUND_SERVICE) != 0) return
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        if (n.category == Notification.CATEGORY_CALL) return  // the Calls feature's, not a regular notification
        if (seenApps.add(sbn.packageName) && sbn.packageName !in launchable(l)) host?.let { sendAppList(it) }
        if (!allowed(sbn.packageName)) return  // filtered on the PC's say-so; never leaves the device
        val extras = n.extras
        val chat = messages(extras)
        val title = (extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)
            ?: extras.getCharSequence(Notification.EXTRA_TITLE))?.toString().orEmpty()
        val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras.getCharSequence(Notification.EXTRA_TEXT))
            ?.toString().orEmpty()
        if (title.isEmpty() && text.isEmpty() && chat.length() == 0) return
        val digest = title + "\u0000" + text + "\u0000" + chat.toString()
        if (lastSent[sbn.key] == digest) return  // progress/timestamp-only update
        lastSent[sbn.key] = digest
        val pm = l.packageManager
        val app = runCatching { pm.getApplicationLabel(pm.getApplicationInfo(sbn.packageName, 0)).toString() }
            .getOrDefault(sbn.packageName)
        val out = JSONObject().put("type", "notification").put("key", sbn.key).put("app", app)
            .put("package", sbn.packageName).put("title", title).put("text", text).put("time", sbn.postTime)
            .put("reply", replyAction(n) != null)
        if (replay) out.put("replay", true)
        if (chat.length() > 0) out.put("messages", chat)
        extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.let { out.put("subtext", it.toString()) }
        largeImage(l, n)?.let { out.put("image", it) }
        if (sbn.packageName !in iconsSent) {
            appIcon(l, sbn.packageName)?.let { out.put("icon", it) }
            iconsSent += sbn.packageName
        }
        if (h.send(out)) lastEvent.value = "Sent: $app"
    }

    fun removed(sbn: StatusBarNotification) {
        if (lastSent.remove(sbn.key) != null) {
            host?.send(JSONObject().put("type", "notification_removed").put("key", sbn.key))
        }
    }

    fun listenerUp(up: Boolean) {
        host?.let { status.value = if (up) "Forwarding notifications" else "Needs notification access" }
    }

    // --- helpers ---------------------------------------------------------------------------------------

    private fun replyAction(n: Notification): Notification.Action? =
        n.actions?.firstOrNull { a -> a.remoteInputs?.any { it.allowFreeFormInput } == true }

    private fun reply(ctx: Context, sbn: StatusBarNotification, text: String): Boolean {
        val action = replyAction(sbn.notification) ?: return false
        val inputs = action.remoteInputs ?: return false
        val results = Bundle().apply { inputs.forEach { putCharSequence(it.resultKey, text) } }
        val fill = Intent().addFlags(Intent.FLAG_RECEIVER_FOREGROUND)
        RemoteInput.addResultsToIntent(inputs, fill, results)
        return runCatching { action.actionIntent.send(ctx, 0, fill); true }
            .onFailure { Log.w("SwitcherNotif", "reply failed", it) }.getOrDefault(false)
    }

    /** Apps the PC can switch: everything with a launcher entry, plus any app that has notified. */
    private fun sendAppList(host: FeatureHost) {
        val ctx = host.context
        val pm = ctx.packageManager
        Thread({
            val apps = JSONArray()
            for (pkg in (launchable(ctx) + seenApps).sorted()) {
                if (pkg == ctx.packageName) continue
                val label = runCatching { pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString() }.getOrNull()
                    ?: continue
                val entry = JSONObject().put("package", pkg).put("label", label)
                runCatching { png(pm.getApplicationIcon(pkg), 48) }.getOrNull()?.let { entry.put("icon", it) }
                apps.put(entry)
            }
            host.send(JSONObject().put("type", "notification_apps").put("apps", apps))
        }, "app-list").start()
    }

    private fun launchable(ctx: Context): Set<String> =
        ctx.packageManager.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
            .map { it.activityInfo.packageName }.toSet()

    private fun appIcon(ctx: Context, pkg: String): String? =
        runCatching { png(ctx.packageManager.getApplicationIcon(pkg), 96) }.getOrNull()

    /** The notification's own picture (contact photo, image preview), if it has one. */
    private fun largeImage(ctx: Context, n: Notification): String? =
        runCatching { n.getLargeIcon()?.loadDrawable(ctx)?.let { png(it, 128) } }.getOrNull()

    /** Chat apps (MessagingStyle): the last few messages as [{sender, text}]. */
    private fun messages(extras: Bundle): JSONArray {
        val out = JSONArray()
        val raw = extras.getParcelableArray(Notification.EXTRA_MESSAGES) ?: return out
        for (m in raw.takeLast(4)) {
            val b = m as? Bundle ?: continue
            val text = b.getCharSequence("text")?.toString() ?: continue
            val sender = b.getCharSequence("sender")?.toString()
                ?: (b.getParcelable("sender_person") as? android.app.Person)?.name?.toString()
            out.put(JSONObject().put("sender", sender ?: "").put("text", text))
        }
        return out
    }

    private fun png(d: Drawable, size: Int): String {
        val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        d.setBounds(0, 0, size, size)
        d.draw(Canvas(bmp))
        val out = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    companion object {
        @Volatile var active: NotificationsFeature? = null
    }
}
