package online.thrax.switcher.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.DesktopWindows
import androidx.compose.material.icons.outlined.LaptopWindows
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import online.thrax.switcher.core.Machine
import online.thrax.switcher.theme.LocalFaces
import online.thrax.switcher.theme.LocalTokens
import online.thrax.switcher.theme.Tokens
import online.thrax.switcher.theme.isCogitator
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/*
 * The component set. Each one draws either the Cogitator furniture (ported from Archivum's
 * Ornament/Panel/Button) or the plain Material 3 equivalent, so screens are written once.
 */

enum class Level { Ok, Warn, Bad, Idle }

/** Chamfered top-left and bottom-right corners: the Cogitator plate and button shape. */
fun cutCorners(cut: Dp): Shape = object : Shape {
    override fun createOutline(size: Size, layoutDirection: LayoutDirection, density: Density): Outline {
        val c = with(density) { cut.toPx() }
        return Outline.Generic(Path().apply {
            moveTo(c, 0f)
            lineTo(size.width, 0f)
            lineTo(size.width, size.height - c)
            lineTo(size.width - c, size.height)
            lineTo(0f, size.height)
            lineTo(0f, c)
            close()
        })
    }
}

// --- ornament (Cogitator only; draws nothing under Material) ---------------------------------------------

@Composable
fun Modifier.rivets(): Modifier {
    if (!isCogitator) return this
    val tk = LocalTokens.current
    return drawBehind {
        val r = 2.dp.toPx()
        val inset = 6.dp.toPx() + r
        for (c in listOf(
            Offset(inset + 4.dp.toPx(), inset), Offset(size.width - inset, inset),
            Offset(inset, size.height - inset), Offset(size.width - inset - 4.dp.toPx(), size.height - inset),
        )) {
            drawCircle(
                Brush.radialGradient(listOf(tk.rivetCore, tk.rivetEdge), Offset(c.x - r * .3f, c.y - r * .3f), r * 1.4f),
                radius = r, center = c,
            )
            drawCircle(Color.Black.copy(alpha = .5f), radius = r, center = c, style = Stroke(1.dp.toPx()))
        }
    }
}

/** 45° hazard chevrons (`--hazard`), clipped to the bar. */
@Composable
fun HazardBar(modifier: Modifier = Modifier, height: Dp = 6.dp) {
    if (!isCogitator) return
    val tk = LocalTokens.current
    Spacer(modifier.fillMaxWidth().height(height).drawBehind { drawHazard(tk) })
}

private fun DrawScope.drawHazard(tk: Tokens) {
    val stripe = 10.dp.toPx()
    val period = stripe * 2
    clipRect {
        var x = -size.height - period
        while (x < size.width + size.height + period) {
            val p = Path().apply {
                moveTo(x, size.height); lineTo(x + stripe, size.height)
                lineTo(x + stripe + size.height, 0f); lineTo(x + size.height, 0f); close()
            }
            drawPath(p, tk.brass, alpha = .45f)
            x += period
        }
    }
}

/** Gear outline centred on (0,0) in a -112..112 space; even-odd so the bore is a hole (Archivum's cogPath). */
fun cogPath(teeth: Int, rRoot: Float, rTip: Float, rHole: Float): Path {
    val step = (PI * 2 / teeth).toFloat()
    return Path().apply {
        fillType = PathFillType.EvenOdd
        for (i in 0 until teeth) {
            val a = i * step
            fun px(f: Float, r: Float) = cos(a + f * step) * r
            fun py(f: Float, r: Float) = sin(a + f * step) * r
            if (i == 0) moveTo(px(0f, rTip), py(0f, rTip)) else lineTo(px(0f, rTip), py(0f, rTip))
            lineTo(px(.38f, rTip), py(.38f, rTip))
            lineTo(px(.5f, rRoot), py(.5f, rRoot))
            lineTo(px(.88f, rRoot), py(.88f, rRoot))
        }
        close()
        if (rHole > 0f) addOval(Rect(Offset(-rHole, -rHole), Size(rHole * 2, rHole * 2)))
    }
}

@Composable
fun Cog(size: Dp = 22.dp, teeth: Int = 10, spin: Boolean = false, periodMs: Int = 9000, color: Color? = null,
        modifier: Modifier = Modifier) {
    if (!isCogitator) return
    val tint = color ?: LocalTokens.current.brass
    val path = remember(teeth) { cogPath(teeth, 76f, 100f, 36f) }
    // Read inside the layer block, so spinning redraws this node only (no recomposition).
    val angle = if (spin) rememberInfiniteTransition(label = "cog").animateFloat(
        0f, 360f, infiniteRepeatable(tween(periodMs, easing = LinearEasing)), label = "spin",
    ) else null
    Canvas(modifier.size(size).graphicsLayer { rotationZ = angle?.value ?: 0f }) {
        val k = this.size.minDimension / 224f
        translate(this.size.width / 2, this.size.height / 2) { scale(k, k, Offset.Zero) { drawPath(path, tint) } }
    }
}

