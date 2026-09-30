package com.localaiagent.app.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.padding
import com.localaiagent.app.ui.theme.Brand

/**
 * Alti's moods, as on the PC (pc/static/mascot.js): idle floats and blinks, think spins gently next to
 * the running status, sleep sits in empty places with a floating "z", sad droops on errors, help
 * wiggles on cards that need the user, happy hops.
 */
enum class AltiMood { Idle, Think, Sleep, Sad, Help, Happy }

/** Alti's star outline (the cubic Béziers from mascot.js, 24×24 grid); center (cx, cy), R to a tip. */
private fun altiPath(cx: Float, cy: Float, R: Float): Path {
    val s = R / 11.4f
    fun px(x: Float) = cx + (x - 12f) * s
    fun py(y: Float) = cy + (y - 12f) * s
    return Path().apply {
        moveTo(px(12f), py(0.6f))
        cubicTo(px(13.2f), py(7.7f), px(16.3f), py(10.8f), px(23.4f), py(12f))
        cubicTo(px(16.3f), py(13.2f), px(13.2f), py(16.3f), px(12f), py(23.4f))
        cubicTo(px(10.8f), py(16.3f), px(7.7f), py(13.2f), px(0.6f), py(12f))
        cubicTo(px(7.7f), py(10.8f), px(10.8f), py(7.7f), px(12f), py(0.6f))
        close()
    }
}

/** The star body: a volumetric radial gradient, shading at the bottom, gloss and a rim (mascot.js spec). */
private fun DrawScope.drawAltiBody(cx: Float, cy: Float, R: Float) {
    val path = altiPath(cx, cy, R)
    drawPath(
        path,
        Brush.radialGradient(
            0f to Color(0xFFFFF6DA), 0.42f to Color(0xFFFFD37A), 0.80f to Color(0xFFF1A93C), 1f to Color(0xFFD6811E),
            center = Offset(cx - 0.28f * R, cy - 0.40f * R), radius = 1.7f * R,
        ),
    )
    // darker toward the bottom for volume
    drawPath(
        path,
        Brush.verticalGradient(0f to Color.Transparent, 0.55f to Color.Transparent, 1f to Color(0x6B5A2D08), startY = cy - R, endY = cy + R),
    )
    // glossy highlight at the upper left, inside the outline
    clipPath(path) {
        drawCircle(
            Brush.radialGradient(
                listOf(Color.White.copy(alpha = 0.5f), Color.Transparent),
                center = Offset(cx - 0.30f * R, cy - 0.45f * R), radius = 0.95f * R,
            ),
            radius = 0.95f * R, center = Offset(cx - 0.30f * R, cy - 0.45f * R),
        )
    }
    // rim outline
    drawPath(
        path, Brush.verticalGradient(listOf(Color(0xFFFFE9A8), Color(0xFFC9761A)), startY = cy - R, endY = cy + R),
        style = Stroke(width = R * 0.05f), alpha = 0.55f,
    )
}

private val ALTI_EASE = CubicBezierEasing(0.45f, 0f, 0.55f, 1f)
private val EYE = Color(0xFF241608)

/**
 * Alti: the main star, two satellites (the three bodies) and a minimal face. Idle floats with a
 * slight sway, the contact shadow shrinks as it rises, the satellites bob on their own rhythms, the
 * eyes blink every few seconds, and it pops in on appearance. Every motion stops when the system
 * turns animations off.
 */
