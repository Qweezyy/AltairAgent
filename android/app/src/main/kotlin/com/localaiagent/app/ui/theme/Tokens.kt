package com.localaiagent.app.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * Design tokens: shared spacing, radii and semantic colours instead of local numbers. The accent is
 * not here: the user picks it ([ThemePrefs.accent]) and it lives in `MaterialTheme.colorScheme.primary`.
 */
object Dims {
    // 4 dp grid
    val xs = 4.dp
    val sm = 8.dp
    val md = 12.dp
    val lg = 16.dp
    val xl = 20.dp
    val xxl = 24.dp
    val xxxl = 32.dp

    // Radii, following the PC's premium look (premium.css --r-*)
    val rChip = 12.dp
    val rCard = 18.dp
    val rBubble = 18.dp
    val rSheet = 22.dp

    // Feed rhythm
    val messageGap = 20.dp
    val screenPad = 16.dp

    // Lines
    val hairline = 1.dp
}

/** State colours, readable in every theme whatever the accent. */
object Semantic {
    val success = Color(0xFF5BC08A)
    val warning = Color(0xFFE0A046)
    val danger = Color(0xFFE5675C)
}

/**
 * Altair brand tokens: a star in the night sky, warm gold on near-black. The gold gradient and the
 * glow are reused by animations (StarPulse, the spark caret) whatever accent the user picks.
 */
object Brand {
    val goldTop = Color(0xFFFFF3CD)   // top of the star gradient
    val goldMid = Color(0xFFFFCD6C)   // middle
    val goldLow = Color(0xFFE39B2E)   // bottom (deep gold)
    val glow = Color(0xFFFFBE4A)      // golden glow around the star
    val accent = 0xFFFFC247           // Altair gold, the default accent (PC --accent: hsl(40 100% 64%))
    /** The default accent of builds before the PC look; saved themes carrying it follow the new gold. */
    const val legacyAccent = 0xFFE9A23B
}

/**
 * The Material 3 shape scale built from [Dims], passed to `MaterialTheme` so standard components
 * (dialogs, menus, chips, buttons) share the same corners instead of numbers at every call.
 */
val AppShapes: Shapes = Shapes(
    extraSmall = RoundedCornerShape(Dims.sm),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(Dims.rCard),
    extraLarge = RoundedCornerShape(Dims.rSheet),
)
