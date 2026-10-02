package com.localaiagent.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AudioFile
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.TableChart
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.localaiagent.app.R
import com.localaiagent.app.ui.theme.LocalAltair

/** Photos and videos are shown as pictures; everything else as a compact card. */
fun isVisualKind(kind: String?) = kind == "image" || kind == "video"

private fun fileIcon(kind: String?, name: String): ImageVector = when {
    kind == "audio" -> Icons.Outlined.AudioFile
    name.substringAfterLast('.', "").lowercase() in setOf("csv", "tsv", "xlsx", "xls") -> Icons.Outlined.TableChart
    else -> Icons.Outlined.Description
}

/** The video mark over a frame: a play arrow in a dark round badge. */
@Composable
private fun PlayBadge(size: Int = 40) {
    Box(
        Modifier.size(size.dp).clip(CircleShape).background(Color.Black.copy(alpha = 0.5f)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(Icons.Rounded.PlayArrow, null, Modifier.size((size * 0.6f).dp), tint = Color.White)
    }
}

/** The round "remove" badge in the corner of a pending attachment. */
@Composable
private fun RemoveBadge(onRemove: () -> Unit, modifier: Modifier = Modifier) {
    Box(
        modifier.padding(6.dp).size(26.dp).clip(CircleShape)
            .background(Color.Black.copy(alpha = 0.55f)).clickable(onClick = onRemove),
        contentAlignment = Alignment.Center,
    ) {
        Icon(Icons.Rounded.Close, stringResource(R.string.remove), Modifier.size(16.dp), tint = Color.White)
    }
}

/**
 * The attachment waiting in the composer, as in ChatGPT: a photo or a video is a tall rounded tile
 * with a remove badge, a file or audio a compact card. Tapping opens it.
 */
@Composable
fun ComposerAttachment(path: String, name: String, kind: String, onOpen: () -> Unit, onRemove: () -> Unit) {
    val alt = LocalAltair.current
    if (isVisualKind(kind)) {
        Box(
            Modifier.size(width = 104.dp, height = 132.dp).clip(RoundedCornerShape(16.dp))
                .border(1.dp, alt.hair, RoundedCornerShape(16.dp)).clickable(onClick = onOpen),
        ) {
            AsyncImage(
                model = java.io.File(path), contentDescription = name,
                modifier = Modifier.matchParentSize(), contentScale = ContentScale.Crop,
            )
            if (kind == "video") Box(Modifier.matchParentSize(), contentAlignment = Alignment.Center) { PlayBadge(34) }
            RemoveBadge(onRemove, Modifier.align(Alignment.TopEnd))
        }
    } else {
        Row(
            Modifier.widthIn(max = 240.dp).clip(RoundedCornerShape(14.dp)).background(alt.core)
                .border(1.dp, alt.hair, RoundedCornerShape(14.dp))
                .padding(start = 12.dp, top = 10.dp, bottom = 10.dp, end = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(fileIcon(kind, name), null, Modifier.size(26.dp), tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(10.dp))
            Text(
                name, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false),
            )
            Box(
                Modifier.padding(start = 6.dp).size(28.dp).clip(CircleShape).clickable(onClick = onRemove),
                contentAlignment = Alignment.Center,
            ) {
                Icon(Icons.Rounded.Close, stringResource(R.string.remove), Modifier.size(16.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/**
 * A photo or video the user sent, as in ChatGPT: right-aligned, keeping its proportions, at most
 * ~two thirds of the width and a comfortable height, rounded; tapping opens the viewer.
 */
@Composable
fun SentMedia(path: String, name: String, kind: String, onOpen: () -> Unit) {
    val model: Any = if (path.startsWith("http")) path else java.io.File(path)
    Box(
        Modifier.widthIn(max = 250.dp).heightIn(max = 340.dp).clip(RoundedCornerShape(18.dp)).clickable(onClick = onOpen),
        contentAlignment = Alignment.Center,
    ) {
        AsyncImage(model = model, contentDescription = name, contentScale = ContentScale.Fit)
        if (kind == "video") PlayBadge()
    }
}

/** A file or audio the user sent: a smaller card than a picture. */
@Composable
fun SentFileCard(name: String, kind: String, onOpen: () -> Unit) {
    val alt = LocalAltair.current
    Row(
        Modifier.widthIn(max = 260.dp).clip(RoundedCornerShape(16.dp)).background(alt.core)
            .border(1.dp, alt.hair, RoundedCornerShape(16.dp)).clickable(onClick = onOpen)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(fileIcon(kind, name), null, Modifier.size(28.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(10.dp))
        Text(name, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
