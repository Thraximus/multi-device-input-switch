package online.thrax.switcher

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import online.thrax.switcher.core.DeviceConfig
import online.thrax.switcher.theme.LocalTokens
import online.thrax.switcher.theme.Skin
import online.thrax.switcher.theme.SwitcherTheme
import online.thrax.switcher.ui.Body
import online.thrax.switcher.ui.Dot
import online.thrax.switcher.ui.Level
import online.thrax.switcher.ui.Panel
import online.thrax.switcher.ui.PanelHead

/** Started by the hub over ADB with the pairing details; pairs, starts the service, closes itself. */
class PairActivity : ComponentActivity() {
    private val result = mutableStateOf<Pair<Boolean, String>?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val host = intent.getStringExtra("host")
        val port = intent.getIntExtra("port", 0)
        val token = intent.getStringExtra("token")
        val fingerprint = intent.getStringExtra("fingerprint")
        setContent {
            SwitcherTheme(Skin.from(DeviceConfig(this).skin)) {
                Box(Modifier.fillMaxSize().background(LocalTokens.current.ground).padding(24.dp), contentAlignment = Alignment.Center) {
                    Panel(Modifier.widthIn(max = 480.dp)) {
                        PanelHead("Pairing with the hub", Icons.Outlined.Link)
                        val r = result.value
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            if (r == null) CircularProgressIndicator(Modifier.padding(2.dp), strokeWidth = 2.dp)
                            else Dot(if (r.first) Level.Ok else Level.Bad)
                            Body(r?.second ?: "Connecting to $host…")
                        }
                    }
                }
            }
        }
        Thread({
            val r = try {
                val state = HubState(this)
                state.forget()  // a fresh identity per pairing, like a reinstall of the Windows agent
                val agentId = Pairing.pair(state, host, port, token, fingerprint)
                AgentService.start(this)
                true to "Paired as $agentId. You can close this."
            } catch (e: Exception) {
                android.util.Log.w("SwitcherPair", "pairing failed", e)
                false to "Pairing failed: ${e.message}"
            }
            runOnUiThread {
                result.value = r
                window.decorView.postDelayed({ finish() }, if (r.first) 2500 else 15000)
            }
        }, "pair").start()
    }
}
