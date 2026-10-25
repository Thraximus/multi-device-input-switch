package online.thrax.switcher

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ContentPaste
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import com.github.takahirom.roborazzi.captureRoboImage
import online.thrax.switcher.core.AppState
import online.thrax.switcher.core.Holder
import online.thrax.switcher.core.Machine
import online.thrax.switcher.features.Fix
import online.thrax.switcher.features.Need
import online.thrax.switcher.theme.Skin
import online.thrax.switcher.theme.SwitcherTheme
import online.thrax.switcher.ui.FeatureCard
import online.thrax.switcher.ui.HomeScreen
import online.thrax.switcher.ui.KeySink
import online.thrax.switcher.ui.KeyboardState
import online.thrax.switcher.ui.PadInput
import online.thrax.switcher.ui.TouchpadScreen
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Renders the home screen in both skins to PNG on the JVM (no device), for checking the look:
 *   ./gradlew :app:recordRoborazziDebug   → app/build/outputs/roborazzi/
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
class HomeScreenshotTest {
    @get:Rule
    val compose = createComposeRule()

    private val machines = listOf(
        Machine(null, "desktop", "linux", true), Machine("laptop", "Laptop", "pico", true),
        Machine("bt-7d7c", "Phone", "ble", true), Machine("bt-21aa", "Tablet", "ble", true),
    )

    private val state = AppState(
        paired = true, host = "192.168.1.10", agentId = "a1b2c3d4e5f60718", connected = true,
        connection = "Connected to 192.168.1.10",
        holder = Holder("Laptop", here = false, selfTarget = "bt-7d7c", pending = false, machines = machines, at = "laptop"),
        enabled = mapOf("clipboard" to true),
    )

    private val cards = listOf(
        FeatureCard("clipboard", "Clipboard", "Copy here, paste on the PC or laptop — and the other way round",
            Icons.Outlined.ContentPaste, enabled = true, status = "Watching for copies",
            needs = listOf(
                Need("See that you copied something (log access)", true, Fix.ByHub),
                Need("Read what you copied (display over other apps)", false, Fix.OpenSettings("Open settings") { throw IllegalStateException() }),
            )),
        FeatureCard("notifications", "Notifications", "See this phone's notifications on the PC",
            Icons.Outlined.Notifications, enabled = false, status = "off", needs = emptyList()),
    )

    private fun shot(skin: Skin, name: String, s: AppState = state) {
        compose.setContent {
            SwitcherTheme(skin) {
                HomeScreen(s, "Pixel 8", "0.7.1", cards, onSwitch = {}, onConnect = {}, onDisconnect = {}, onFix = {})
            }
        }
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test fun material() = shot(Skin.Material, "home-material")
    @Test fun cogitator() = shot(Skin.Cogitator, "home-cogitator")
    @Test fun cogitatorHere() = shot(Skin.Cogitator, "home-cogitator-here",
        state.copy(holder = Holder("Phone", here = true, selfTarget = "bt-7d7c", pending = false, machines = machines,
            at = "bt-7d7c")))
    @Test fun disconnected() = shot(Skin.Material, "home-material-disconnected",
        state.copy(stopped = true, connected = false, connection = "Disconnected", holder = null))

    private val pad = object : PadInput {
        override fun move(dx: Float, dy: Float) {}
        override fun scroll(dx: Float, dy: Float) {}
        override fun tap(fingers: Int) {}
        override fun button(bit: Int, down: Boolean) {}
    }

    private fun touchpad(skin: Skin, name: String) {
        compose.setContent {
            SwitcherTheme(skin) {
                TouchpadScreen(machines.filter { it.id != "bt-7d7c" }, "laptop", "", 1f, {}, {}, {}, pad,
                    KeyboardState(object : KeySink {
                        override fun text(text: String) {}
                        override fun key(name: String) {}
                    }, emptySet(), {}, ""))
            }
        }
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test fun touchpadMaterial() = touchpad(Skin.Material, "touchpad-material")
    @Test fun touchpadCogitator() = touchpad(Skin.Cogitator, "touchpad-cogitator")
    @Test fun offline() = shot(Skin.Material, "home-material-offline",
        state.copy(connected = false, connection = "Hub not reachable — retrying", holder = null))
}
