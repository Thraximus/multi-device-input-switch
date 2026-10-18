package online.thrax.switcher.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import online.thrax.switcher.R

/**
 * Two skins, chosen on the PC (the hub pushes it): Material — the phone's own look, Material You colours
 * on Android 12+ and light/dark from the system — and Cogitator, the machine-cult skin of the PC GUI and
 * the Archivum app (tokens and fonts transcribed from there: scorched iron, brass, the red rune).
 *
 * Components read [Tokens] for colour and [isCogitator] for ornament, so both skins share one layout.
 */
enum class Skin(val key: String) {
    Material("material"),
    Cogitator("cogitator");

    companion object {
        fun from(key: String?) = entries.firstOrNull { it.key == key } ?: Material
    }
}

@Immutable
data class Tokens(
    val ground: Color,
    val surface: Color,
    val surface2: Color,
    val border: Color,
    val borderStrong: Color,
    val text: Color,
    val text2: Color,
    val muted: Color,
    val accent: Color,
    val onAccent: Color,
    val ok: Color,
    val warn: Color,
    val bad: Color,
    // Cogitator materials (under Material they resolve to plain equivalents)
    val brass: Color,
    val brassHi: Color,
    val brassDim: Color,
    val brassGhost: Color,
    val amber: Color,
    val iron: Color,
    val rivetCore: Color,
    val rivetEdge: Color,
) {
    companion object {
        /** `html[data-skin='cogitator']` from Archivum's tokens.cogitator.css. Dark only by design. */
        val cogitator = Tokens(
            ground = Color(0xFF0B0908),
            surface = Color(0xFF14100B),
            surface2 = Color(0xFF1C1710),
            border = Color(0xFF2C2213),
            borderStrong = Color(0xFF5D4B26),
            text = Color(0xFFE8DCC0),
            text2 = Color(0xFFB9A67E),
            muted = Color(0xFF6B5C3C),
            accent = Color(0xFFA11111),
            onAccent = Color(0xFFFFE9D0),
            ok = Color(0xFF7FAE4F),
            warn = Color(0xFFD99A2B),
            bad = Color(0xFFFF5540),
            brass = Color(0xFFB08D3E),
            brassHi = Color(0xFFD4AB52),
            brassDim = Color(0xFF6B5624),
            brassGhost = Color(0xFFB08D3E).copy(alpha = 0.22f),
            amber = Color(0xFFFFD98A),
            iron = Color(0xFF171208),
            rivetCore = Color(0xFF7D6428),
            rivetEdge = Color(0xFF2C2213),
        )

        /** Material: everything taken from the colour scheme in force. */
        fun from(c: ColorScheme, dark: Boolean) = Tokens(
            ground = c.surface,
            surface = c.surfaceContainerLow,
            surface2 = c.surfaceContainer,
            border = c.outlineVariant,
            borderStrong = c.outline,
            text = c.onSurface,
            text2 = c.onSurfaceVariant,
            muted = c.outline,
            accent = c.primary,
            onAccent = c.onPrimary,
            ok = if (dark) Color(0xFF81C995) else Color(0xFF1E8E3E),
            warn = if (dark) Color(0xFFFDD663) else Color(0xFFB06000),
            bad = c.error,
            brass = c.primary,
            brassHi = c.primary,
            brassDim = c.outline,
            brassGhost = c.outlineVariant,
            amber = c.primary,
            iron = c.surfaceContainerHighest,
            rivetCore = c.outline,
            rivetEdge = c.outlineVariant,
        )
    }
}

/**
 * Grenze Gotisch ships only as a variable font, so each weight is pinned on the `wght` axis (otherwise
 * every weight renders at the axis default) — as in Archivum.
 */
@OptIn(ExperimentalTextApi::class)
val GrenzeGotisch = FontFamily(
    Font(R.font.grenze_gotisch, FontWeight.W600, variationSettings = FontVariation.Settings(FontVariation.weight(600))),
    Font(R.font.grenze_gotisch, FontWeight.W700, variationSettings = FontVariation.Settings(FontVariation.weight(700))),
)

val IbmPlexMono = FontFamily(
    Font(R.font.ibm_plex_mono_regular, FontWeight.W400),
    Font(R.font.ibm_plex_mono_medium, FontWeight.W500),
)

@Immutable
data class Faces(val body: FontFamily, val display: FontFamily, val label: FontFamily)

val LocalTokens = staticCompositionLocalOf { Tokens.cogitator }
val LocalSkin = staticCompositionLocalOf { Skin.Material }
val LocalFaces = staticCompositionLocalOf { Faces(FontFamily.SansSerif, FontFamily.SansSerif, FontFamily.SansSerif) }

val isCogitator: Boolean
    @Composable get() = LocalSkin.current == Skin.Cogitator

/** Cogitator's colour scheme for the Material widgets it still uses (dialogs, ripples, text selection). */
private val cogitatorScheme = darkColorScheme(
    primary = Tokens.cogitator.brassHi,
    onPrimary = Tokens.cogitator.ground,
    secondary = Tokens.cogitator.brass,
    background = Tokens.cogitator.ground,
    surface = Tokens.cogitator.surface,
    onSurface = Tokens.cogitator.text,
    onSurfaceVariant = Tokens.cogitator.text2,
    outline = Tokens.cogitator.borderStrong,
    outlineVariant = Tokens.cogitator.border,
    error = Tokens.cogitator.bad,
)

@Composable
fun SwitcherTheme(skin: Skin, content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val ctx = LocalContext.current
    val scheme = when {
        skin == Skin.Cogitator -> cogitatorScheme
        Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    val tokens = if (skin == Skin.Cogitator) Tokens.cogitator else Tokens.from(scheme, dark)
    val faces = if (skin == Skin.Cogitator) {
        Faces(body = IbmPlexMono, display = GrenzeGotisch, label = IbmPlexMono)
    } else {
        Faces(FontFamily.Default, FontFamily.Default, FontFamily.Default)
    }
    MaterialTheme(colorScheme = scheme) {
        CompositionLocalProvider(
            LocalTokens provides tokens,
            LocalSkin provides skin,
            LocalFaces provides faces,
            content = content,
        )
    }
}
