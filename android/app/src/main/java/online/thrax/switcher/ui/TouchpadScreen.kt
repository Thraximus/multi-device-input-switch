package online.thrax.switcher.ui

import android.view.HapticFeedbackConstants
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp
import online.thrax.switcher.core.Machine
import online.thrax.switcher.theme.LocalTokens
import online.thrax.switcher.theme.isCogitator
import kotlin.math.hypot
import kotlin.math.max

/** What the pad reports; distances in dp. */
interface PadInput {
    fun move(dx: Float, dy: Float)
    fun scroll(dx: Float, dy: Float)
    /** A tap with this many fingers: 1 left click, 2 right click, 3 middle click. */
    fun tap(fingers: Int)
    /** A mouse button held or let go (1 left, 2 right). */
    fun button(bit: Int, down: Boolean)
}

/** Keyboard mode: where typing goes, and the modifiers latched on the extra-keys row. */
class KeyboardState(
    val sink: KeySink,
    val mods: Set<String>,
    val onMod: (String) -> Unit,
    /** Characters the machine's layout couldn't type (shown under the keys); empty when all went through. */
    val skipped: String,
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TouchpadScreen(
    machines: List<Machine>,
    target: String?,
    /** Shown over the pad when it can't work right now (offline, feature off, machine unavailable). */
    problem: String,
    speed: Float,
    onPick: (String?) -> Unit,
    onSpeed: (Float) -> Unit,
    onClose: () -> Unit,
    input: PadInput,
    keyboard: KeyboardState,
) {
    val tk = LocalTokens.current
    val sink by rememberUpdatedState(keyboard.sink)
    val typing = WindowInsets.isImeVisible
    var catcher by remember { mutableStateOf<KeyCatcher?>(null) }
    Column(
        Modifier.fillMaxSize().background(tk.ground).statusBarsPadding().navigationBarsPadding().imePadding()
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClose) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back", tint = tk.text) }
            Heading("Touchpad", size = 22, modifier = Modifier.weight(1f))
            // Takes the on-screen keyboard's input; invisible, it only needs focus.
            AndroidView({ ctx ->
                KeyCatcher(ctx, object : KeySink {
                    override fun text(text: String) = sink.text(text)
                    override fun key(name: String) = sink.key(name)
                }).also { catcher = it }
            }, Modifier.size(1.dp))
            Btn(if (typing) "Hide keys" else "Keyboard", {
                if (typing) catcher?.hideKeyboard() else catcher?.showKeyboard()
            }, style = if (typing) BtnStyle.Primary else BtnStyle.Quiet, icon = Icons.Outlined.Keyboard)
        }
        if (machines.isEmpty()) {
            Label("No other machine is connected")
        } else {
            MachinePicker(machines, { it.id == target }, { onPick(it.id) })
        }
        Pad(Modifier.weight(1f).fillMaxWidth(), problem, input)
        Row(Modifier.fillMaxWidth().height(if (typing) 56.dp else 76.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            PadButton("Left", 1, Modifier.weight(1f), input)
            PadButton("Right", 2, Modifier.weight(1f), input)
        }
        if (typing) {
            ExtraKeys(keyboard)
            if (keyboard.skipped.isNotEmpty()) {
                Label("Can't type \"${keyboard.skipped}\" with this machine's keyboard layout", color = tk.warn)
            }
        } else Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Label("Speed")
            Slider(speed, onSpeed, Modifier.weight(1f), valueRange = .4f..2.5f,
                colors = SliderDefaults.colors(thumbColor = tk.accent, activeTrackColor = tk.accent,
                    inactiveTrackColor = tk.border))
        }
    }
}

@Composable
private fun padShape(): Shape = if (isCogitator) cutCorners(12.dp) else RoundedCornerShape(28.dp)