@Composable
fun AltiMascot(size: Dp, satellites: Boolean = true, mood: AltiMood = AltiMood.Idle, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val motion = remember {
        runCatching {
            android.provider.Settings.Global.getFloat(
                context.contentResolver, android.provider.Settings.Global.ANIMATOR_DURATION_SCALE, 1f,
            )
        }.getOrDefault(1f) > 0f
    }
    val t = rememberInfiniteTransition(label = "alti")
    fun swing(ms: Int) = infiniteRepeatable<Float>(tween(ms / 2, easing = ALTI_EASE), RepeatMode.Reverse)
    val float by t.animateFloat(0f, 1f, swing(4500), label = "float")
    val bobA by t.animateFloat(0f, 1f, swing(5500), label = "bobA")
    val bobB by t.animateFloat(0f, 1f, swing(6800), label = "bobB")
    val glow by t.animateFloat(0.12f, 0.20f, infiniteRepeatable(tween(2200), RepeatMode.Reverse), label = "glow")
    // One loop per mood, 0..1 over its period; the shapes below are the PC keyframes.
    val cycleMs = when (mood) {
        AltiMood.Think -> 1600; AltiMood.Sleep -> 3600; AltiMood.Sad -> 4000
        AltiMood.Help -> 2400; AltiMood.Happy -> 1900; AltiMood.Idle -> 5200
    }
    val cycle by t.animateFloat(0f, 1f, infiniteRepeatable(tween(cycleMs, easing = LinearEasing)), label = "cycle")
    val measurer = rememberTextMeasurer()
    val appear = remember { Animatable(if (motion) 0f else 1f) }
    LaunchedEffect(Unit) { appear.animateTo(1f, tween(600, easing = CubicBezierEasing(0.2f, 0.8f, 0.2f, 1f))) }

    Canvas(
        modifier.size(size).graphicsLayer {
            alpha = appear.value
            val sc = 0.82f + 0.18f * appear.value
            scaleX = sc
            scaleY = sc
        },
    ) {
        val c = if (motion) cycle else 0f
        val fl = if (motion && mood == AltiMood.Idle) float else 0f
        val w = this.size.width
        val cx = w / 2f
        val baseCy = w / 2f
        val R = w * 0.33f
        val s = R / 11.4f
        val unit = 24f * s

        // The mood's motion: lift (px), rotation (deg) and scale (x, y) of the core.
        var lift = -0.05f * unit * fl
        var rot = -1.5f + 3f * fl
        var sx = 1f
        var sy = 1f
        when (mood) {
            AltiMood.Think -> { val k = 0.5f - 0.5f * kotlin.math.cos(c * 2f * Math.PI.toFloat()); sx = 0.94f - 0.04f * k; sy = sx; rot = 180f * k }
            AltiMood.Sleep -> { val k = 0.5f - 0.5f * kotlin.math.cos(c * 2f * Math.PI.toFloat()); sx = 1f - 0.05f * k; sy = sx; lift = 0.03f * unit * k; rot = 0f }
            AltiMood.Sad -> { val k = 0.5f - 0.5f * kotlin.math.cos(c * 2f * Math.PI.toFloat()); rot = -4f - 3f * k; lift = (0.02f + 0.02f * k) * unit }
            AltiMood.Help -> { rot = wiggle(c); lift = 0f }
            AltiMood.Happy -> { val (dy, hx, hy) = hop(c); lift = dy * unit; sx = hx; sy = hy; rot = 0f }
            AltiMood.Idle -> {}
        }
        val cy = baseCy + lift

        // contact shadow: narrower and fainter while the star is up
        val up = (-lift / (0.09f * unit)).coerceIn(0f, 1f)
        val shW = 6.2f * s * (1f - 0.14f * up)
        drawOval(
            Color.Black.copy(alpha = 0.28f - 0.10f * up),
            topLeft = Offset(cx - shW, baseCy + 10.4f * s - 1.15f * s), size = Size(shW * 2, 2.3f * s),
        )
        drawCircle(
            Brush.radialGradient(listOf(Brand.glow.copy(alpha = glow), Color.Transparent), center = Offset(cx, cy), radius = R * 2f),
            radius = R * 2f, center = Offset(cx, cy),
        )
        if (satellites) {
            val a = if (motion) bobA else 0f
            val b = if (motion) bobB else 0f
            drawAltiBody(cx + 8.6f * s, baseCy - 7.6f * s - 0.08f * (0.4f * unit) * a, R * 0.40f * (1f + 0.07f * a))
            drawAltiBody(cx - 8.4f * s, baseCy + 7.2f * s + 0.08f * (0.28f * unit) * b, R * 0.28f * (1f - 0.08f * b))
        }
        rotate(rot, pivot = Offset(cx, cy)) {
            scale(sx, sy, pivot = Offset(cx, cy)) {
                drawAltiBody(cx, cy, R)
                drawEyes(cx, cy, s, mood, blink = if (motion && mood in setOf(AltiMood.Idle, AltiMood.Think, AltiMood.Help)) blinkAt(c, mood) else 1f)
            }
        }
        if (mood == AltiMood.Sleep && motion) {
            // A small "z" floats up from the top right and fades.
            val zA = when { c < 0.3f -> c / 0.3f; else -> 1f - (c - 0.3f) / 0.7f }.coerceIn(0f, 1f)
            val zs = 0.7f + 0.4f * c
            val layout = measurer.measure("z", TextStyle(fontSize = (4.2f * s * zs / density).sp, fontWeight = FontWeight.Bold))
            drawText(
                layout, color = Color(0xFF9A9486).copy(alpha = zA),
                topLeft = Offset(cx + 5.6f * s + 2f * s * c, cy - 9.5f * s - 4f * s * c),
            )
        }
    }
}

/**
 * An empty place with Alti asleep above a line of text, as on the PC (`.alti-empty`): nothing is
 * wrong, there is just nothing here yet.
 */
