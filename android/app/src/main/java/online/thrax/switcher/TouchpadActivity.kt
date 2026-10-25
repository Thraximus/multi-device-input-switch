package online.thrax.switcher

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import online.thrax.switcher.core.App
import online.thrax.switcher.features.Features
import online.thrax.switcher.theme.Skin
import online.thrax.switcher.theme.SwitcherTheme
import online.thrax.switcher.ui.KeySink
import online.thrax.switcher.ui.KeyboardState
import online.thrax.switcher.ui.PadInput
import online.thrax.switcher.ui.TouchpadScreen

/** The touchpad and keyboard, full screen and kept awake; what they do goes to [Features.touchpad]. */
class TouchpadActivity : ComponentActivity() {
    private val pad = Features.touchpad

    private val input = object : PadInput {
        override fun move(dx: Float, dy: Float) = pad.move(dx, dy)
        override fun scroll(dx: Float, dy: Float) = pad.scroll(dx, dy)
        override fun tap(fingers: Int) = pad.click(when (fingers) { 1 -> 1; 2 -> 2; else -> 4 })
        override fun button(bit: Int, down: Boolean) = pad.press(bit, down)
    }

    private val keys = object : KeySink {
        override fun text(text: String) = pad.type(text)
        override fun key(name: String) = pad.key(name)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent {
            val state by App.state.collectAsState()
            val target by pad.target.collectAsState()
            val problem by pad.problem.collectAsState()
            val speed by pad.speed.collectAsState()
            val running by pad.running.collectAsState()
            val mods by pad.mods.collectAsState()
            val skipped by pad.skipped.collectAsState()
            val holder = state.holder
            val machines = holder?.machines.orEmpty().filter { it.connected && it.id != holder?.selfTarget }
            SwitcherTheme(Skin.from(state.skin)) {
                TouchpadScreen(
                    machines = machines,
                    target = target,
                    problem = when {
                        state.stopped -> "Disconnected — connect again on the app's main screen"
                        !state.connected -> "Not connected to the hub"
                        !running -> "The touchpad is turned off for this device on the PC"
                        machines.none { it.id == target } -> "Pick a machine above"
                        else -> problem
                    },
                    speed = speed,
                    onPick = pad::choose,
                    onSpeed = pad::setSpeed,
                    onClose = ::finish,
                    input = input,
                    keyboard = KeyboardState(keys, mods, pad::toggleMod, skipped),
                )
            }
        }
    }

    override fun onPause() {
        pad.release()  // never leave a button held on the other machine
        super.onPause()
    }
}