@Composable
private fun Pad(modifier: Modifier, problem: String, input: PadInput) {
    val tk = LocalTokens.current
    val view = LocalView.current
    val current by rememberUpdatedState(input)
    val shape = padShape()
    Box(
        modifier.clip(shape).background(tk.surface).border(1.dp, if (isCogitator) tk.brassDim else tk.border, shape)
            .pointerInput(Unit) {
                awaitEachGesture {
                    val first = awaitFirstDown(requireUnconsumed = false)
                    var fingers = 1
                    var travel = 0f
                    var last = first.uptimeMillis
                    do {
                        val event = awaitPointerEvent()
                        val down = event.changes.filter { it.pressed }
                        fingers = max(fingers, down.size)
                        val moving = down.filter { it.previousPressed }
                        if (moving.isNotEmpty()) {
                            val dx = moving.map { it.position.x - it.previousPosition.x }.average().toFloat() / density
                            val dy = moving.map { it.position.y - it.previousPosition.y }.average().toFloat() / density
                            travel += hypot(dx, dy)
                            // Once a second finger joined, the gesture is a scroll to its end: lifting one
                            // finger must not turn the rest into a jump of the pointer.
                            if (fingers >= 2) current.scroll(dx, dy) else current.move(dx, dy)
                        }
                        event.changes.forEach {
                            last = max(last, it.uptimeMillis)
                            it.consume()
                        }
                    } while (event.changes.any { it.pressed })
                    if (last - first.uptimeMillis < TAP_MS && travel < TAP_SLOP_DP) {
                        current.tap(fingers.coerceAtMost(3))
                        view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                    }
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        if (problem.isNotEmpty()) {
            Body(problem, Modifier.padding(24.dp), color = tk.warn)
        } else {
            Label("Move with one finger · tap to click\nTwo fingers: scroll · tap to right-click",
                Modifier.padding(24.dp), color = tk.muted)
        }
    }
}

@Composable
private fun PadButton(text: String, bit: Int, modifier: Modifier, input: PadInput) {
    val tk = LocalTokens.current
    val view = LocalView.current
    val current by rememberUpdatedState(input)
    var held by remember { mutableStateOf(false) }
    val shape = padShape()
    Box(
        modifier.fillMaxSize().clip(shape)
            .background(if (held) tk.accent.copy(alpha = .35f) else tk.surface2)
            .border(1.dp, if (held) tk.accent else if (isCogitator) tk.brassDim else tk.border, shape)
            .pointerInput(bit) {
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false).consume()
                    held = true
                    current.button(bit, true)
                    view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                    do {
                        val event = awaitPointerEvent()
                        event.changes.forEach { it.consume() }
                    } while (event.changes.any { it.pressed })
                    held = false
                    current.button(bit, false)
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Label(text, color = if (held) tk.text else tk.text2, maxLines = 1)
    }
}

/** The keys a phone keyboard lacks: modifiers latch for the next key, the rest are pressed at once. */
@Composable
private fun ExtraKeys(keyboard: KeyboardState) {
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        KeyChip("Esc", false) { keyboard.sink.key("esc") }
        KeyChip("Tab", false) { keyboard.sink.key("tab") }
        for ((mod, label) in listOf("ctrl" to "Ctrl", "alt" to "Alt", "shift" to "Shift", "meta" to "Win")) {
            KeyChip(label, mod in keyboard.mods) { keyboard.onMod(mod) }
        }
        for ((key, label) in listOf("left" to "←", "up" to "↑", "down" to "↓", "right" to "→", "home" to "Home",
            "end" to "End", "pageup" to "PgUp", "pagedown" to "PgDn", "delete" to "Del")) {
            KeyChip(label, false) { keyboard.sink.key(key) }
        }
        for (n in 1..12) KeyChip("F$n", false) { keyboard.sink.key("f$n") }
    }
}

@Composable
private fun KeyChip(text: String, latched: Boolean, onClick: () -> Unit) {
    val tk = LocalTokens.current
    val view = LocalView.current
    val shape = if (isCogitator) cutCorners(5.dp) else RoundedCornerShape(10.dp)
    Box(
        Modifier.height(44.dp).widthIn(min = 48.dp).clip(shape)
            .background(if (latched) tk.accent.copy(alpha = .35f) else tk.surface2)
            .border(1.dp, if (latched) tk.accent else if (isCogitator) tk.brassDim else tk.border, shape)
            .clickable {
                view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                onClick()
            }
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        Label(text, color = if (latched) tk.text else tk.text2, maxLines = 1)
    }
}

private const val TAP_MS = 220L
private const val TAP_SLOP_DP = 8f
