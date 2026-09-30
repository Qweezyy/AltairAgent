package com.localaiagent.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.localaiagent.app.R

/**
 * Geist, the PC app's default interface font (SIL OFL 1.1), bundled so the app looks the same
 * offline and on every phone. Static instances of the PC's variable font, Latin + Cyrillic merged.
 */
val Geist = FontFamily(
    Font(R.font.geist_regular, FontWeight.Normal),
    Font(R.font.geist_medium, FontWeight.Medium),
    Font(R.font.geist_semibold, FontWeight.SemiBold),
    Font(R.font.geist_bold, FontWeight.Bold),
)

/** JetBrains Mono for code, as on the PC (SIL OFL 1.1). */
val AppMono = FontFamily(
    Font(R.font.jetbrains_mono_regular, FontWeight.Normal),
    Font(R.font.jetbrains_mono_semibold, FontWeight.SemiBold),
)

// Headings are set slightly tight, as on the PC (letter-spacing -0.02…-0.035em); body text barely.
private val tight = (-0.02).em
private val body = (-0.005).em

/** Seven steps mapped onto the Material slots, so every MaterialTheme.typography.* call picks them up. */
val AppTypography = Typography(
    // The welcome title ("How can I help?").
    headlineMedium = TextStyle(fontFamily = Geist, fontWeight = FontWeight.SemiBold, fontSize = 32.sp, lineHeight = 38.sp, letterSpacing = (-0.035).em),
    headlineSmall = TextStyle(fontFamily = Geist, fontWeight = FontWeight.SemiBold, fontSize = 26.sp, lineHeight = 32.sp, letterSpacing = tight),
    titleLarge = TextStyle(fontFamily = Geist, fontWeight = FontWeight.SemiBold, fontSize = 20.sp, lineHeight = 26.sp, letterSpacing = tight),
    titleMedium = TextStyle(fontFamily = Geist, fontWeight = FontWeight.Medium, fontSize = 17.sp, lineHeight = 24.sp, letterSpacing = (-0.01).em),
    titleSmall = TextStyle(fontFamily = Geist, fontWeight = FontWeight.SemiBold, fontSize = 15.sp, lineHeight = 21.sp, letterSpacing = (-0.01).em),
    bodyLarge = TextStyle(fontFamily = Geist, fontWeight = FontWeight.Normal, fontSize = 16.sp, lineHeight = 26.sp, letterSpacing = body),
    bodyMedium = TextStyle(fontFamily = Geist, fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 23.sp, letterSpacing = body),
    bodySmall = TextStyle(fontFamily = Geist, fontWeight = FontWeight.Normal, fontSize = 13.5.sp, lineHeight = 20.sp, letterSpacing = body),
    labelLarge = TextStyle(fontFamily = Geist, fontWeight = FontWeight.Medium, fontSize = 14.sp, lineHeight = 20.sp, letterSpacing = body),
    labelMedium = TextStyle(fontFamily = Geist, fontWeight = FontWeight.Medium, fontSize = 13.sp, lineHeight = 18.sp, letterSpacing = body),
    labelSmall = TextStyle(fontFamily = Geist, fontWeight = FontWeight.Medium, fontSize = 11.sp, lineHeight = 15.sp, letterSpacing = 0.02.em),
)
