package com.localaiagent.app.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * Дизайн-токены: единые отступы, радиусы и семантические цвета вместо локальных чисел.
 * Акцентный цвет НЕ здесь — он выбирается пользователем (см. [ThemePrefs.accent]) и живёт
 * в `MaterialTheme.colorScheme.primary`.
 */
object Dims {
    // Сетка 4px
    val xs = 4.dp
    val sm = 8.dp
    val md = 12.dp
    val lg = 16.dp
    val xl = 20.dp
    val xxl = 24.dp
    val xxxl = 32.dp

    // Радиусы
    val rChip = 12.dp
    val rCard = 16.dp
    val rBubble = 20.dp
    val rSheet = 24.dp

    // Ритм ленты
    val messageGap = 20.dp
    val screenPad = 16.dp

    // Границы
    val hairline = 1.dp
}

/** Семантические цвета состояний — читаемы в обеих темах, вне зависимости от акцента. */
object Semantic {
    val success = Color(0xFF3FB37F)
    val warning = Color(0xFFE0A046)
    val danger = Color(0xFFE5484D)
}

/**
 * Бренд-токены Altair: «звезда в ночном небе» — тёплое золото на почти-чёрном.
 * Золотой градиент и свечение переиспользуются в анимациях (StarPulse, каретка-искра)
 * независимо от выбранного пользователем акцента.
 */
object Brand {
    val goldTop = Color(0xFFFFF3CD)   // верх градиента звезды
    val goldMid = Color(0xFFFFCD6C)   // середина
    val goldLow = Color(0xFFE39B2E)   // низ (глубокое золото)
    val glow = Color(0xFFFFBE4A)      // золотое свечение вокруг звезды
    val accent = 0xFFE9A23B           // золото Altair как акцент по умолчанию (ARGB)
}

/**
 * Шкала форм M3, собранная из радиусов [Dims]. Передаётся в `MaterialTheme`, чтобы
 * стандартные компоненты (диалоги, меню, чипы, кнопки) наследовали единые углы, а не
 * задавали их числами на каждом вызове.
 */
val AppShapes: Shapes = Shapes(
    extraSmall = RoundedCornerShape(Dims.sm),      // 8
    small = RoundedCornerShape(Dims.rChip),        // 12
    medium = RoundedCornerShape(Dims.rCard),       // 16
    large = RoundedCornerShape(Dims.rSheet),       // 24
    extraLarge = RoundedCornerShape(28.dp),
)
