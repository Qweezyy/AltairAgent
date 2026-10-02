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
 * A page over the chat that covers the whole display. It is drawn in the activity window through
 * [LocalOverlayHost] (see Overlay.kt) rather than in a Dialog window, because Dialog windows stop short
 * of the navigation bar on some phones whatever flags they get. [padBars] = false for a Scaffold,
 * which pads the bars itself.
 */
@Composable
fun FullScreenDialog(onDismissRequest: () -> Unit, padBars: Boolean = true, content: @Composable () -> Unit) {
    val host = LocalOverlayHost.current
    if (host != null) {
        OverlayPage(host, onDismissRequest, padBars, content)
        return
    }
    // No host (previews): a plain full-width dialog.
    Dialog(onDismissRequest = onDismissRequest, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) { content() }
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
