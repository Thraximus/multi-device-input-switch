package online.thrax.switcher

import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.lifecycle.compose.LifecycleResumeEffect
import online.thrax.switcher.core.App
import online.thrax.switcher.features.Features
import online.thrax.switcher.features.Fix
import online.thrax.switcher.theme.Skin
import online.thrax.switcher.theme.SwitcherTheme
import online.thrax.switcher.ui.FeatureCard
import online.thrax.switcher.ui.HomeScreen

/** Status and the few actions that must happen on the device; settings live in the PC's GUI. */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Features.init(this)
        if (HubState(this).paired()) AgentService.start(this)
        val version = runCatching { packageManager.getPackageInfo(packageName, 0).versionName }.getOrNull() ?: "dev"
        setContent {
            val state by App.state.collectAsState()
            // Bumped on every resume, so permission checks re-run after visiting system settings.
            val resumes = remember { mutableIntStateOf(0) }
            LifecycleResumeEffect(Unit) {
                resumes.intValue++
                // On screen now: Android 13+ only asks for log access while the app is visible.
                if (state.enabled[Features.clipboard.id] == true) Features.clipboard.retryLogAccess()
                onPauseOrDispose {}
            }
            SwitcherTheme(Skin.from(state.skin)) {
                key(resumes.intValue) {
                    val cards = Features.all.map { f ->
                        val status by f.status.collectAsState()
                        FeatureCard(
                            id = f.id, title = f.title, summary = f.summary, icon = f.icon,
                            enabled = state.enabled[f.id] == true, status = status,
                            needs = f.permissions(this), details = { f.Details() },
                        )
                    }
                    HomeScreen(
                        state = state, device = Build.MODEL, version = version, features = cards,
                        onUseHere = { AgentService.instance?.useKeyboardHere() },
                        onReturn = { AgentService.instance?.returnKeyboard() },
                        onFix = { fix ->
                            when (fix) {
                                is Fix.OpenSettings -> startActivity(fix.intent(this))
                                is Fix.Retry -> fix.run()
                                is Fix.Request -> requestPermissions(fix.permissions.toTypedArray(), 1)
                                Fix.ByHub -> {}
                            }
                        },
                    )
                }
            }
        }
    }
}
