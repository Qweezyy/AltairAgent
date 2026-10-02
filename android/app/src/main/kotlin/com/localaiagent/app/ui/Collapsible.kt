package com.localaiagent.app.ui

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.KeyboardArrowDown
import androidx.compose.material.icons.rounded.KeyboardArrowUp
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.localaiagent.app.R
import com.localaiagent.app.ui.theme.SpringEasing

/**
 * Folds content taller than [collapsedHeight] to that height with a soft fade, and adds a
 * "Show more" / "Show less" toggle. Content that fits is shown as is, with no toggle. The state is
 * kept per [key] (a message id) across scrolling.
 */
@Composable
fun Collapsible(
    key: String,
    collapsedHeight: Dp,
    fadeColor: Color,
    enabled: Boolean = true,
    alignEnd: Boolean = false,
    content: @Composable () -> Unit,
) {
    if (!enabled) { content(); return }
    var expanded by rememberSaveable(key) { mutableStateOf(false) }
    var overflows by remember { mutableStateOf(false) }
    Column(horizontalAlignment = if (alignEnd) Alignment.End else Alignment.Start) {
        Box(Modifier.animateContentSize(tween(320, easing = SpringEasing)).clipToBounds()) {
            Layout(content) { measurables, constraints ->
                val placeable = measurables.first().measure(constraints.copy(maxHeight = androidx.compose.ui.unit.Constraints.Infinity))
                val cap = collapsedHeight.roundToPx()
                // A little slack, so a text barely over the limit is not folded by a line or two.
                val tooTall = placeable.height > cap + 48.dp.roundToPx()
                if (tooTall != overflows) overflows = tooTall
                val h = if (tooTall && !expanded) cap else placeable.height
                layout(placeable.width, h) { placeable.place(0, 0) }
            }
            if (overflows && !expanded) {
                Box(
                    Modifier.matchParentSize(),
                    contentAlignment = Alignment.BottomCenter,
                ) {
                    Box(
                        Modifier.fillMaxWidth().height(56.dp)
                            .background(Brush.verticalGradient(listOf(fadeColor.copy(alpha = 0f), fadeColor))),
                    )
                }
            }
        }
        if (overflows) {
            Row(
                Modifier.padding(top = 4.dp).clip(RoundedCornerShape(50)).clickable { expanded = !expanded }
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    stringResource(if (expanded) R.string.show_less else R.string.show_more),
                    style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.width(4.dp))
                Icon(
                    if (expanded) Icons.Rounded.KeyboardArrowUp else Icons.Rounded.KeyboardArrowDown, null,
                    Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}
