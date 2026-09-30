package com.localaiagent.app.ui

import androidx.compose.ui.res.stringResource
import com.localaiagent.app.R

import android.graphics.Bitmap
import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Delete
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties

private class DrawStroke(val colorArgb: Int, val points: SnapshotStateList<Offset>)

private val PEN_COLORS = listOf(
    0xFFFFFFFF, 0xFF111111, 0xFFE5484D, 0xFF3B82F6, 0xFF22C55E, 0xFFF59E0B, 0xFF8B5CF6,
)

/**
 * #10: набросок пальцем. Пользователь рисует на холсте, «Готово» превращает рисунок
 * в изображение и прикрепляет к сообщению (как фото).
 */
@Composable
fun DrawCanvas(onDone: (Bitmap) -> Unit, onDismiss: () -> Unit) {
    val strokes = remember { mutableStateListOf<DrawStroke>() }
    var current by remember { mutableStateOf<DrawStroke?>(null) }
    var penColor by remember { mutableStateOf(Color(0xFF111111)) }
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }
    val strokeWidthPx = 10f

    FullScreenDialog(onDismissRequest = onDismiss) {
        run {
            Column(Modifier.fillMaxSize()) {
                Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onDismiss) { Icon(Icons.Rounded.Close, stringResource(R.string.action_close)) }
                    Text(stringResource(R.string.attach_draw), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    TextButton(
                        onClick = {
                            val bmp = renderToBitmap(strokes, canvasSize, strokeWidthPx)
                            if (bmp != null) onDone(bmp) else onDismiss()
                        },
                        enabled = strokes.isNotEmpty(),
                    ) { Text(stringResource(R.string.ask_done)) }
                }
                // Холст — белый фон, рисуем пальцем.
                Box(
                    Modifier.fillMaxWidth().weight(1f).padding(horizontal = 12.dp)
                        .clip(androidx.compose.foundation.shape.RoundedCornerShape(16.dp))
                        .background(Color.White),
                ) {
                    Canvas(
                        Modifier.fillMaxSize()
                            .onSizeChanged { canvasSize = it }
                            .pointerInput(Unit) {
                                detectDragGestures(
                                    onDragStart = { pos ->
                                        current = DrawStroke(penColor.toArgb(), mutableStateListOf(pos))
                                    },
                                    onDrag = { change, _ -> current?.points?.add(change.position) },
                                    onDragEnd = { current?.let { strokes.add(it) }; current = null },
                                    onDragCancel = { current?.let { strokes.add(it) }; current = null },
                                )
                            },
                    ) {
                        (strokes + listOfNotNull(current)).forEach { s ->
                            val pts = s.points
                            for (i in 1 until pts.size) {
                                drawLine(
                                    Color(s.colorArgb), pts[i - 1], pts[i],
                                    strokeWidth = strokeWidthPx, cap = StrokeCap.Round,
                                )
                            }
                            if (pts.size == 1) {
                                drawCircle(Color(s.colorArgb), strokeWidthPx / 2, pts[0])
                            }
                        }
                    }
                }
                // Палитра + очистить.
                Row(
                    Modifier.fillMaxWidth().padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    PEN_COLORS.forEach { c ->
                        val col = Color(c)
                        val sel = col == penColor
                        val size by animateDpAsState(if (sel) 34.dp else 28.dp, label = "swatch")
                        Box(
                            Modifier.size(size).clip(CircleShape).background(col)
                                .clickable { penColor = col },
                        )
                    }
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = { strokes.clear() }) {
                        Icon(Icons.Rounded.Delete, stringResource(R.string.board_clear), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

private fun renderToBitmap(strokes: List<DrawStroke>, size: IntSize, widthPx: Float): Bitmap? {
    if (size.width <= 0 || size.height <= 0 || strokes.isEmpty()) return null
    val bmp = Bitmap.createBitmap(size.width, size.height, Bitmap.Config.ARGB_8888)
    val canvas = android.graphics.Canvas(bmp)
    canvas.drawColor(android.graphics.Color.WHITE)
    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        strokeWidth = widthPx
    }
    for (s in strokes) {
        paint.color = s.colorArgb
        val pts = s.points
        for (i in 1 until pts.size) {
            canvas.drawLine(pts[i - 1].x, pts[i - 1].y, pts[i].x, pts[i].y, paint)
        }
        if (pts.size == 1) canvas.drawPoint(pts[0].x, pts[0].y, paint)
    }
    return bmp
}
