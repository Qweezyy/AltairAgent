@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.localaiagent.app.ui

import androidx.compose.ui.res.stringResource
import com.localaiagent.app.R

import android.media.MediaPlayer
import android.net.Uri
import android.widget.MediaController
import android.widget.VideoView
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.AudioFile
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Description
import androidx.compose.material.icons.rounded.PlayCircle
import androidx.compose.material.icons.rounded.TableChart
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import coil.compose.AsyncImage
import com.localaiagent.app.LibraryItem
import com.localaiagent.core.tools.readableFileText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/** Полноэкранная медиа-библиотека: сетка + просмотр/проигрывание по тапу. */
@Composable
fun LibraryScreen(items: List<LibraryItem>, onClose: () -> Unit) {
    var selected by remember { mutableStateOf<LibraryItem?>(null) }
    FullScreenDialog(onDismissRequest = onClose) {
        run {
            Column(Modifier.fillMaxSize()) {
                Row(header = stringResource(R.string.lib_title), onClose = onClose)
                if (items.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        AltiEmpty(stringResource(R.string.lib_empty))
                    }
                } else {
                    LazyVerticalGrid(
                        columns = GridCells.Adaptive(110.dp),
                        modifier = Modifier.fillMaxSize().padding(horizontal = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp),
                    ) {
                        items(items) { item -> LibraryCell(item) { selected = item } }
                    }
                }
            }
        }
    }
    selected?.let { AttachmentViewer(it) { selected = null } }
}

@Composable
private fun Row(header: String, onClose: () -> Unit) {
    androidx.compose.foundation.layout.Row(
        Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onClose) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, stringResource(R.string.action_back)) }
        Text(header, style = MaterialTheme.typography.titleLarge)
    }
}

@Composable
private fun LibraryCell(item: LibraryItem, onOpen: () -> Unit) {
    Box(
        Modifier.aspectRatio(1f).clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant).clickable { onOpen() },
        contentAlignment = Alignment.Center,
    ) {
        when (item.kind) {
            "image", "video" -> {
                val model: Any = if (item.path.startsWith("http")) item.path else File(item.path)
                AsyncImage(model = model, contentDescription = item.name,
                    modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                if (item.kind == "video") {
                    Icon(Icons.Rounded.PlayCircle, null, Modifier.size(38.dp), tint = Color.White.copy(alpha = 0.9f))
                }
            }
            "audio" -> IconWithName(Icons.Rounded.AudioFile, item.name)
            else -> IconWithName(if (isTable(item.name)) Icons.Rounded.TableChart else Icons.Rounded.Description, item.name)
        }
    }
}

@Composable
private fun IconWithName(icon: androidx.compose.ui.graphics.vector.ImageVector, name: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Icon(icon, null, Modifier.size(40.dp), tint = MaterialTheme.colorScheme.primary)
        Text(name, fontSize = 11.sp, maxLines = 2, color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp))
    }
}

private fun isTable(name: String) = name.substringAfterLast('.', "").lowercase() in setOf("csv", "tsv", "xlsx")

// ------------------------------------------------------------- просмотрщики

/** Полноэкранный просмотрщик одного вложения (переиспользуется в чате и библиотеке). */
@Composable
fun AttachmentViewer(item: LibraryItem, onClose: () -> Unit) {
    FullScreenDialog(onDismissRequest = onClose) {
        run {
            Column(Modifier.fillMaxSize()) {
                androidx.compose.foundation.layout.Row(
                    Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = onClose) { Icon(Icons.Rounded.Close, stringResource(R.string.action_close)) }
                    Text(item.name, style = MaterialTheme.typography.titleMedium, maxLines = 1)
                }
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    when (item.kind) {
                        "image" -> {
                            val model: Any = if (item.path.startsWith("http")) item.path else File(item.path)
                            AsyncImage(model, item.name, Modifier.fillMaxWidth(), contentScale = ContentScale.Fit)
                        }
                        "video" -> VideoPlayer(item.path)
                        "audio" -> AudioPlayer(item.path, item.name)
                        else -> FilePreview(item.path)
                    }
                }
            }
        }
    }
}

@Composable
private fun VideoPlayer(path: String) {
    AndroidView(
        factory = { ctx ->
            VideoView(ctx).apply {
                setVideoURI(if (path.startsWith("http")) Uri.parse(path) else Uri.fromFile(File(path)))
                val mc = MediaController(ctx); mc.setAnchorView(this); setMediaController(mc)
                setOnPreparedListener { it.isLooping = false; start() }
            }
        },
        modifier = Modifier.fillMaxWidth().height(320.dp),
    )
}

@Composable
private fun AudioPlayer(path: String, name: String) {
    var playing by remember { mutableStateOf(false) }
    val player = remember { MediaPlayer() }
    DisposableEffect(path) {
        runCatching {
            player.setDataSource(if (path.startsWith("http")) path else File(path).absolutePath)
            player.prepare()
        }
        player.setOnCompletionListener { playing = false }
        onDispose { runCatching { player.release() } }
    }
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Icons.Rounded.AudioFile, null, Modifier.size(72.dp), tint = MaterialTheme.colorScheme.primary)
        Text(name, modifier = Modifier.padding(8.dp))
        IconButton(onClick = {
            if (playing) { player.pause(); playing = false } else { runCatching { player.start(); playing = true } }
        }) {
            Icon(if (playing) Icons.Rounded.Close else Icons.Rounded.PlayCircle, "play/pause",
                Modifier.size(56.dp), tint = MaterialTheme.colorScheme.primary)
        }
    }
}

@Composable
private fun FilePreview(path: String) {
    var text by remember { mutableStateOf<String?>(null) }
    val cantShow = stringResource(R.string.lib_cant_show)
    LaunchedEffect(path) {
        text = withContext(Dispatchers.IO) { runCatching { readableFileText(File(path)) }.getOrNull() }
            ?: cantShow
    }
    val t = text
    when {
        t == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text(stringResource(R.string.lib_loading)) }
        // Таблицы — рисуем как таблицу (исходный вид), а не текстом.
        isTable(path.substringAfterLast('/')) -> {
            val rows = remember(path) { com.localaiagent.core.tools.parseTableRows(File(path)) }
            if (rows.isEmpty()) Text(t, fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                modifier = Modifier.padding(16.dp))
            else TableView(rows)
        }
        else -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
            MarkdownText(t, Modifier.fillMaxWidth())
        }
    }
}

/** Простой табличный просмотр: заголовок жирным, ячейки в колонках, скролл по обеим осям. */
@Composable
private fun TableView(rows: List<List<String>>) {
    val cols = rows.maxOf { it.size }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState())
            .horizontalScroll(rememberScrollState()).padding(8.dp),
    ) {
        rows.forEachIndexed { r, row ->
            androidx.compose.foundation.layout.Row {
                for (c in 0 until cols) {
                    Text(
                        row.getOrElse(c) { "" },
                        Modifier.width(130.dp).padding(horizontal = 8.dp, vertical = 6.dp),
                        fontWeight = if (r == 0) androidx.compose.ui.text.font.FontWeight.Bold else androidx.compose.ui.text.font.FontWeight.Normal,
                        fontSize = 13.sp,
                        maxLines = 2,
                        color = MaterialTheme.colorScheme.onBackground,
                    )
                }
            }
            androidx.compose.material3.HorizontalDivider(color = MaterialTheme.colorScheme.surfaceVariant)
        }
    }
}