@Composable
fun AltiEmpty(text: String, modifier: Modifier = Modifier) {
    androidx.compose.foundation.layout.Column(
        modifier.padding(horizontal = 24.dp, vertical = 28.dp),
        horizontalAlignment = androidx.compose.ui.Alignment.CenterHorizontally,
    ) {
        AltiMascot(size = 64.dp, satellites = false, mood = AltiMood.Sleep)
        androidx.compose.foundation.layout.Spacer(Modifier.size(12.dp))
        androidx.compose.material3.Text(
            text, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium,
            color = androidx.compose.material3.MaterialTheme.colorScheme.outline,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
    }
}

/** 1 = eyes open, 0.1 = shut; the PC blinks at 93–100 % of a 5.2 s loop. */
private fun blinkAt(c: Float, mood: AltiMood): Float {
    if (mood != AltiMood.Idle) return 1f
    return when {
        c < 0.93f -> 1f
        c < 0.955f -> 1f - 0.9f * (c - 0.93f) / 0.025f
        else -> 0.1f + 0.9f * (c - 0.955f) / 0.045f
    }
}

/** alti-wiggle: still, then a quick left-right shake at the end of the loop. */
private fun wiggle(c: Float): Float = when {
    c < 0.70f -> 0f
    c < 0.78f -> -8f * (c - 0.70f) / 0.08f
    c < 0.86f -> -8f + 15f * (c - 0.78f) / 0.08f
    c < 0.94f -> 7f - 10f * (c - 0.86f) / 0.08f
    else -> -3f + 3f * (c - 0.94f) / 0.06f
}

/** alti-hop: a jump with squash and stretch, then a rest. Returns (lift in units, scaleX, scaleY). */
private fun hop(c: Float): Triple<Float, Float, Float> = when {
    c < 0.20f -> { val k = c / 0.20f; Triple(-0.09f * k, 1f + 0.04f * k, 1f - 0.03f * k) }
    c < 0.35f -> { val k = (c - 0.20f) / 0.15f; Triple(-0.09f * (1 - k), 1.04f - 0.06f * k, 0.97f + 0.06f * k) }
    c < 0.60f -> { val k = (c - 0.35f) / 0.25f; Triple(0f, 0.98f + 0.02f * k, 1.03f - 0.03f * k) }
    else -> Triple(0f, 1f, 1f)
}

private fun DrawScope.drawEyes(cx: Float, cy: Float, s: Float, mood: AltiMood, blink: Float) {
    val ew = 1.5f * s
    val er = 0.75f * s
    val ey = cy + 0.4f * s
    val ex1 = cx - 2.3f * s
    val ex2 = cx + 2.3f * s
    when (mood) {
        AltiMood.Sleep -> {
            // Closed eyes: small arcs down.
            for (x in listOf(ex1, ex2)) {
                drawArc(
                    EYE, startAngle = 0f, sweepAngle = 180f, useCenter = false,
                    topLeft = Offset(x - 0.8f * s, ey - 0.7f * s), size = Size(1.6f * s, 1.2f * s),
                    style = Stroke(1.2f * s, cap = StrokeCap.Round),
                )
            }
        }
        AltiMood.Happy -> {
            for (x in listOf(ex1, ex2)) {
                drawArc(
                    EYE, startAngle = 180f, sweepAngle = 180f, useCenter = false,
                    topLeft = Offset(x - 0.8f * s, ey - 0.4f * s), size = Size(1.6f * s, 1.4f * s),
                    style = Stroke(1.3f * s, cap = StrokeCap.Round),
                )
            }
        }
        else -> {
            val eh = 3.0f * s * (if (mood == AltiMood.Think) 0.7f else 1f) * (if (mood == AltiMood.Sad) 0.8f else 1f) * blink
            val oy = if (mood == AltiMood.Sad) 0.5f * s else 0f
            for (x in listOf(ex1, ex2)) {
                drawRoundRect(EYE, topLeft = Offset(x - ew / 2, ey - eh / 2 + oy), size = Size(ew, eh), cornerRadius = CornerRadius(er, er))
                if (blink > 0.5f) {
                    drawCircle(Color.White.copy(alpha = 0.9f), radius = 0.42f * s, center = Offset(x + 0.45f * s, ey - eh / 2 + oy + 0.55f * s))
                }
            }
            if (mood == AltiMood.Sad) {
                // Brows tilted up in the middle.
                drawLine(EYE, Offset(cx - 3.4f * s, cy - 1.9f * s), Offset(cx - 1.5f * s, cy - 2.6f * s), 0.7f * s, cap = StrokeCap.Round)
                drawLine(EYE, Offset(cx + 3.4f * s, cy - 1.9f * s), Offset(cx + 1.5f * s, cy - 2.6f * s), 0.7f * s, cap = StrokeCap.Round)
            }
        }
    }
}