// --- text ------------------------------------------------------------------------------------------------

/** Display heading: Grenze Gotisch in brass under Cogitator, a plain title under Material. */
@Composable
fun Heading(text: String, size: Int = 22, modifier: Modifier = Modifier, color: Color? = null) {
    val tk = LocalTokens.current
    if (isCogitator) {
        Text(text, modifier, color = color ?: tk.brassHi, fontFamily = LocalFaces.current.display,
            fontWeight = FontWeight.W700, fontSize = (size * 1.2f).sp, lineHeight = (size * 1.3f).sp)
    } else {
        Text(text, modifier, color = color ?: tk.text, fontSize = size.sp, fontWeight = FontWeight.Medium,
            lineHeight = (size * 1.25f).sp)
    }
}

/** Small caption; the spaced uppercase "litany" under Cogitator. */
@Composable
fun Label(text: String, modifier: Modifier = Modifier, color: Color? = null, maxLines: Int = Int.MAX_VALUE) {
    val tk = LocalTokens.current
    if (isCogitator) {
        Text(text.uppercase(), modifier, color = color ?: tk.text2.copy(alpha = .8f), fontFamily = LocalFaces.current.label,
            fontSize = 10.5.sp, letterSpacing = 1.6.sp, lineHeight = 15.sp, maxLines = maxLines, overflow = TextOverflow.Ellipsis)
    } else {
        Text(text, modifier, color = color ?: tk.text2, style = MaterialTheme.typography.bodySmall, maxLines = maxLines,
            overflow = TextOverflow.Ellipsis)
    }
}

@Composable
fun Body(text: String, modifier: Modifier = Modifier, color: Color? = null, maxLines: Int = Int.MAX_VALUE) {
    val tk = LocalTokens.current
    Text(text, modifier, color = color ?: tk.text, fontFamily = LocalFaces.current.body,
        fontSize = if (isCogitator) 13.sp else 15.sp, lineHeight = if (isCogitator) 19.sp else 21.sp,
        maxLines = maxLines, overflow = TextOverflow.Ellipsis)
}

// --- status ---------------------------------------------------------------------------------------------

@Composable
fun levelColor(level: Level): Color {
    val tk = LocalTokens.current
    return when (level) {
        Level.Ok -> tk.ok
        Level.Warn -> tk.warn
        Level.Bad -> tk.bad
        Level.Idle -> tk.muted
    }
}

@Composable
fun Dot(level: Level, modifier: Modifier = Modifier) {
    val c = levelColor(level)
    val glow = isCogitator && level != Level.Idle
    Canvas(modifier.size(14.dp)) {
        if (glow) drawCircle(Brush.radialGradient(listOf(c.copy(alpha = .45f), Color.Transparent)), radius = size.minDimension / 2)
        drawCircle(c, radius = 4.dp.toPx())
    }
}

/** A small tag ("On", "Here", "Off"). */
@Composable
fun Pill(text: String, level: Level = Level.Idle, modifier: Modifier = Modifier) {
    val c = if (level == Level.Idle) LocalTokens.current.text2 else levelColor(level)
    if (isCogitator) {
        Box(modifier.border(1.dp, c.copy(alpha = .55f), cutCorners(2.dp)).background(c.copy(alpha = .08f))
            .padding(horizontal = 8.dp, vertical = 2.dp)) {
            Text(text.uppercase(), color = c, fontFamily = LocalFaces.current.label, fontSize = 10.sp, letterSpacing = 1.4.sp)
        }
    } else {
        Box(modifier.clip(RoundedCornerShape(50)).background(c.copy(alpha = .14f)).padding(horizontal = 10.dp, vertical = 3.dp)) {
            Text(text, color = c, style = MaterialTheme.typography.labelMedium)
        }
    }
}

// --- containers -----------------------------------------------------------------------------------------

/**
 * A card. Cogitator: a bolted iron plate with chamfered corners, lit amber with the red rune along its
 * foot when [active]. Material: a rounded card, tinted when [active].
 */
