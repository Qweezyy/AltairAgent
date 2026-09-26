package com.localaiagent.app.ui

import android.provider.Settings
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameMillis
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.platform.LocalContext
import kotlin.math.max
import kotlin.math.sin

/**
 * Live cosmic background of the welcome screen, ported from the PC (pc/static/cosmos.js): nebulae that
 * slowly breathe, a milky band, constellations, parallax stars drifting upward, bright stars with bloom
 * and diffraction rays, and a rare shooting star. Theme-aware like on the PC: deep space ("nebula") for
 * a dark theme, a calm dawn sky ("dawn") for a light one.
 *
 * Everything is a pure function of time, so there is no per-frame state to allocate. The frame loop runs
 * only while [animate] is on; with system animations turned off a single still frame is drawn.
 */
@Composable
fun CosmosBackground(dark: Boolean, modifier: Modifier = Modifier, animate: Boolean = true) {
    val context = LocalContext.current
    val motion = remember {
        runCatching { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) }
            .getOrDefault(1f) > 0f
    }
    val style = if (dark) NEBULA else DAWN
    val field = remember(style) { StarField.seed(style) }
    var now by remember { mutableLongStateOf(0L) }
    if (motion && animate) {
        LaunchedEffect(Unit) {
            val start = withFrameMillis { it }
            while (true) withFrameMillis { now = it - start }
        }
    }
    Canvas(modifier) { drawCosmos(style, field, now.toFloat()) }
}

private class CosmosStyle(
    val light: Boolean,
    val bg: List<Color>,
    val stars: List<Color>,
    val heroes: List<Color>,
    val heroCount: Int,
    val milky: Boolean,
    val vignette: Float,
    /** x, y, radius (share of the longer side), color. */
    val nebulae: List<Nebula>,
)

private class Nebula(val x: Float, val y: Float, val r: Float, val c: Color)

private val NEBULA = CosmosStyle(
    light = false,
    bg = listOf(Color(0xFF0A0918), Color(0xFF070610), Color(0xFF040309)),
    stars = listOf(0xFFFFFFFF, 0xFFFFFFFF, 0xFFF4F7FF, 0xFFFFF2CF, 0xFFCFE0FF, 0xFFFFE6B0, 0xFFDFE8FF).map { Color(it) },
    heroes = listOf(Color(0xFFFFF6DC), Color(0xFFDFEAFF), Color(0xFFFFE3A6)),
    heroCount = 8, milky = true, vignette = 0.62f,
    nebulae = listOf(
        Nebula(0.22f, 0.30f, 0.70f, Color(96, 74, 210).copy(alpha = 0.16f)),
        Nebula(0.80f, 0.24f, 0.55f, Color(58, 120, 220).copy(alpha = 0.12f)),
        Nebula(0.66f, 0.82f, 0.75f, Color(70, 60, 180).copy(alpha = 0.13f)),
        Nebula(0.14f, 0.80f, 0.50f, Color(150, 80, 200).copy(alpha = 0.09f)),
    ),
)

private val DAWN = CosmosStyle(
    light = true,
    bg = listOf(Color(0xFFEAF1FB), Color(0xFFF5EEF1), Color(0xFFFDF6EA)),
    stars = listOf(0xFFC7B58A, 0xFFB3C0DA, 0xFFD6C496, 0xFFC5B4CD, 0xFFA8B5CB).map { Color(it) },
    heroes = listOf(Color(0xFFFFD7A0), Color(0xFFFFC8D6), Color(0xFFCDD9FF)),
    heroCount = 6, milky = true, vignette = 0f,
    nebulae = listOf(
        Nebula(0.24f, 0.28f, 0.74f, Color(255, 190, 140).copy(alpha = 0.22f)),
        Nebula(0.80f, 0.22f, 0.60f, Color(255, 214, 150).copy(alpha = 0.18f)),
        Nebula(0.68f, 0.80f, 0.80f, Color(236, 168, 196).copy(alpha = 0.17f)),
        Nebula(0.14f, 0.78f, 0.55f, Color(178, 170, 232).copy(alpha = 0.15f)),
    ),
)

private class Star(
    val x: Float, val y: Float, val r: Float, val c: Color,
    val tw: Float, val sp: Float, val drift: Float, val layer: Int,
)

private class Hero(val x: Float, val y: Float, val r: Float, val c: Color, val tw: Float, val sp: Float, val rays: Boolean)

