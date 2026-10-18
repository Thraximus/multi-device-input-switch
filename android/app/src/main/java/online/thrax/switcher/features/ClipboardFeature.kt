package online.thrax.switcher.features

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import android.util.Base64
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ContentPaste
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import kotlinx.coroutines.flow.MutableStateFlow
import online.thrax.switcher.ClipboardBridge
import online.thrax.switcher.ui.Label
import org.json.JSONObject

/**
 * Clipboard sync with the hub (and through it every other machine): text and images both ways.
 * Reading copies made on the device needs the log + overlay technique in [ClipboardBridge].
 */
class ClipboardFeature : Feature, ClipboardBridge.Sink {
    override val id = "clipboard"
    override val title = "Clipboard"
    override val summary = "Copy here, paste on the PC or laptop — and the other way round"
    override val icon = Icons.Outlined.ContentPaste
    override val defaultEnabled = true
    override val messageTypes = setOf("clipboard")
    override val status = MutableStateFlow("off")

    /** Whether log access works (Android 13+ asks, and refuses apps in the background). */
    val logAccess = MutableStateFlow(true)
    val lastEvent = MutableStateFlow("")

    private var host: FeatureHost? = null
    private var bridge: ClipboardBridge? = null
    @Volatile private var pending: JSONObject? = null  // newest copy made while the hub was unreachable

    override fun start(host: FeatureHost) {
        this.host = host
        bridge = ClipboardBridge(host.context, this)
        status.value = "starting"
    }

    override fun stop() {
        bridge?.stop()
        bridge = null
        host = null
        pending = null
        status.value = "off"
    }

    override fun permissions(ctx: Context): List<Need> {
        val needs = mutableListOf(
            Need(
                "See that you copied something (log access)",
                ctx.checkSelfPermission(Manifest.permission.READ_LOGS) == PackageManager.PERMISSION_GRANTED &&
                    logAccess.value,
                if (ctx.checkSelfPermission(Manifest.permission.READ_LOGS) != PackageManager.PERMISSION_GRANTED) {
                    Fix.ByHub
                } else {
                    Fix.Retry("Allow") { bridge?.restartLogWatch() }
                },
            ),
            Need(
                "Read what you copied (display over other apps)",
                Settings.canDrawOverlays(ctx),
                Fix.OpenSettings("Open settings") { c ->
                    Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${c.packageName}"))
                },
            ),
        )
        return needs
    }

    /** Called while the app is on screen: Android 13+ only asks for log access then. */
    fun retryLogAccess() {
        val b = bridge ?: return
        if (!b.hasLogAccess()) b.restartLogWatch()
    }

    @Composable
    override fun Details() {
        val event by lastEvent.collectAsState()
        if (event.isNotEmpty()) Label("Last: $event")
    }

    override fun onMessage(msg: JSONObject) {
        val mime = msg.optString("mime")
        val data = Base64.decode(msg.optString("data"), Base64.DEFAULT)
        bridge?.apply(mime, data, msg.optBoolean("secret"))
        lastEvent.value = "Received ${describe(mime, data)}"
    }

    override fun onConnected() {
        val queued = pending ?: return
        if (host?.send(queued) == true) {
            pending = null
            lastEvent.value = "Sent the copy made while offline"
        }
    }

    // --- ClipboardBridge.Sink --------------------------------------------------------------------------

    override fun copied(mime: String, data: ByteArray, secret: Boolean) {
        val out = JSONObject().put("type", "clipboard").put("mime", mime)
            .put("data", Base64.encodeToString(data, Base64.NO_WRAP))
        if (secret) out.put("secret", true)
        if (host?.send(out) == true) {
            lastEvent.value = "Sent ${describe(mime, data)}"
        } else {
            pending = out
            lastEvent.value = "Copied ${describe(mime, data)}; sends when the hub is reachable"
        }
    }

    override fun status(what: String) {
        status.value = what
    }

    override fun logAccess(ok: Boolean) {
        logAccess.value = ok
        host?.let { Attention.logAccess(it.context, ok) }
    }

    private fun describe(mime: String, data: ByteArray): String {
        val kind = if (mime.startsWith("image/")) "image" else "text"
        val size = if (data.size < 1024) "${data.size} B" else "${data.size / 1024} KB"
        return "$kind ($size)"
    }
}
