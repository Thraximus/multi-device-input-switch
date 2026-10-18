package online.thrax.switcher.features

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.ContactsContract
import android.service.notification.StatusBarNotification
import android.telecom.TelecomManager
import android.telephony.TelephonyManager
import android.util.Base64
import android.util.Log
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Call
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import kotlinx.coroutines.flow.MutableStateFlow
import online.thrax.switcher.ui.Label
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * Incoming calls on the PC — regular calls (phone state) and app calls (WhatsApp, LINE, …: their call
 * notifications, via the shared notification listener). Ringing → a critical (shown even over games / in Do Not Disturb) desktop
 * notification with the caller's name and photo, "Answer on <device>" and "Decline"; answered or ended →
 * it goes away. Separate from the notifications feature, so it doesn't depend on the app filter.
 *
 * Audio seam: answering from the PC answers on the phone; once audio routing exists (USB Bluetooth
 * dongle), answering there will also move the call audio to the PC and its mic back to the phone.
 *
 * Off = no receiver registered at all. Only offered on devices that can make calls.
 */
class CallsFeature : Feature {
    override val id = "calls"
    override val title = "Calls"
    override val summary = "Incoming calls pop up on the PC — even over games — answer or decline there"
    override val icon = Icons.Outlined.Call
    override val messageTypes = setOf("call_action")
    override val status = MutableStateFlow("off")
    private val lastEvent = MutableStateFlow("")

    private var host: FeatureHost? = null
    private var receiver: BroadcastReceiver? = null
    @Volatile private var appCall: String? = null      // notification key of the ringing/ongoing app call
    @Volatile private var appCallAnswered = false
    @Volatile private var state = TelephonyManager.EXTRA_STATE_IDLE
    @Volatile private var sentNumber: String? = null

