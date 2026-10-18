package online.thrax.switcher.features

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.vector.ImageVector
import kotlinx.coroutines.flow.StateFlow
import org.json.JSONObject

/**
 * One self-contained thing the app does for the hub: clipboard sync now; notifications, calls/SMS, files
 * and touchpad later. Adding a feature means writing one of these and listing it in [Features] —
 * the service, the hub protocol, the PC's per-device switches and this app's home screen pick it up.
 *
 * Whether a feature runs is decided on the PC (settings live on the host): the hub sends a config with
 * the enabled set, the service starts/stops features to match and tells the hub what it now supports.
 */
interface Feature {
    /** Stable id, used in the hub config and protocol ("clipboard", "notifications", …). */
    val id: String
    val title: String
    /** One line: what it does for you. */
    val summary: String
    val icon: ImageVector
    /** On unless the hub's config says otherwise (only for features that are harmless to run). */
    val defaultEnabled: Boolean get() = false
    /** Capability announced to the hub while running (the hub routes messages by it). */
    val capability: String get() = id
    /** Whether this device can do it at all (e.g. calls need telephony); unavailable features aren't offered. */
    fun available(ctx: Context): Boolean = true
    /** Hub → device message types this feature handles. */
    val messageTypes: Set<String> get() = emptySet()

    /** What it needs from Android, shown on its card with a way to fix each. */
    fun permissions(ctx: Context): List<Need> = emptyList()

    /** Short live status for the card ("watching for copies", "3 notifications sent", …). */
    val status: StateFlow<String>

    fun start(host: FeatureHost)
    fun stop()
    /** This feature's settings from the hub config (`settings.<id>`; empty when none were set on the PC). */
    fun onSettings(settings: JSONObject) {}
    fun onMessage(msg: JSONObject) {}
    /** The hub connection came up (e.g. send what was queued while offline). */
    fun onConnected() {}

    /** Extra controls on the feature's card, below its status. */
    @Composable
    fun Details() {}
}

/** What a running feature can use from the service. */
interface FeatureHost {
    val context: Context
    val connected: Boolean
    /** Send to the hub; false if not connected right now. */
    fun send(msg: JSONObject): Boolean
}

/** A requirement on Android's side and how the user (or the hub) satisfies it. */
data class Need(
    val label: String,
    val granted: Boolean,
    val fix: Fix,
)

sealed interface Fix {
    /** Only the hub can grant it over USB (reinstall from the PC). */
    data object ByHub : Fix

    /** Open a system screen where the user grants it. */
    data class OpenSettings(val action: String, val intent: (Context) -> Intent) : Fix

    /** Ordinary runtime permissions the app can ask for itself (Android's own prompt). */
    data class Request(val action: String, val permissions: List<String>) : Fix

    /** Something the feature can retry itself while the app is on screen. */
    data class Retry(val action: String, val run: () -> Unit) : Fix
}
