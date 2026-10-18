package online.thrax.switcher.features

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.RemoteInput
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.drawable.Icon
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.text.Html
import android.util.Base64
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Devices
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import kotlinx.coroutines.flow.MutableStateFlow
import online.thrax.switcher.R
import online.thrax.switcher.ui.Label
import org.json.JSONObject

/**
 * Notifications from the user's other devices (sent by the hub), shown here as this app's notifications,
 * with "Dismiss on <device>" and inline Reply that act on the device they came from. These are posted by
 * this app, which the notifications feature never forwards — so mirrors can't loop.
 */
class NotificationMirrorFeature : Feature {
    override val id = "notification_mirror"
    override val title = "Other devices' notifications"
    override val summary = "Show notifications from your other devices here"
    override val icon = Icons.Outlined.Devices
    override val messageTypes = setOf("mirror_notification", "mirror_notification_removed")
    override val status = MutableStateFlow("off")
    private val lastEvent = MutableStateFlow("")

    private var host: FeatureHost? = null
    private val posted = mutableMapOf<String, Int>()  // mirror id -> Android notification id

    override fun permissions(ctx: Context): List<Need> = listOf(
        Need(
            "Show notifications",
            ctx.getSystemService(NotificationManager::class.java).areNotificationsEnabled(),
            Fix.OpenSettings("Open settings") { c ->
                Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, c.packageName)
            },
        ),
    )

    override fun start(host: FeatureHost) {
        this.host = host
        active = this
        host.context.getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Other devices' notifications", NotificationManager.IMPORTANCE_HIGH),
        )
        status.value = "Showing notifications from your other devices"
    }

    override fun stop() {
        host?.let { h ->
            val nm = h.context.getSystemService(NotificationManager::class.java)
            posted.values.forEach { nm.cancel(TAG, it) }
        }
        posted.clear()
        active = null
        host = null
        status.value = "off"
    }

    @Composable
    override fun Details() {
        val event by lastEvent.collectAsState()
        if (event.isNotEmpty()) Label("Last: $event")
    }

    override fun onMessage(msg: JSONObject) {
        val h = host ?: return
        val nm = h.context.getSystemService(NotificationManager::class.java)
        val mid = msg.optString("id")
        if (msg.optString("type") == "mirror_notification_removed") {
            posted.remove(mid)?.let { nm.cancel(TAG, it) }
            return
        }
        val nid = posted.getOrPut(mid) { mid.hashCode() }
        nm.notify(TAG, nid, build(h.context, msg, mid, nid))
        lastEvent.value = "${msg.optString("app")} from ${msg.optString("device")}"
    }

    private fun build(ctx: Context, msg: JSONObject, mid: String, nid: Int): Notification {
        val device = msg.optString("device", "another device")
        val app = msg.optString("app")
        val b = Notification.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_switcher)
            .setContentTitle(msg.optString("title").ifEmpty { app })
            .setSubText("$app · $device")
            .setAutoCancel(true)
            .setGroup(device)
        if (msg.has("time")) b.setWhen(msg.optLong("time")).setShowWhen(true)
        val messages = msg.optJSONArray("messages")
        if (messages != null && messages.length() > 0) {
            val lines = (0 until messages.length()).map { i ->
                val m = messages.getJSONObject(i)
                val sender = m.optString("sender")
                if (sender.isEmpty()) Html.escapeHtml(m.optString("text")) else "<b>${Html.escapeHtml(sender)}</b>: ${Html.escapeHtml(m.optString("text"))}"
            }
            val text = Html.fromHtml(lines.joinToString("<br>"), Html.FROM_HTML_MODE_LEGACY)
            b.setContentText(text).setStyle(Notification.BigTextStyle().bigText(text))
        } else {
            val text = msg.optString("text")
            b.setContentText(text).setStyle(Notification.BigTextStyle().bigText(text))
        }
        (bitmap(msg.optString("image")) ?: bitmap(msg.optString("icon")))?.let { b.setLargeIcon(it) }

        val dismiss = Intent(ctx, MirrorActionReceiver::class.java).setAction(ACTION_DISMISS)
            .setData(Uri.parse("switcher-mirror://$mid")).putExtra(EXTRA_ID, mid).putExtra(EXTRA_NID, nid)
        b.addAction(Notification.Action.Builder(
            Icon.createWithResource(ctx, R.drawable.ic_switcher), "Dismiss on $device",
            PendingIntent.getBroadcast(ctx, nid, dismiss, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT),
        ).build())
        if (msg.optBoolean("reply")) {
            val reply = Intent(ctx, MirrorActionReceiver::class.java).setAction(ACTION_REPLY)
                .setData(Uri.parse("switcher-mirror://$mid")).putExtra(EXTRA_ID, mid).putExtra(EXTRA_NID, nid)
            // Reply needs a mutable intent: Android fills in the typed text.
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            b.addAction(Notification.Action.Builder(
                Icon.createWithResource(ctx, R.drawable.ic_switcher), "Reply",
                PendingIntent.getBroadcast(ctx, nid + 1, reply, flags),
            ).addRemoteInput(RemoteInput.Builder(KEY_TEXT).setLabel("Reply on $device").build()).build())
        }
        return b.build()
    }

    private fun bitmap(b64: String) = if (b64.isEmpty()) null else runCatching {
        val bytes = Base64.decode(b64, Base64.DEFAULT)
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
    }.getOrNull()

    /** From the notification's buttons. */
    fun action(ctx: Context, intent: Intent) {
        val h = host ?: return
        val mid = intent.getStringExtra(EXTRA_ID) ?: return
        val out = JSONObject().put("type", "mirror_action").put("id", mid)
        when (intent.action) {
            ACTION_DISMISS -> out.put("action", "dismiss")
            ACTION_REPLY -> {
                val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(KEY_TEXT)?.toString() ?: return
                out.put("action", "reply").put("text", text)
            }
            else -> return
        }
        h.send(out)
        // Take it down here too (a replied-to notification would otherwise keep its spinner).
        ctx.getSystemService(NotificationManager::class.java).cancel(TAG, intent.getIntExtra(EXTRA_NID, 0))
        posted.remove(mid)
        lastEvent.value = if (intent.action == ACTION_REPLY) "Replied" else "Dismissed on the other device"
    }

    companion object {
        const val CHANNEL = "mirror"
        const val TAG = "mirror"
        const val ACTION_DISMISS = "online.thrax.switcher.MIRROR_DISMISS"
        const val ACTION_REPLY = "online.thrax.switcher.MIRROR_REPLY"
        const val EXTRA_ID = "id"
        const val EXTRA_NID = "nid"
        const val KEY_TEXT = "text"
        @Volatile var active: NotificationMirrorFeature? = null
    }
}

/** The buttons on mirrored notifications. */
class MirrorActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        NotificationMirrorFeature.active?.action(ctx, intent)
    }
}