    override fun available(ctx: Context) = ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_TELEPHONY)

    override fun permissions(ctx: Context): List<Need> = listOf(
        Manifest.permission.READ_PHONE_STATE to "See that a call is ringing",
        Manifest.permission.READ_CALL_LOG to "See the caller's number",
        Manifest.permission.READ_CONTACTS to "Show the caller's name and photo",
        Manifest.permission.ANSWER_PHONE_CALLS to "Answer or decline from the PC",
    ).map { (perm, label) ->
        Need(label, ctx.checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED, Fix.Request("Allow", listOf(perm)))
    } + Need(
        "Calls from apps like WhatsApp and LINE (notification access)",
        NotificationAccess.granted(ctx),
        Fix.OpenSettings("Open settings") { Intent(android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS) },
    )

    override fun start(host: FeatureHost) {
        this.host = host
        val r = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context, intent: Intent) = onPhoneState(ctx, intent)
        }
        host.context.registerReceiver(r, IntentFilter(TelephonyManager.ACTION_PHONE_STATE_CHANGED))
        receiver = r
        active = this
        NotificationAccess.use(host.context, id, true)
        status.value = "Watching for incoming calls"
    }

    override fun stop() {
        receiver?.let { r -> host?.context?.let { runCatching { it.unregisterReceiver(r) } } }
        host?.let { NotificationAccess.use(it.context, id, false) }
        receiver = null
        active = null
        appCall = null
        host = null
        state = TelephonyManager.EXTRA_STATE_IDLE
        sentNumber = null
        status.value = "off"
    }

    @Composable
    override fun Details() {
        val event by lastEvent.collectAsState()
        if (event.isNotEmpty()) Label("Last: $event")
    }

    @SuppressLint("MissingPermission")  // granted by the hub over ADB; failures are reported below
    override fun onMessage(msg: JSONObject) {
        val ctx = host?.context ?: return
        if (msg.optString("key").isNotEmpty()) return appCallAction(ctx, msg.optString("key"), msg.optString("action"))
        val telecom = ctx.getSystemService(TelecomManager::class.java)
        runCatching {
            when (msg.optString("action")) {
                "answer" -> telecom.acceptRingingCall()
                "decline" -> telecom.endCall()
            }
            lastEvent.value = if (msg.optString("action") == "answer") "Answered from the PC" else "Declined from the PC"
        }.onFailure {
            Log.w("SwitcherCalls", "call action failed", it)
            lastEvent.value = "Couldn't ${msg.optString("action")}: ${it.message}"
        }
    }

    private fun onPhoneState(ctx: Context, intent: Intent) {
        val h = host ?: return
        val now = intent.getStringExtra(TelephonyManager.EXTRA_STATE) ?: return
        @Suppress("DEPRECATION")
        val number = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER)
        val before = state
        state = now
        when (now) {
            TelephonyManager.EXTRA_STATE_RINGING -> {
                // Android sends RINGING twice (without, then with the number when READ_CALL_LOG is held).
                if (before == now && (number == null || number == sentNumber)) return
                sentNumber = number
                Thread({
                    val out = JSONObject().put("type", "call").put("state", "ringing").put("number", number ?: "")
                    contact(ctx, number)?.let { (name, photo) ->
                        out.put("name", name)
                        photo?.let { out.put("photo", it) }
                    }
                    h.send(out)
                    lastEvent.value = "Incoming call from ${out.optString("name").ifEmpty { number ?: "unknown" }}"
                }, "call").start()
            }
            TelephonyManager.EXTRA_STATE_OFFHOOK -> if (before == TelephonyManager.EXTRA_STATE_RINGING) {
                h.send(JSONObject().put("type", "call").put("state", "answered"))
            }
            TelephonyManager.EXTRA_STATE_IDLE -> if (before != TelephonyManager.EXTRA_STATE_IDLE) {
                h.send(JSONObject().put("type", "call").put("state", "ended"))
                sentNumber = null
            }
        }
    }

    // --- app calls (WhatsApp, LINE, …) from their call notifications ------------------------------------

    fun posted(l: NotificationListener, sbn: StatusBarNotification, replay: Boolean) {
        val h = host ?: return
        val n = sbn.notification
        if (n.category != Notification.CATEGORY_CALL) return
        // Regular calls come through the phone state (with the contact's name and photo); skip the dialer's own.
        val dialer = runCatching { l.getSystemService(TelecomManager::class.java).defaultDialerPackage }.getOrNull()
        if (sbn.packageName == dialer || sbn.packageName == "com.android.server.telecom") return
        val type = n.extras.getInt(EXTRA_CALL_TYPE, 0)  // CallStyle (Android 12+): 1 incoming, 2 ongoing
        val ringing = type == 1 || (type == 0 && n.fullScreenIntent != null)
        when {
            ringing && sbn.key != appCall && !replay -> {
                appCall = sbn.key
                appCallAnswered = false
                val pm = l.packageManager
                val app = runCatching { pm.getApplicationLabel(pm.getApplicationInfo(sbn.packageName, 0)).toString() }
                    .getOrDefault(sbn.packageName)
                val person = n.extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
                val out = JSONObject().put("type", "call").put("state", "ringing").put("key", sbn.key)
                    .put("app", app).put("name", person)
                runCatching { n.getLargeIcon()?.loadDrawable(l)?.let { png(it) } }.getOrNull()?.let { out.put("photo", it) }
                h.send(out)
                lastEvent.value = "Incoming $app call from ${person.ifEmpty { "someone" }}"
            }
            !ringing && sbn.key == appCall && !appCallAnswered -> {
                appCallAnswered = true
                h.send(JSONObject().put("type", "call").put("state", "answered"))
            }
        }
    }

    fun removed(sbn: StatusBarNotification) {
        if (sbn.key != appCall) return
        appCall = null
        host?.send(JSONObject().put("type", "call").put("state", "ended"))
    }

    /** Press the app's own Answer/Decline (CallStyle intents, else the actions by their labels). */
    private fun appCallAction(ctx: Context, key: String, action: String) {
        val sbn = runCatching { NotificationAccess.listener?.activeNotifications }.getOrNull()?.firstOrNull { it.key == key }
            ?: return
        val n = sbn.notification
        @Suppress("DEPRECATION")
        val intent = (if (action == "answer") n.extras.getParcelable<PendingIntent>(EXTRA_ANSWER_INTENT)
        else n.extras.getParcelable<PendingIntent>(EXTRA_DECLINE_INTENT) ?: n.extras.getParcelable(EXTRA_HANG_UP_INTENT))
            ?: n.actions?.firstOrNull { a ->
                val t = a.title?.toString()?.lowercase().orEmpty()
                if (action == "answer") ANSWER_WORDS.any { it in t } else DECLINE_WORDS.any { it in t }
            }?.actionIntent
        if (intent == null) {
            lastEvent.value = "This app's call can't be ${action}ed from the PC"
            return
        }
        runCatching { intent.send() }
            .onSuccess { lastEvent.value = if (action == "answer") "Answered from the PC" else "Declined from the PC" }
            .onFailure { lastEvent.value = "Couldn't $action: ${it.message}" }
    }

    private fun png(d: android.graphics.drawable.Drawable): String {
        val bmp = android.graphics.Bitmap.createBitmap(128, 128, android.graphics.Bitmap.Config.ARGB_8888)
        d.setBounds(0, 0, 128, 128)
        d.draw(android.graphics.Canvas(bmp))
        val out = ByteArrayOutputStream()
        bmp.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out)
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    /** (display name, photo PNG base64?) from the contacts, if the number is known. */
    private fun contact(ctx: Context, number: String?): Pair<String, String?>? {
        if (number.isNullOrEmpty()) return null
        val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
        return runCatching {
            ctx.contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME,
                ContactsContract.PhoneLookup.PHOTO_THUMBNAIL_URI), null, null, null)?.use { c ->
                if (!c.moveToFirst()) return@use null
                val name = c.getString(0) ?: return@use null
                val photo = c.getString(1)?.let { p ->
                    ctx.contentResolver.openInputStream(Uri.parse(p))?.use { input ->
                        val bmp = BitmapFactory.decodeStream(input) ?: return@use null
                        val out = ByteArrayOutputStream()
                        bmp.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out)
                        Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
                    }
                }
                name to photo
            }
        }.getOrNull()
    }

    companion object {
        @Volatile var active: CallsFeature? = null
        // Notification.CallStyle extras (Android 12+), by name so older SDK levels compile the same.
        private const val EXTRA_CALL_TYPE = "android.callType"
        private const val EXTRA_ANSWER_INTENT = "android.answerIntent"
        private const val EXTRA_DECLINE_INTENT = "android.declineIntent"
        private const val EXTRA_HANG_UP_INTENT = "android.hangUpIntent"
        private val ANSWER_WORDS = listOf("answer", "accept", "応答", "出る", "odgovor")
        private val DECLINE_WORDS = listOf("decline", "reject", "hang up", "end", "拒否", "切る", "odbij")
    }
}
