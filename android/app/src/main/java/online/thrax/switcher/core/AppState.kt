package online.thrax.switcher.core

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONObject

/** Which machine holds the keyboard/mouse, as the hub reports it. */
data class Holder(
    val name: String,
    /** True when the devices are on this phone/tablet. */
    val here: Boolean,
    /** This device's target id at the hub (for "use the keyboard here"). */
    val selfTarget: String?,
    val pending: Boolean,
)

/** Everything the home screen shows, published by the service. */
data class AppState(
    val paired: Boolean = false,
    val host: String = "",
    val agentId: String = "",
    val connected: Boolean = false,
    val connection: String = "not started",
    val holder: Holder? = null,
    val skin: String = "material",
    val enabled: Map<String, Boolean> = emptyMap(),
    /** Self-update progress ("Downloading update 0.6.0…"), empty when there's nothing to say. */
    val update: String = "",
)

object App {
    val state = MutableStateFlow(AppState())
}

/**
 * The look and enabled features the hub last sent, kept on the device so features start (and the app
 * looks right) before the hub is reachable.
 */
class DeviceConfig(ctx: Context) {
    private val prefs = ctx.getSharedPreferences("device-config", Context.MODE_PRIVATE)

    var skin: String
        get() = prefs.getString("skin", "material") ?: "material"
        set(v) = prefs.edit().putString("skin", v).apply()

    /** Per-feature settings from the hub, as JSON ({feature id: {...}}). */
    var settings: JSONObject
        get() = runCatching { JSONObject(prefs.getString("settings", "{}") ?: "{}") }.getOrDefault(JSONObject())
        set(v) = prefs.edit().putString("settings", v.toString()).apply()

    var enabled: Map<String, Boolean>
        get() = prefs.getString("features", null)?.let { raw ->
            runCatching {
                val o = JSONObject(raw)
                o.keys().asSequence().associateWith { o.getBoolean(it) }
            }.getOrNull()
        } ?: emptyMap()
        set(v) = prefs.edit().putString("features", JSONObject(v as Map<*, *>).toString()).apply()
}