private class StarField(val stars: List<Star>, val heroes: List<Hero>) {
    companion object {
        fun seed(s: CosmosStyle): StarField {
            val rnd = java.util.Random(0xA17A1L)
            val stars = List(150) { i ->
                val layer = i % 3
                Star(
                    x = rnd.nextFloat(), y = rnd.nextFloat(),
                    r = (layer + 1) * 0.32f + rnd.nextFloat() * 0.75f,
                    c = s.stars[rnd.nextInt(s.stars.size)],
                    tw = rnd.nextFloat() * 6.283f, sp = 0.5f + rnd.nextFloat() * 1.5f,
                    drift = (layer + 1) * 0.0013f, layer = layer,
                )
            }
            val heroes = List(s.heroCount) {
                Hero(
                    x = rnd.nextFloat(), y = rnd.nextFloat(), r = 1.1f + rnd.nextFloat() * 1.3f,
                    c = s.heroes[rnd.nextInt(s.heroes.size)], tw = rnd.nextFloat() * 6.283f,
                    sp = 0.4f + rnd.nextFloat() * 0.8f, rays = !s.light && rnd.nextFloat() > 0.4f,
                )
            }
            return StarField(stars, heroes)
        }
    }
}

/** Constellations in screen fractions: nodes and edges (same figures as on the PC). */
private val CONSTELLATIONS = listOf(
    listOf(0.16f to 0.20f, 0.24f to 0.30f, 0.33f to 0.26f, 0.40f to 0.36f, 0.30f to 0.44f) to
        listOf(0 to 1, 1 to 2, 2 to 3, 1 to 4),
    listOf(0.72f to 0.62f, 0.80f to 0.70f, 0.86f to 0.60f, 0.90f to 0.72f, 0.78f to 0.80f) to
        listOf(0 to 1, 1 to 2, 2 to 3, 1 to 4),
    listOf(0.60f to 0.16f, 0.68f to 0.24f, 0.76f to 0.18f, 0.72f to 0.32f) to listOf(0 to 1, 1 to 2, 1 to 3),
)

private fun DrawScope.drawCosmos(s: CosmosStyle, f: StarField, t: Float) {
    val w = size.width
    val h = size.height
    val dp = density
    // Stars are sized in dp so they look the same on every screen density.
    val blend = if (s.light) BlendMode.SrcOver else BlendMode.Plus

    drawRect(Brush.verticalGradient(0f to s.bg[0], 0.5f to s.bg[1], 1f to s.bg[2]))

    if (s.milky) {
        rotate(degrees = -28.6f, pivot = Offset(w / 2, h / 2)) {
            val band = if (s.light) Color(200, 170, 150) else Color(150, 140, 210)
            drawRect(
                Brush.verticalGradient(
                    0f to Color.Transparent, 0.5f to band.copy(alpha = if (s.light) 0.08f else 0.10f), 1f to Color.Transparent,
                    startY = h * 0.35f, endY = h * 0.65f,
                ),
                topLeft = Offset(-w * 0.5f, h * 0.3f), size = Size(w * 2f, h * 0.4f), blendMode = blend,
            )
        }
    }

    // Nebulae breathe in opposite phases — slow enough to feel alive, not busy.
    val pulse = 0.5f + 0.5f * sin(t * 0.00016f)
    s.nebulae.forEachIndexed { k, nb ->
        val c = Offset(nb.x * w, nb.y * h)
        val rr = nb.r * max(w, h) * (0.9f + 0.12f * (if (k % 2 == 1) pulse else 1 - pulse))
        drawCircle(
            Brush.radialGradient(0f to nb.c, 0.6f to nb.c.copy(alpha = 0.04f), 1f to Color.Transparent, center = c, radius = rr),
            radius = rr, center = c, blendMode = blend,
        )
    }

    CONSTELLATIONS.forEachIndexed { ci, (nodes, edges) ->
        val tw = 0.35f + 0.25f * sin(t * 0.0009f + ci)
        val line = if (s.light) Color(170, 140, 90) else Color(255, 224, 150)
        edges.forEach { (a, b) ->
            drawLine(
                line.copy(alpha = 0.09f + 0.06f * tw),
                Offset(nodes[a].first * w, nodes[a].second * h), Offset(nodes[b].first * w, nodes[b].second * h),
                strokeWidth = 0.7f * dp, blendMode = blend,
            )
        }
        val dot = if (s.light) Color(190, 160, 110) else Color(255, 236, 190)
        nodes.forEach { (nx, ny) ->
            drawCircle(dot.copy(alpha = (0.7f + 0.3f * tw).coerceAtMost(1f)), 1.4f * dp, Offset(nx * w, ny * h), blendMode = blend)
        }
    }

    // Parallax: nearer layers drift upward faster and wrap around.
    f.stars.forEach { st ->
        val y = wrap(st.y - st.drift * t * 0.012f)
        val a = 0.5f + 0.5f * (0.5f + 0.5f * sin(st.tw + t * 0.0008f * st.sp))
        val p = Offset(st.x * w, y * h)
        drawCircle(st.c.copy(alpha = a), st.r * dp, p, blendMode = blend)
        if (st.layer == 2) drawCircle(st.c.copy(alpha = a * 0.16f), st.r * 3.2f * dp, p, blendMode = blend)
    }

    f.heroes.forEach { hs ->
        val p = Offset(hs.x * w, hs.y * h)
        val a = 0.6f + 0.4f * (0.5f + 0.5f * sin(hs.tw + t * 0.0006f * hs.sp))
        val br = hs.r * 8f * dp
        drawCircle(
            Brush.radialGradient(0f to hs.c, 0.32f to Color.White.copy(alpha = 0.06f), 1f to Color.Transparent, center = p, radius = br),
            radius = br, center = p, alpha = a * 0.32f, blendMode = blend,
        )
        if (hs.rays) glint(p, hs.r * 8f * dp, 0.9f * dp, hs.c, a * 0.38f)
        drawCircle(if (s.light) hs.c else Color.White, hs.r * dp, p, alpha = a, blendMode = blend)
    }

    shootingStar(t, s.light)

    if (s.vignette > 0f) {
        drawRect(
            Brush.radialGradient(
                0f to Color.Transparent, 0.4f to Color.Transparent, 1f to Color.Black.copy(alpha = s.vignette),
                center = Offset(w * 0.5f, h * 0.45f), radius = max(w, h) * 0.75f,
            ),
        )
    }
}

