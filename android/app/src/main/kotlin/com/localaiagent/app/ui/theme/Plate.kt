package com.localaiagent.app.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** The motion curve of the PC's premium look: a spring-like ease with mass. */
val SpringEasing = androidx.compose.animation.core.CubicBezierEasing(0.32f, 0.72f, 0f, 1f)

/**
 * A surface drawn as a plate, like on the PC (premium.css `--plate`): a core with a light from
 * above and a hairline, set in a faint outer tray ring — machined, not glassy, and with no extra
 * layout. The ring is drawn outside the bounds, so leave ~6 dp of room around a plate.
 *
 * [tray] draws the outer ring (cards, the composer); without it only the hairline shows (bubbles,
 * inputs). [focused] turns the ring and the line into the accent, as the PC does on focus.
 */
fun Modifier.plate(
    radius: Dp,
    tray: Boolean = true,
    focused: Boolean = false,
    fill: Color? = null,
): Modifier = composed {
    val t = LocalAltair.current
    val accent = androidx.compose.material3.MaterialTheme.colorScheme.primary
    val f by animateFloatAsState(if (focused) 1f else 0f, tween(280, easing = SpringEasing), label = "plateFocus")
    drawBehind { drawPlate(radius.toPx(), t, accent, f, tray, fill ?: t.core) }
}

private fun DrawScope.drawPlate(r: Float, t: AltairTokens, accent: Color, focus: Float, tray: Boolean, core: Color) {
    val d = density
    val w = size.width
    val h = size.height
    fun ring(out: Float, width: Float, color: Color) = drawRoundRect(
        color, topLeft = Offset(-out, -out), size = Size(w + 2 * out, h + 2 * out),
        cornerRadius = CornerRadius(r + out), style = Stroke(width),
    )
    if (tray) {
        ring(5.5f * d, 1f * d, lerp(t.trayEdge, accent.copy(alpha = 0.16f), focus))
        ring(3f * d, 4f * d, lerp(t.tray, t.accentTint, focus))
    }
    ring(0.5f * d, 1f * d, lerp(t.hair, t.accentLine, focus))
    drawRoundRect(core, cornerRadius = CornerRadius(r))
    // The light from above: a 1 dp highlight fading out within the top edge.
    drawRoundRect(
        Brush.verticalGradient(0f to t.sheen, (2.5f * d / h).coerceIn(0.001f, 1f) to Color.Transparent),
        topLeft = Offset(0.5f * d, 0.5f * d), size = Size(w - d, h - d),
        cornerRadius = CornerRadius(r - 0.5f * d), style = Stroke(1f * d),
    )
}

/** The accent as a soft vertical gradient (lighter on top), for primary buttons — PC `--accent-grad`. */
@Composable
fun accentGradient(): Brush {
    val t = LocalAltair.current
    val a = androidx.compose.material3.MaterialTheme.colorScheme.primary
    return Brush.verticalGradient(0f to t.accentHigh, 0.55f to a, 1f to t.accentLow)
}

/** Radii of the premium look (premium.css --r-*). */
object PremiumRadii {
    val sm = 10.dp
    val md = 14.dp
    val lg = 20.dp
    val bubble = RoundedCornerShape(18.dp, 18.dp, 6.dp, 18.dp)
}
