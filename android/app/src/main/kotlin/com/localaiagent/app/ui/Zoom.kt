package com.localaiagent.app.ui

import android.graphics.SurfaceTexture
import android.media.MediaPlayer
import android.net.Uri
import android.view.Surface
import android.view.TextureView
import androidx.compose.animation.core.Animatable
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File

private const val MAX_ZOOM = 6f
private const val DOUBLE_TAP_ZOOM = 2.5f

/**
 * Pinch to zoom (up to 6x), drag to pan within the picture, double-tap to zoom in on a spot or back
 * out. At 1x one-finger drags are left alone, so nothing else on the page loses its gestures.
 */
@Composable
fun ZoomBox(modifier: Modifier = Modifier, content: @Composable BoxScope.() -> Unit) {
    var size by remember { mutableStateOf(IntSize.Zero) }
    val scale = remember { Animatable(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    val scope = rememberCoroutineScope()

    fun clamp(o: Offset, s: Float): Offset {
        val maxX = (size.width * (s - 1f)) / 2f
        val maxY = (size.height * (s - 1f)) / 2f
        return Offset(o.x.coerceIn(-maxX, maxX), o.y.coerceIn(-maxY, maxY))
    }

    Box(
        modifier
            .clipToBounds()
            .onSizeChanged { size = it }
            .pointerInput(Unit) {
                detectTapGestures(onDoubleTap = { tap ->
                    scope.launch {
                        if (scale.value > 1.05f) {
                            offset = Offset.Zero
                            scale.animateTo(1f)
                        } else {
                            // Zoom towards the tapped point.
                            val center = Offset(size.width / 2f, size.height / 2f)
                            offset = clamp((center - tap) * (DOUBLE_TAP_ZOOM - 1f), DOUBLE_TAP_ZOOM)
                            scale.animateTo(DOUBLE_TAP_ZOOM)
                        }
                    }
                })
            }
            .pointerInput(Unit) {
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    do {
                        val event = awaitPointerEvent()
                        val pointers = event.changes.count { it.pressed }
                        val zoom = event.calculateZoom()
                        val pan = event.calculatePan()
                        // Pinch always; a one-finger pan only while zoomed in.
                        if (pointers >= 2 || scale.value > 1.01f) {
                            val newScale = (scale.value * zoom).coerceIn(1f, MAX_ZOOM)
                            val centroid = event.calculateCentroid()
                            val center = Offset(size.width / 2f, size.height / 2f)
                            val focal = if (centroid == Offset.Unspecified) center else centroid
                            val scaled = (offset + (center - focal)) * (newScale / scale.value) - (center - focal) + pan
                            scope.launch { scale.snapTo(newScale) }
                            offset = clamp(if (newScale <= 1.01f) Offset.Zero else scaled, newScale)
                            event.changes.forEach { if (it.positionChanged()) it.consume() }
                        }
                    } while (event.changes.any { it.pressed })
                }
            }
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
                translationX = offset.x
                translationY = offset.y
            },
        contentAlignment = Alignment.Center,
        content = content,
    )
}

/**
 * A video on a TextureView, so it can be zoomed like a photo (a VideoView draws on its own surface,
 * which no scaling reaches), with play/pause and a seek bar.
 */
@Composable
fun ZoomableVideo(path: String, modifier: Modifier = Modifier) {
    val player = remember { MediaPlayer() }
    var prepared by remember { mutableStateOf(false) }
    var playing by remember { mutableStateOf(false) }
    var duration by remember { mutableIntStateOf(0) }
    var position by remember { mutableFloatStateOf(0f) }
    var ratio by remember { mutableFloatStateOf(16f / 9f) }
    var dragging by remember { mutableStateOf(false) }

    DisposableEffect(path) {
        onDispose { runCatching { player.release() } }
    }
    LaunchedEffect(playing, prepared) {
        while (playing && prepared) {
            if (!dragging) position = runCatching { player.currentPosition.toFloat() }.getOrDefault(position)
            delay(200)
        }
    }

    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        ZoomBox(Modifier.fillMaxWidth().weight(1f)) {
            AndroidView(
                factory = { ctx ->
                    TextureView(ctx).apply {
                        surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                            override fun onSurfaceTextureAvailable(st: SurfaceTexture, w: Int, h: Int) {
                                runCatching {
                                    player.setSurface(Surface(st))
                                    if (path.startsWith("http")) player.setDataSource(path)
                                    else player.setDataSource(ctx, Uri.fromFile(File(path)))
                                    player.setOnPreparedListener { mp ->
                                        prepared = true
                                        duration = mp.duration
                                        if (mp.videoWidth > 0 && mp.videoHeight > 0) ratio = mp.videoWidth.toFloat() / mp.videoHeight
                                        mp.start()
                                        playing = true
                                    }
                                    player.setOnCompletionListener { playing = false }
                                    player.prepareAsync()
                                }
                            }
                            override fun onSurfaceTextureSizeChanged(st: SurfaceTexture, w: Int, h: Int) {}
                            override fun onSurfaceTextureDestroyed(st: SurfaceTexture) = true
                            override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth().aspectRatio(ratio),
            )
        }
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(
                onClick = {
                    if (!prepared) return@IconButton
                    if (playing) { player.pause(); playing = false } else { player.start(); playing = true }
                },
                modifier = Modifier.size(44.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceContainerHigh),
            ) {
                Icon(if (playing) Icons.Rounded.Pause else Icons.Rounded.PlayArrow, null, tint = MaterialTheme.colorScheme.onSurface)
            }
            Slider(
                value = if (duration > 0) position / duration else 0f,
                onValueChange = { dragging = true; position = it * duration },
                onValueChangeFinished = {
                    dragging = false
                    if (prepared) runCatching { player.seekTo(position.toInt()) }
                },
                modifier = Modifier.weight(1f).padding(horizontal = 10.dp),
            )
            Text(fmt(position.toInt()) + " / " + fmt(duration), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun fmt(ms: Int): String {
    val s = ms / 1000
    return "%d:%02d".format(s / 60, s % 60)
}