/** Keeps a drifting star inside [-0.02, 1.02]: off the top, it comes back from the bottom. */
private fun wrap(v: Float): Float = (((v + 0.02f) % 1.04f) + 1.04f) % 1.04f - 0.02f

/** A four-ray diffraction glint centered at [p]. */
private fun DrawScope.glint(p: Offset, len: Float, width: Float, color: Color, alpha: Float) {
    translate(p.x, p.y) {
        for (k in 0..1) {
            rotate(degrees = k * 90f, pivot = Offset.Zero) {
                drawRect(
                    Brush.horizontalGradient(0f to Color.Transparent, 0.5f to color, 1f to Color.Transparent, startX = -len, endX = len),
                    topLeft = Offset(-len, -width / 2), size = Size(len * 2, width), alpha = alpha, blendMode = BlendMode.Plus,
                )
            }
        }
    }
}

/**
 * A rare shooting star: one every ~11–23 s, its path picked from the cycle number so it is random
 * across cycles yet stable within one — no state needed.
 */
private fun DrawScope.shootingStar(t: Float, light: Boolean) {
    if (light) return
    val period = 17_000f
    val cycle = (t / period).toInt()
    if (cycle == 0) return
    val rnd = java.util.Random(cycle * 7919L)
    val startAt = 2_600f + rnd.nextFloat() * 10_000f
    val life = (t - cycle * period) - startAt
    val max = 1_200f
    if (life < 0f || life > max) return
    val x = 0.03f + rnd.nextFloat() * 0.55f
    val y = 0.03f + rnd.nextFloat() * 0.32f
    val vx = 0.30f + rnd.nextFloat() * 0.24f
    val vy = 0.14f + rnd.nextFloat() * 0.14f
    val p = life / max
    val ease = if (p < 0.15f) p / 0.15f else if (p > 0.75f) (1 - p) / 0.25f else 1f
    val w = size.width
    val h = size.height
    val head = Offset((x + vx * p) * w, (y + vy * p) * h)
    val tail = Offset((x + vx * max(0f, p - 0.09f)) * w, (y + vy * max(0f, p - 0.09f)) * h)
    drawLine(
        Brush.linearGradient(listOf(Color(255, 240, 200, 0), Color(255, 244, 214).copy(alpha = 0.9f * ease)), start = tail, end = head),
        tail, head, strokeWidth = 2f * density, cap = StrokeCap.Round, blendMode = BlendMode.Plus,
    )
    glint(head, 10f * density, 1.2f * density, Color.White.copy(alpha = 0.9f), ease * 0.8f)
    drawCircle(Color.White.copy(alpha = ease), 1.7f * density, head)
}
