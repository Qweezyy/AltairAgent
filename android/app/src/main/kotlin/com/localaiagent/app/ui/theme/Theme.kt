package com.localaiagent.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.luminance

/** Theme settings: the mode (system/light/dark/black/graphite/snow) and the accent colour (ARGB). */
data class ThemePrefs(
    val mode: String = "black", // Altair's default look
    val accent: Long = Brand.accent,
)

/** Accent choices in Settings. Altair gold first. */
val ACCENT_CHOICES: List<Long> = listOf(
    Brand.accent,
    0xFFD97757, // coral
    0xFFCC6B8E, // pink
    0xFF3B82F6, // blue
    0xFF22C55E, // green
    0xFF8B5CF6, // violet
    0xFF14B8A6, // teal
)

/** Every theme mode offered in Settings, in display order. */
val THEME_MODES = listOf("system", "light", "snow", "dark", "graphite", "black")

/**
 * The raw palette of one theme, the same values as the PC app (pc/static/redesign.css and the
 * palettes in app-fonts.css), so a user switching devices sees the same colours.
 */
private class Palette(
    val light: Boolean,
    val bg: Color,
    val rail: Color,
    val surface1: Color,
    val surface2: Color,
    val surface3: Color,
    val elevated: Color,
    val text1: Color,
    val text2: Color,
    val text3: Color,
)

private fun hsl(h: Float, s: Float, l: Float) = Color.hsl(h, s / 100f, l / 100f)

// Cosmic dark: a faint blue-violet night, warm parchment text.
private val COSMIC = Palette(
    light = false,
    bg = hsl(240f, 15f, 5f), rail = hsl(240f, 18f, 3f),
    surface1 = hsl(240f, 13f, 7.5f), surface2 = hsl(240f, 11f, 12f), surface3 = hsl(240f, 10f, 16f),
    elevated = hsl(240f, 12f, 10.5f),
    text1 = hsl(45f, 28f, 92f), text2 = hsl(44f, 12f, 66f), text3 = hsl(44f, 8f, 47f),
)

// Black: true black for OLED screens, neutral greys.
private val BLACK = Palette(
    light = false,
    bg = Color.Black, rail = Color.Black,
    surface1 = hsl(0f, 0f, 4.5f), surface2 = hsl(0f, 0f, 8.5f), surface3 = hsl(0f, 0f, 12f),
    elevated = hsl(0f, 0f, 6.5f),
    text1 = hsl(40f, 12f, 93f), text2 = hsl(40f, 5f, 66f), text3 = hsl(40f, 3f, 47f),
)

// Graphite: a neutral dark grey, softer on the eyes than black.
private val GRAPHITE = Palette(
    light = false,
    bg = hsl(220f, 6f, 13f), rail = hsl(220f, 7f, 10.5f),
    surface1 = hsl(220f, 6f, 15.5f), surface2 = hsl(220f, 6f, 19f), surface3 = hsl(220f, 6f, 23f),
    elevated = hsl(220f, 6f, 17.5f),
    text1 = hsl(220f, 10f, 92f), text2 = hsl(220f, 6f, 68f), text3 = hsl(220f, 5f, 50f),
)

// Light: warm paper.
private val PAPER = Palette(
    light = true,
    bg = hsl(48f, 22f, 96f), rail = hsl(48f, 20f, 93.5f),
    surface1 = hsl(48f, 26f, 98.4f), surface2 = hsl(48f, 16f, 93f), surface3 = hsl(48f, 14f, 89f),
    elevated = hsl(48f, 33f, 99.5f),
    text1 = hsl(40f, 13f, 16f), text2 = hsl(40f, 7f, 38f), text3 = hsl(40f, 6f, 52f),
)

// Snow: a clean neutral white.
private val SNOW = Palette(
    light = true,
    bg = Color.White, rail = hsl(220f, 14f, 97f),
    surface1 = Color.White, surface2 = hsl(220f, 14f, 96f), surface3 = hsl(220f, 12f, 92f),
    elevated = Color.White,
    text1 = hsl(220f, 15f, 13f), text2 = hsl(220f, 8f, 38f), text3 = hsl(220f, 6f, 54f),
)

