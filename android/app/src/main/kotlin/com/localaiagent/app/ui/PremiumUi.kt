@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.localaiagent.app.ui

import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.exclude
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.Box
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.draw.clip
import com.localaiagent.app.ui.theme.plate
import androidx.compose.ui.res.stringResource
import com.localaiagent.app.R

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ChevronRight
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.localaiagent.app.ui.theme.Dims

/**
 * Полноэкранное окно (вместо «острова»-диалога): фон приложения на весь экран,
 * прозрачная верхняя панель с заголовком и кнопкой «назад».
 */
/**
 * A screen shown over the chat that covers the whole display, system bars included. A plain full-width
 * Dialog stops at the status and navigation bars, so strips of the chat showed above and below it;
 * here the dialog window is edge-to-edge with transparent bars, the background runs under them, and
 * [content] gets the bar insets as padding ([padBars] = false for a Scaffold, which pads itself).
 */
@Composable
fun FullScreenDialog(onDismissRequest: () -> Unit, padBars: Boolean = true, content: @Composable () -> Unit) {
    Dialog(
        onDismissRequest = onDismissRequest,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        val view = androidx.compose.ui.platform.LocalView.current
        val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
        androidx.compose.runtime.SideEffect {
            val window = (view.parent as? androidx.compose.ui.window.DialogWindowProvider)?.window ?: return@SideEffect
            window.setLayout(android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.ViewGroup.LayoutParams.MATCH_PARENT)
            window.setBackgroundDrawable(android.graphics.drawable.ColorDrawable(android.graphics.Color.TRANSPARENT))
            window.setDimAmount(0f)
            // Draw under the status bar and the camera cutout too, not only under the navigation bar.
            androidx.core.view.WindowCompat.setDecorFitsSystemWindows(window, false)
            if (android.os.Build.VERSION.SDK_INT >= 28) {
                window.attributes = window.attributes.apply {
                    layoutInDisplayCutoutMode = if (android.os.Build.VERSION.SDK_INT >= 30)
                        android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                    else android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                }
            }
            // Without these the dialog's decor still pads its top by the status bar height.
            window.addFlags(
                android.view.WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                    android.view.WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            )
            window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            // With NO_LIMITS, MATCH_PARENT stops above the navigation bar: size the window to the
            // whole display explicitly and pin it to the top.
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                // Dialog windows fit the system bars by default; some OEM builds (MIUI/HyperOS) then
                // shrink the frame above the navigation bar whatever the flags say.
                window.attributes = window.attributes.apply {
                    fitInsetsTypes = 0
                    fitInsetsSides = 0
                    isFitInsetsIgnoringVisibility = true
                }
            }
            val fullHeight = if (android.os.Build.VERSION.SDK_INT >= 30) {
                window.windowManager.maximumWindowMetrics.bounds.height()
            } else {
                @Suppress("DEPRECATION")
                android.util.DisplayMetrics().also { window.windowManager.defaultDisplay.getRealMetrics(it) }.heightPixels
            }
            window.setGravity(android.view.Gravity.TOP)
            window.setLayout(android.view.ViewGroup.LayoutParams.MATCH_PARENT, fullHeight)
            @Suppress("DEPRECATION")
            window.statusBarColor = android.graphics.Color.TRANSPARENT
            @Suppress("DEPRECATION")
            window.navigationBarColor = android.graphics.Color.TRANSPARENT
            if (android.os.Build.VERSION.SDK_INT >= 29) window.isNavigationBarContrastEnforced = false
            androidx.core.view.WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = !dark
                isAppearanceLightNavigationBars = !dark
            }
        }
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            if (padBars) {
                Box(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.systemBars.union(WindowInsets.ime))) { content() }
            } else {
                content()
            }
        }
    }
}

@Composable
fun FullScreenScaffold(
    title: String,
    onBack: () -> Unit,
    actions: @Composable RowScope.() -> Unit = {},
    content: @Composable (PaddingValues) -> Unit,
) {
    FullScreenDialog(onDismissRequest = onBack, padBars = false) {
        // The Scaffold pads the system bars; the keyboard is added on top of the navigation bar.
        Box(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.ime.exclude(WindowInsets.navigationBars))) {
            Scaffold(
                containerColor = Color.Transparent,
                topBar = {
                    TopAppBar(
                        title = { Text(title, style = MaterialTheme.typography.titleLarge) },
                        navigationIcon = {
                            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, stringResource(R.string.action_back)) }
                        },
                        actions = actions,
                        colors = TopAppBarDefaults.topAppBarColors(
                            containerColor = Color.Transparent,
                            scrolledContainerColor = Color.Transparent,
                        ),
                    )
                },
                content = content,
            )
        }
    }
}

/** Прокручиваемая колонка контента с полями экрана. */
@Composable
fun SettingsScroll(pad: PaddingValues, content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState())
            .padding(horizontal = Dims.screenPad, vertical = Dims.sm),
        verticalArrangement = Arrangement.spacedBy(Dims.md),
        content = content,
    )
}

/**
 * A group of settings: an uppercase label over a plate, like the PC settings cards (premium.css
 * `.prov-card`). The plate's ring is drawn outside, hence the small side inset.
 */
@Composable
fun SettingsGroup(title: String? = null, content: @Composable ColumnScope.() -> Unit) {
    if (title != null) {
        Text(
            title.uppercase(), style = MaterialTheme.typography.labelSmall,
            fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold,
            letterSpacing = androidx.compose.ui.unit.TextUnit(0.1f, androidx.compose.ui.unit.TextUnitType.Em),
            color = MaterialTheme.colorScheme.outline,
            modifier = Modifier.padding(start = Dims.md, top = Dims.md, bottom = Dims.xs),
        )
    }
    Column(
        Modifier.padding(horizontal = 4.dp).fillMaxWidth()
            .plate(Dims.rCard)
            .clip(RoundedCornerShape(Dims.rCard))
            .padding(vertical = Dims.xs),
        content = content,
    )
}

/** A row that opens a subsection: icon · title · subtitle · chevron. */
@Composable
fun SettingRow(icon: ImageVector, title: String, subtitle: String? = null, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick)
            .padding(horizontal = Dims.lg, vertical = Dims.md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(Dims.lg))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurface)
            if (subtitle != null) {
                Text(subtitle, style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            }
        }
        Icon(Icons.Rounded.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
