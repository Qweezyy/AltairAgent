package com.localaiagent.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import com.localaiagent.app.ui.theme.SpringEasing

/**
 * Full-screen pages drawn inside the activity window instead of in Dialog windows.
 *
 * A Compose Dialog is a separate window, and how far it may extend under the status and navigation
 * bars is decided by the platform and the OEM: `decorFitsSystemWindows` is documented to help but
 * does not on several builds (issuetracker.google.com/373093006), and MIUI/HyperOS kept a strip above
 * the navigation bar whatever flags we set. The activity window is already edge-to-edge, so pages
 * drawn here cover the whole display on every phone, with the bars as padding.
 */
class OverlayHost {
    internal val entries = mutableStateListOf<OverlayEntry>()
}

class OverlayEntry {
    var content by mutableStateOf<@Composable () -> Unit>({})
    var onBack by mutableStateOf({})
    var padBars by mutableStateOf(true)
}

val LocalOverlayHost = staticCompositionLocalOf<OverlayHost?> { null }

/** The chosen text size and its setter, for Settings → Appearance. */
/** Line spacing of the model's answers and its setter (Settings → Appearance). */
val LocalAnswerSpacing = androidx.compose.runtime.compositionLocalOf<Pair<Float, (Float) -> Unit>> {
    com.localaiagent.app.data.ANSWER_SPACING_DEFAULT to {}
}

val LocalUiScale = androidx.compose.runtime.compositionLocalOf<Pair<Float, (Float) -> Unit>> { 1f to {} }

/** Re-checks the PC bridge right away (tapping the presence chip). */
val LocalRefreshPresence = staticCompositionLocalOf<() -> Unit> { {} }

/** Draws the open pages, newest on top; Back closes the top one. Place it last in the root Box. */
@Composable
fun OverlayLayer(host: OverlayHost) {
    val top = host.entries.lastOrNull()
    host.entries.forEach { entry ->
        key(entry) {
            BackHandler(enabled = entry === top) { entry.onBack() }
            val appear = remember { Animatable(0f) }
            LaunchedEffect(Unit) { appear.animateTo(1f, tween(260, easing = SpringEasing)) }
            Surface(
                Modifier
                    .fillMaxSize()
                    .graphicsLayer {
                        alpha = appear.value
                        translationY = (1f - appear.value) * 24.dp.toPx()
                    }
                    // Swallow touches so nothing below the page reacts to them.
                    .clickable(remember { MutableInteractionSource() }, indication = null) {},
                color = MaterialTheme.colorScheme.background,
            ) {
                if (entry.padBars) {
                    Box(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.systemBars.union(WindowInsets.ime))) {
                        entry.content()
                    }
                } else {
                    entry.content()
                }
            }
        }
    }
}

/** Registers a page in the nearest [OverlayHost] for as long as the caller composes it. */
@Composable
internal fun OverlayPage(host: OverlayHost, onBack: () -> Unit, padBars: Boolean, content: @Composable () -> Unit) {
    val entry = remember { OverlayEntry() }
    SideEffect {
        entry.content = content
        entry.onBack = onBack
        entry.padBars = padBars
    }
    DisposableEffect(entry) {
        host.entries.add(entry)
        onDispose { host.entries.remove(entry) }
    }
}
