package online.thrax.switcher.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.material.icons.outlined.KeyboardReturn
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.LinkOff
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import online.thrax.switcher.R
import online.thrax.switcher.core.AppState
import online.thrax.switcher.features.Fix
import online.thrax.switcher.features.Need
import online.thrax.switcher.theme.LocalTokens
import online.thrax.switcher.theme.isCogitator

/** What a feature card shows (built from a [online.thrax.switcher.features.Feature] by the activity). */
data class FeatureCard(
    val id: String,
    val title: String,
    val summary: String,
    val icon: ImageVector,
    val enabled: Boolean,
    val status: String,
    val needs: List<Need>,
    val details: @Composable () -> Unit = {},
)

@Composable
fun HomeScreen(
    state: AppState,
    device: String,
    version: String,
    features: List<FeatureCard>,
    /** Send the keyboard/mouse to a machine (a target id; null for the hub's PC). */
    onSwitch: (String?) -> Unit,
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onFix: (Fix) -> Unit,
) {
    val tk = LocalTokens.current
    Column(
        Modifier
            .fillMaxSize()
            .background(tk.ground)
            .drawBehind {
                if (tk.brass != tk.accent) {  // Cogitator: the warm glow from the top of the page
                    drawRect(Brush.radialGradient(listOf(Color(0xFF1A1410), Color.Transparent),
                        center = Offset(size.width / 2, -size.height * .08f), radius = size.width * 1.1f))
                }
            }
            .verticalScroll(rememberScrollState())
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 18.dp, vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Column(Modifier.widthIn(max = 640.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Header(device, state)
            HubPanel(state, onSwitch, onConnect, onDisconnect)
            SectionDivider("Features")
            for (f in features) FeaturePanel(f, onFix)
            Spacer(Modifier.height(6.dp))
            if (state.update.isNotEmpty()) Label(state.update, Modifier.align(Alignment.CenterHorizontally),
                color = LocalTokens.current.warn)
            Label("Switcher $version" + if (state.agentId.isNotEmpty()) " · agent ${state.agentId}" else "",
                Modifier.align(Alignment.CenterHorizontally))
            Label("Settings live on the PC: Switcher Hub → Machines", Modifier.align(Alignment.CenterHorizontally))
        }
    }
}

@Composable
private fun Header(device: String, state: AppState) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp),
        modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)) {
        if (isCogitator) {
            Cog(42.dp, 12, spin = true, periodMs = 30_000)
        } else {
            Image(painterResource(R.drawable.ic_launcher), null, Modifier.size(48.dp))
        }
        Column {
            Heading("Switcher", size = 28)
            Label(if (state.paired && state.host.isNotEmpty()) "$device · paired with ${state.host}" else device)
        }
    }
}

@Composable
private fun HubPanel(state: AppState, onSwitch: (String?) -> Unit, onConnect: () -> Unit, onDisconnect: () -> Unit) {
    val holder = state.holder
    Panel(active = holder?.here == true) {
        PanelHead("Hub", Icons.Outlined.Hub) {
            when {
                state.stopped -> Pill("Off")
                state.connected -> Pill("Online", Level.Ok)
                else -> Pill("Offline", Level.Bad)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Dot(when {
                state.stopped -> Level.Idle
                state.connected -> Level.Ok
                state.paired -> Level.Warn
                else -> Level.Bad
            })
            Body(state.connection, Modifier.weight(1f))
            if (state.stopped) {
                Btn("Connect", onConnect, style = BtnStyle.Primary, icon = Icons.Outlined.Link)
            } else if (state.paired) {
                Btn("Disconnect", onDisconnect, style = BtnStyle.Quiet, icon = Icons.Outlined.LinkOff)
            }
        }
        if (state.stopped) Label("Every feature is off until you connect again")
        if (holder != null) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Column(Modifier.weight(1f)) {
                    Label("Keyboard & mouse")
                    Heading(if (holder.here) "Here" else holder.name, size = 20)
                    if (holder.pending) Label("switching once all keys are released")
                }
                if (holder.here) {
                    Btn("Return to PC", { onSwitch(null) }, style = BtnStyle.Normal, icon = Icons.Outlined.KeyboardReturn)
                } else {
                    Btn("Use here", { onSwitch(holder.selfTarget) }, style = BtnStyle.Primary, icon = Icons.Outlined.Keyboard,
                        enabled = holder.selfTarget != null)
                }
            }
            // Everywhere else they can go: the button above covers this device and (from here) the PC.
            val others = holder.machines.filter {
                it.connected && it.id != holder.at && it.id != holder.selfTarget && !(holder.here && it.id == null)
            }
            if (others.isNotEmpty()) {
                Label("Send to")
                MachinePicker(others, { false }, { onSwitch(it.id) })
            }
        }
    }
}

@Composable
private fun FeaturePanel(f: FeatureCard, onFix: (Fix) -> Unit) {
    val missing = f.needs.filter { !it.granted }
    Panel(active = f.enabled && missing.isEmpty()) {
        PanelHead(f.title, f.icon) {
            Pill(if (f.enabled) "On" else "Off", if (f.enabled) Level.Ok else Level.Idle)
        }
        Body(f.summary, color = LocalTokens.current.text2)
        if (!f.enabled) {
            Label("Turned off on the PC (Switcher Hub → Machines)")
            return@Panel
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Dot(if (missing.isEmpty()) Level.Ok else Level.Warn)
            Body(f.status)
        }
        f.details()
        for (need in f.needs) NeedRow(need, onFix)
    }
}

@Composable
private fun NeedRow(need: Need, onFix: (Fix) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxWidth()) {
        Dot(if (need.granted) Level.Ok else Level.Bad)
        Label(need.label, Modifier.weight(1f))
        if (!need.granted) {
            when (val fix = need.fix) {
                is Fix.ByHub -> Label("reinstall from the PC", color = LocalTokens.current.warn)
                is Fix.OpenSettings -> Btn(fix.action, { onFix(fix) }, style = BtnStyle.Quiet)
                is Fix.Retry -> Btn(fix.action, { onFix(fix) }, style = BtnStyle.Quiet)
                is Fix.Request -> Btn(fix.action, { onFix(fix) }, style = BtnStyle.Quiet)
            }
        }
    }
}