@Composable
fun Panel(modifier: Modifier = Modifier, active: Boolean = false, content: @Composable ColumnScope.() -> Unit) {
    val tk = LocalTokens.current
    if (isCogitator) {
        val shape = cutCorners(9.dp)
        Column(
            modifier.fillMaxWidth()
                .border(1.dp, if (active) tk.brassHi else tk.brassGhost, shape)
                .clip(shape)
                .background(Brush.verticalGradient(listOf(if (active) Color(0xFF221A10) else tk.surface2, tk.surface)))
                .rivets()
                .drawBehind {
                    if (active) drawRect(tk.accent, Offset(0f, size.height - 2.5.dp.toPx()), Size(size.width, 2.dp.toPx()))
                }
                .padding(horizontal = 18.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            content = content,
        )
    } else {
        Card(
            modifier.fillMaxWidth(),
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.cardColors(
                containerColor = if (active) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surfaceContainer,
            ),
        ) {
            Column(Modifier.padding(horizontal = 20.dp, vertical = 18.dp), verticalArrangement = Arrangement.spacedBy(10.dp), content = content)
        }
    }
}

/** A panel's title row: icon, title, and whatever goes on the right. Cogitator adds the hazard rule. */
@Composable
fun PanelHead(title: String, icon: ImageVector?, aside: @Composable RowScope.() -> Unit = {}) {
    val tk = LocalTokens.current
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        if (icon != null) {
            if (isCogitator) {
                Icon(icon, null, tint = tk.brassHi, modifier = Modifier.size(22.dp))
            } else {
                Box(Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.primaryContainer),
                    contentAlignment = Alignment.Center) {
                    Icon(icon, null, tint = MaterialTheme.colorScheme.onPrimaryContainer, modifier = Modifier.size(22.dp))
                }
            }
        }
        Heading(title, size = 18, modifier = Modifier.weight(1f))
        aside()
    }
    HazardBar(Modifier.padding(top = 2.dp))
}

/** A section break with a label: cog rosettes and fading brass rules under Cogitator. */
@Composable
fun SectionDivider(label: String, modifier: Modifier = Modifier) {
    val tk = LocalTokens.current
    Row(modifier.fillMaxWidth().padding(top = 18.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        if (isCogitator) {
            Cog(13.dp, 8, color = tk.brassDim)
            Label(label, color = tk.brass)
            Spacer(Modifier.weight(1f).height(1.dp).background(Brush.horizontalGradient(listOf(tk.brassDim, Color.Transparent))))
        } else {
            Text(label, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.weight(1f).height(1.dp).background(tk.border))
        }
    }
}

// --- buttons --------------------------------------------------------------------------------------------

enum class BtnStyle { Primary, Normal, Quiet }

@Composable
fun Btn(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, style: BtnStyle = BtnStyle.Normal,
        enabled: Boolean = true, icon: ImageVector? = null) {
    val tk = LocalTokens.current
    if (!isCogitator) {
        val inner: @Composable RowScope.() -> Unit = {
            if (icon != null) {
                Icon(icon, null, Modifier.size(18.dp))
                Spacer(Modifier.size(8.dp))
            }
            Text(text)
        }
        when (style) {
            BtnStyle.Primary -> Button(onClick, modifier, enabled, content = inner)
            BtnStyle.Normal -> FilledTonalButton(onClick, modifier, enabled, content = inner)
            BtnStyle.Quiet -> OutlinedButton(onClick, modifier, enabled, content = inner)
        }
        return
    }
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    val shape = cutCorners(7.dp)
    val (fill, edge, ink) = when {
        !enabled -> Triple(Color(0xFF120E09), tk.border, tk.muted)
        style == BtnStyle.Primary -> Triple(if (pressed) Color(0xFF7A1010) else Color(0xFF5A0E0E), Color(0xFFE03A2A), tk.onAccent)
        else -> Triple(if (pressed) Color(0xFF2A2010) else Color(0xFF16110B), if (pressed) tk.brass else tk.brassDim,
            if (pressed) tk.amber else tk.brassHi)
    }
    Row(
        modifier.border(1.dp, edge, shape).clip(shape).background(fill)
            .clickable(source, null, enabled = enabled, onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (icon != null) Icon(icon, null, tint = ink, modifier = Modifier.size(16.dp))
        Text(text.uppercase(), color = ink, fontFamily = LocalFaces.current.label, fontSize = 11.5.sp, letterSpacing = 1.8.sp)
    }
}

// --- machines -------------------------------------------------------------------------------------------

/** The icon for a machine of this kind ("linux", "pico", "ble"). */
fun machineIcon(kind: String): ImageVector = when (kind) {
    "linux" -> Icons.Outlined.DesktopWindows
    "pico" -> Icons.Outlined.LaptopWindows
    else -> Icons.Outlined.PhoneAndroid
}

/** Machines to pick from, one button each; [selected] is drawn as the primary one. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MachinePicker(machines: List<Machine>, selected: (Machine) -> Boolean, onPick: (Machine) -> Unit,
                  modifier: Modifier = Modifier) {
    FlowRow(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (m in machines) {
            Btn(m.name, { onPick(m) }, style = if (selected(m)) BtnStyle.Primary else BtnStyle.Quiet,
                icon = machineIcon(m.kind))
        }
    }
}
