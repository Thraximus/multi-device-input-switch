package online.thrax.switcher.features

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.TouchApp
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.flow.MutableStateFlow
import online.thrax.switcher.TouchpadActivity
import online.thrax.switcher.ui.Btn
import online.thrax.switcher.ui.BtnStyle
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.min

/**
 * This screen as a touchpad and keyboard for another machine: the PC, the laptop, or another phone/tablet.
 * The pad is [TouchpadActivity]; this turns what it does into pointer and key messages, and the hub moves
 * that machine's mouse and presses its keys (hub/touchpad.py). Buttons travel as the full state, so a lost
 * message can't leave one held.
 */
class TouchpadFeature : Feature {
    override val id = "touchpad"
    override val title = "Touchpad & keyboard"
    override val summary = "Use this screen as a touchpad and keyboard for the PC or the laptop"
    override val icon = Icons.Outlined.TouchApp
    override val defaultEnabled = true  // does nothing until the pad is opened
    override val messageTypes = setOf("touchpad", "touchpad_skipped")
    override val status = MutableStateFlow("off")

    /** The machine the pad drives: a target id, or null for the hub's own PC. */
    val target = MutableStateFlow<String?>(null)
    /** Why the hub can't drive that machine right now; empty when it can. */
    val problem = MutableStateFlow("")
    /** Pointer speed multiplier (the pad's slider). */
    val speed = MutableStateFlow(1f)
    val running = MutableStateFlow(false)
    /** Modifiers latched on the extra-keys row; they apply to the next key, then let go. */
    val mods = MutableStateFlow(emptySet<String>())
    /** Characters the machine's keyboard layout couldn't type, last time it happened. */
    val skipped = MutableStateFlow("")

    private var host: FeatureHost? = null
    private var prefs: SharedPreferences? = null
    private var buttons = 0
    private var restX = 0f
    private var restY = 0f
    private var scrollX = 0f
    private var scrollY = 0f

    override fun start(host: FeatureHost) {
        this.host = host
        val p = host.context.getSharedPreferences("touchpad", Context.MODE_PRIVATE)
        prefs = p
        target.value = p.getString("target", "")!!.ifEmpty { null }
        speed.value = p.getFloat("speed", 1f)
        running.value = true
        status.value = "ready"
    }

    override fun stop() {
        release()
        host = null
        running.value = false
        status.value = "off"
    }

    override fun onMessage(msg: JSONObject) {
        if (msg.optString("type") == "touchpad_skipped") {
            skipped.value = msg.optString("text")
            return
        }
        val about = if (msg.isNull("target")) null else msg.optString("target")
        if (about != target.value) return
        problem.value = if (msg.optBoolean("ok")) "" else msg.optString("reason")
        status.value = if (msg.optBoolean("ok")) "ready" else problem.value
    }

    fun choose(machine: String?) {
        release()
        target.value = machine
        problem.value = ""
        prefs?.edit()?.putString("target", machine ?: "")?.apply()
    }

    fun setSpeed(value: Float) {
        speed.value = value
        prefs?.edit()?.putFloat("speed", value)?.apply()
    }

    /** One finger moved (dp); slow movements stay precise, fast ones cover the screen. */
    fun move(dxDp: Float, dyDp: Float) {
        val step = abs(dxDp) + abs(dyDp)
        val gain = BASE_GAIN * speed.value * (1 + min(step, 40f) * ACCELERATION)
        restX += dxDp * gain
        restY += dyDp * gain
        val dx = restX.toInt()
        val dy = restY.toInt()
        restX -= dx
        restY -= dy
        if (dx != 0 || dy != 0) send(dx, dy)
    }

    /** Two fingers moved (dp): natural scrolling, like scrolling a page on the phone. */
    fun scroll(dxDp: Float, dyDp: Float) {
        scrollX += dxDp
        scrollY += dyDp
        val wheel = (scrollY / SCROLL_STEP_DP).toInt()
        val pan = (-scrollX / SCROLL_STEP_DP).toInt()
        scrollY -= wheel * SCROLL_STEP_DP
        scrollX += pan * SCROLL_STEP_DP
        if (wheel != 0 || pan != 0) send(wheel = wheel, pan = pan)
    }

    /** A button held (true) or let go; [bit] 1 left, 2 right, 4 middle. */
    fun press(bit: Int, down: Boolean) {
        val next = if (down) buttons or bit else buttons and bit.inv()
        if (next != buttons) {
            buttons = next
            send()
        }
    }

    fun click(bit: Int) {
        press(bit, true)
        press(bit, false)
    }

    /** Text from the on-screen keyboard; with a modifier latched, each character is a shortcut instead. */
    fun type(text: String) {
        if (text.isEmpty()) return
        skipped.value = ""
        if (mods.value.isEmpty()) {
            host?.send(keys().put("text", text))
        } else {
            text.forEach { key(it.toString()) }
        }
    }

    /** One key ("enter", "backspace", "left", "f5", or a character), with the latched modifiers. */
    fun key(name: String) {
        host?.send(keys().put("key", name).put("mods", org.json.JSONArray(mods.value.toList())))
        mods.value = emptySet()
    }

    fun toggleMod(mod: String) {
        mods.value = if (mod in mods.value) mods.value - mod else mods.value + mod
    }

    /** Let go of everything (the pad left the screen, or the machine changes). */
    fun release() {
        buttons = 0
        restX = 0f; restY = 0f; scrollX = 0f; scrollY = 0f
        mods.value = emptySet()
        host?.send(message().put("release", true))
    }

    private fun send(dx: Int = 0, dy: Int = 0, wheel: Int = 0, pan: Int = 0) {
        host?.send(message().put("dx", dx).put("dy", dy).put("wheel", wheel).put("pan", pan).put("buttons", buttons))
    }

    private fun keys() = JSONObject().put("type", "keys").put("target", target.value ?: JSONObject.NULL)

    private fun message() = JSONObject().put("type", "pointer").put("target", target.value ?: JSONObject.NULL)

    @Composable
    override fun Details() {
        val ctx = LocalContext.current
        Btn("Open touchpad", { ctx.startActivity(Intent(ctx, TouchpadActivity::class.java)) },
            style = BtnStyle.Primary, icon = icon)
    }

    companion object {
        private const val BASE_GAIN = 1.4f        // counts per dp at the slowest
        private const val ACCELERATION = 0.06f    // extra gain per dp moved in one event
        private const val SCROLL_STEP_DP = 18f    // finger travel per wheel notch
    }
}
