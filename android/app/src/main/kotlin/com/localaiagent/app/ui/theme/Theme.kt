package com.localaiagent.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Настройки темы: режим (system/light/dark/black) и акцентный цвет (ARGB). */
data class ThemePrefs(
    val mode: String = "black", // «Чёрная» — тема бренда Altair по умолчанию
    val accent: Long = Brand.accent, // золото Altair по умолчанию
)

/** Палитра акцентов для выбора в настройках. Первым — золото Altair (бренд). */
val ACCENT_CHOICES: List<Long> = listOf(
    Brand.accent, // золото Altair (бренд)
    0xFFD97757, // коралл (Claude)
    0xFFCC6B8E, // розовый (как ChatGPT)
    0xFF3B82F6, // синий
    0xFF22C55E, // зелёный
    0xFF8B5CF6, // фиолетовый
    0xFF14B8A6, // бирюза
)

// Тёмная — почти чёрный, в духе ChatGPT: глубина через «освещение» поверхностей,
// а не тени. Светлая — тёплая «бумага» Claude. Акцент = выбор пользователя (primary).
private fun darkScheme(accent: Color) = darkColorScheme(
    primary = accent,
    onPrimary = Color(0xFFFFFFFF),
    background = Color(0xFF0D0D0D),
    surface = Color(0xFF161618),
    surfaceVariant = Color(0xFF1E1E21),
    surfaceContainerLowest = Color(0xFF0F0F10),
    surfaceContainerLow = Color(0xFF161618),
    surfaceContainer = Color(0xFF1E1E21),
    surfaceContainerHigh = Color(0xFF26262A),
    surfaceContainerHighest = Color(0xFF2E2E33),
    onBackground = Color(0xFFECECEC),
    onSurface = Color(0xFFECECEC),
    onSurfaceVariant = Color(0xFFA6A6AD),
    outline = Color(0xFF6E6E76),
    outlineVariant = Color(0xFF2A2A2E), // hairline-разделители
)

// Чёрная (AMOLED): та же тёмная палитра, но фон и «нижние» поверхности — чистый чёрный
// (#000000), чтобы на OLED пиксели гасли. Карточки/поднятые слои чуть выше нуля, чтобы
// оставаться различимыми. Текст/акцент — как в тёмной.
private fun blackScheme(accent: Color) = darkColorScheme(
    primary = accent,
    onPrimary = Color(0xFFFFFFFF),
    background = Color(0xFF000000),
    surface = Color(0xFF000000),
    surfaceVariant = Color(0xFF141416),
    surfaceContainerLowest = Color(0xFF000000),
    surfaceContainerLow = Color(0xFF0A0A0B),
    surfaceContainer = Color(0xFF121214),
    surfaceContainerHigh = Color(0xFF1B1B1E),
    surfaceContainerHighest = Color(0xFF242428),
    onBackground = Color(0xFFECECEC),
    onSurface = Color(0xFFECECEC),
    onSurfaceVariant = Color(0xFFA6A6AD),
    outline = Color(0xFF6E6E76),
    outlineVariant = Color(0xFF1E1E22), // hairline-разделители чуть ярче фона
)

private fun lightScheme(accent: Color) = lightColorScheme(
    primary = accent,
    onPrimary = Color(0xFFFFFFFF),
    background = Color(0xFFFAF9F5),
    surface = Color(0xFFFFFFFF),
    surfaceVariant = Color(0xFFF0EEE6),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF7F5EF),
    surfaceContainer = Color(0xFFF0EEE6),
    surfaceContainerHigh = Color(0xFFECEAE1),
    surfaceContainerHighest = Color(0xFFE6E3D9),
    onBackground = Color(0xFF1A1A1A),
    onSurface = Color(0xFF1A1A1A),
    onSurfaceVariant = Color(0xFF6B6B70),
    outline = Color(0xFF8A877E),
    outlineVariant = Color(0xFFE0DDD3),
)

@Composable
fun LocalAIAgentTheme(
    prefs: ThemePrefs = ThemePrefs(),
    content: @Composable () -> Unit,
) {
    val accent = Color(prefs.accent)
    val scheme = when (prefs.mode) {
        "black" -> blackScheme(accent)
        "dark" -> darkScheme(accent)
        "light" -> lightScheme(accent)
        else -> if (isSystemInDarkTheme()) darkScheme(accent) else lightScheme(accent)
    }
    MaterialTheme(
        colorScheme = scheme,
        typography = AppTypography,
        shapes = AppShapes,
        content = content,
    )
}
