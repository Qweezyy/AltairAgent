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
        // A large, plainly visible remove button: 32 dp in a 44 dp touch area.
        modifier.size(44.dp).clip(CircleShape).clickable(onClick = onRemove).padding(6.dp)
            .clip(CircleShape).background(Color.Black.copy(alpha = 0.65f))
            .border(1.dp, Color.White.copy(alpha = 0.35f), CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Icon(Icons.Rounded.Close, stringResource(R.string.remove), Modifier.size(18.dp), tint = Color.White)
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
                model = if (path.startsWith("content:")) android.net.Uri.parse(path) else java.io.File(path),
                contentDescription = name, modifier = Modifier.matchParentSize(), contentScale = ContentScale.Crop,
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


/**
 * Everything attached to a sent message, right-aligned like ChatGPT: a single photo or video keeps its
 * proportions; several are square tiles that wrap into rows; files and audio are cards below them.
 */
@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun SentAttachments(items: List<com.localaiagent.app.LibraryItem>, onOpen: (com.localaiagent.app.LibraryItem) -> Unit) {
    val visual = items.filter { isVisualKind(it.kind) }
    val files = items.filterNot { isVisualKind(it.kind) }
    androidx.compose.foundation.layout.Column(horizontalAlignment = Alignment.End) {
        if (visual.size == 1) {
            val v = visual[0]
            SentMedia(v.path, v.name, v.kind) { onOpen(v) }
        } else if (visual.isNotEmpty()) {
            androidx.compose.foundation.layout.FlowRow(
                modifier = Modifier.widthIn(max = 300.dp),
                horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(6.dp, Alignment.End),
                verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(6.dp),
            ) {
                visual.forEach { v ->
                    Box(
                        Modifier.size(96.dp).clip(RoundedCornerShape(14.dp)).clickable { onOpen(v) },
                        contentAlignment = Alignment.Center,
                    ) {
                        val model: Any = if (v.path.startsWith("http")) v.path else java.io.File(v.path)
                        AsyncImage(model = model, contentDescription = v.name, modifier = Modifier.matchParentSize(), contentScale = ContentScale.Crop)
                        if (v.kind == "video") PlayBadge(30)
                    }
                }
            }
        }
        files.forEachIndexed { i, f ->
            if (i > 0 || visual.isNotEmpty()) Spacer(Modifier.size(6.dp))
            SentFileCard(f.name, f.kind) { onOpen(f) }
        }
    }
}