/**
 * Tokens of the premium look that Material has no slot for (pc/static/premium.css): surfaces are
 * plates — a core with a hairline and a light from above, set in a faint outer tray.
 */
@Immutable
data class AltairTokens(
    val light: Boolean,
    val rail: Color,
    val core: Color,
    val hair: Color,
    val hairStrong: Color,
    val tray: Color,
    val trayEdge: Color,
    val sheen: Color,
    val accentTint: Color,
    val accentLine: Color,
    val accentHigh: Color,
    val accentLow: Color,
)

val LocalAltair = staticCompositionLocalOf {
    tokensFor(COSMIC, Color(Brand.accent))
}

private fun tokensFor(p: Palette, accent: Color) = AltairTokens(
    light = p.light,
    rail = p.rail,
    core = lerp(p.surface1, p.text1, 0.015f),
    hair = p.text1.copy(alpha = 0.08f),
    hairStrong = p.text1.copy(alpha = 0.14f),
    tray = p.text1.copy(alpha = 0.035f),
    trayEdge = p.text1.copy(alpha = 0.04f),
    sheen = if (p.light) Color.White.copy(alpha = 0.9f) else Color.White.copy(alpha = 0.07f),
    accentTint = accent.copy(alpha = 0.08f),
    accentLine = accent.copy(alpha = 0.5f),
    accentHigh = lerp(accent, Color.White, 0.22f),
    accentLow = lerp(accent, Color.Black, 0.08f),
)

/** Text on the accent: dark on a light accent (gold), white on a dark one — as on the PC. */
private fun onAccent(accent: Color) = if (accent.luminance() > 0.45f) hsl(240f, 35f, 6f) else Color.White

private fun scheme(p: Palette, accent: Color) = if (p.light) {
    lightColorScheme(
        primary = accent, onPrimary = onAccent(accent),
        background = p.bg, onBackground = p.text1,
        surface = p.surface1, onSurface = p.text1,
        surfaceVariant = p.surface2, onSurfaceVariant = p.text2,
        surfaceContainerLowest = p.bg, surfaceContainerLow = p.surface1, surfaceContainer = p.elevated,
        surfaceContainerHigh = p.surface2, surfaceContainerHighest = p.surface3,
        outline = p.text3, outlineVariant = lerp(p.bg, p.text1, 0.08f),
        error = hsl(4f, 66f, 47f),
    )
} else {
    darkColorScheme(
        primary = accent, onPrimary = onAccent(accent),
        background = p.bg, onBackground = p.text1,
        surface = p.surface1, onSurface = p.text1,
        surfaceVariant = p.surface2, onSurfaceVariant = p.text2,
        surfaceContainerLowest = p.bg, surfaceContainerLow = p.surface1, surfaceContainer = p.elevated,
        surfaceContainerHigh = p.surface2, surfaceContainerHighest = p.surface3,
        outline = p.text3, outlineVariant = lerp(p.bg, p.text1, 0.08f),
        error = hsl(6f, 68f, 63f),
    )
}

@Composable
fun LocalAIAgentTheme(
    prefs: ThemePrefs = ThemePrefs(),
    content: @Composable () -> Unit,
) {
    val palette = when (prefs.mode) {
        "black" -> BLACK
        "dark" -> COSMIC
        "graphite" -> GRAPHITE
        "light" -> PAPER
        "snow" -> SNOW
        else -> if (isSystemInDarkTheme()) COSMIC else PAPER
    }
    // The default gold is too bright on paper; the PC uses a deeper gold there.
    val brandAccent = prefs.accent == Brand.accent || prefs.accent == Brand.legacyAccent
    val accent = when {
        brandAccent && palette.light -> hsl(38f, 82f, 42f)
        brandAccent -> Color(Brand.accent)
        else -> Color(prefs.accent)
    }
    CompositionLocalProvider(LocalAltair provides tokensFor(palette, accent)) {
        MaterialTheme(
            colorScheme = scheme(palette, accent),
            typography = AppTypography,
            shapes = AppShapes,
            content = content,
        )
    }
}
