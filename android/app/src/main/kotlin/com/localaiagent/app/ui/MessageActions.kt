@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.localaiagent.app.ui

import androidx.compose.ui.res.stringResource
import com.localaiagent.app.R

import android.speech.tts.TextToSpeech
import android.view.ActionMode
import android.view.Menu
import android.view.MenuItem
import android.widget.TextView
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.CallSplit
import androidx.compose.material.icons.rounded.ContentCopy
import androidx.compose.material.icons.rounded.Edit
import androidx.compose.material.icons.rounded.FormatQuote
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Restore
import androidx.compose.material.icons.rounded.TextFields
import androidx.compose.material.icons.automirrored.rounded.VolumeUp
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.localaiagent.app.ChatMessage
import java.util.Locale

/**
 * Меню действий над сообщением (нижний лист, как в ChatGPT/Claude). Набор действий
 * зависит от роли: у пользователя — Изменить; у ИИ — Перегенерировать. Общее:
 * копировать, озвучить, вернуться сюда, ветвить в новый чат, выделить текст.
 */
@Composable
fun MessageActionsSheet(
    msg: ChatMessage,
    onCopy: () -> Unit,
    onSpeak: () -> Unit,
    onEdit: () -> Unit,
    onRegenerate: () -> Unit,
    onRevert: () -> Unit,
    onBranch: () -> Unit,
    onSelectText: () -> Unit,
    onReact: (String) -> Unit = {},
    onFormat: (String) -> Unit = {},
    onDismiss: () -> Unit,
) {
    val sheet = rememberModalBottomSheetState()
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.fillMaxWidth().padding(bottom = 24.dp)) {
            // #6: реакции на ответ ИИ (😕 — переобъяснить, 🔖 — в память).
            if (!msg.fromUser && msg.text.isNotBlank()) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    listOf("👍", "👎", "❤️", "😕", "🔖").forEach { e ->
                        Surface(
                            shape = androidx.compose.foundation.shape.CircleShape,
                            color = if (msg.reaction == e) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f)
                            else MaterialTheme.colorScheme.surfaceContainerHigh,
                            modifier = Modifier.size(46.dp).clickable { onReact(e); onDismiss() },
                        ) {
                            androidx.compose.foundation.layout.Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                Text(e, fontSize = 20.sp)
                            }
                        }
                    }
                }
                // #9: показать ответ в другом формате.
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                        .padding(horizontal = 18.dp, vertical = 2.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    listOf(
                        "table" to stringResource(R.string.remix_table), "diagram" to stringResource(R.string.remix_diagram), "list" to stringResource(R.string.remix_list),
                        "eli5" to stringResource(R.string.remix_simpler), "code" to stringResource(R.string.remix_code),
                    ).forEach { (mode, label) ->
                        Surface(
                            shape = androidx.compose.foundation.shape.RoundedCornerShape(14.dp),
                            color = MaterialTheme.colorScheme.surfaceContainerHigh,
                            modifier = Modifier.clickable { onFormat(mode); onDismiss() },
                        ) {
                            Text(label, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface,
                                modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp))
                        }
                    }
                }
            }
            ActionRow(Icons.Rounded.ContentCopy, stringResource(R.string.act_copy)) { onCopy(); onDismiss() }
            if (msg.text.isNotBlank()) ActionRow(Icons.AutoMirrored.Rounded.VolumeUp, stringResource(R.string.act_speak)) { onSpeak(); onDismiss() }
            if (msg.text.isNotBlank()) ActionRow(Icons.Rounded.TextFields, stringResource(R.string.act_select_text)) { onSelectText() }
            if (msg.fromUser) {
                ActionRow(Icons.Rounded.Edit, stringResource(R.string.act_edit)) { onEdit() }
            } else {
                ActionRow(Icons.Rounded.Refresh, stringResource(R.string.act_regenerate)) { onRegenerate(); onDismiss() }
            }
            ActionRow(Icons.AutoMirrored.Rounded.CallSplit, stringResource(R.string.act_continue_new)) { onBranch(); onDismiss() }
            ActionRow(Icons.Rounded.Restore, stringResource(R.string.act_revert_here)) { onRevert(); onDismiss() }
        }
    }
}

@Composable
private fun ActionRow(icon: ImageVector, label: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 22.dp, vertical = 15.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.width(18.dp))
        Text(label, fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurface)
    }
}

/** Диалог правки своего сообщения. */
@Composable
fun EditMessageDialog(initial: String, onConfirm: (String) -> Unit, onDismiss: () -> Unit) {
    var value by remember { mutableStateOf(TextFieldValue(initial)) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.edit_title)) },
        text = {
            OutlinedTextField(
                value = value, onValueChange = { value = it },
                modifier = Modifier.fillMaxWidth(), minLines = 2,
            )
        },
        confirmButton = {
            TextButton(onClick = { if (value.text.isNotBlank()) onConfirm(value.text.trim()) }) {
                Text(stringResource(R.string.edit_redo))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.action_cancel)) } },
    )
}

/**
 * Лист выделения текста: показываем сообщение в нативном TextView с текстовым
 * выделением и кастомным меню (Ответить цитатой / В память чата / В общую память).
 */
@Composable
fun SelectionSheet(
    text: String,
    onQuote: (String) -> Unit,
    onSaveChat: (String) -> Unit,
    onSaveGlobal: (String) -> Unit,
    onAsk: (String, String) -> Unit,
    onBoard: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    val onColor = MaterialTheme.colorScheme.onBackground
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxWidth().padding(12.dp), shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surface) {
            Column(Modifier.padding(16.dp)) {
                Text(stringResource(R.string.sel_hint),
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.outline)
                Spacer(Modifier.size(8.dp))
                Column(
                    Modifier.verticalScroll(rememberScrollState()).fillMaxWidth()
                        .heightIn(max = 460.dp),
                ) {
                    AndroidView(factory = { ctx ->
                        TextView(ctx).apply {
                            setText(text)
                            setTextIsSelectable(true)
                            textSize = 16f
                            setTextColor(onColorToArgb(onColor))
                            setPadding(4, 4, 4, 4)
                            customSelectionActionModeCallback = selectionCallback(
                                this, onQuote = { onQuote(it); onDismiss() },
                                onChat = { onSaveChat(it); onDismiss() },
                                onGlobal = { onSaveGlobal(it); onDismiss() },
                                onAsk = { t, mode -> onAsk(t, mode); onDismiss() },
                                onBoard = { onBoard(it); onDismiss() },
                            )
                        }
                    })
                }
                Spacer(Modifier.size(8.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onDismiss) { Text(stringResource(R.string.action_close)) }
                }
            }
        }
    }
}

private fun onColorToArgb(c: androidx.compose.ui.graphics.Color): Int {
    val a = (c.alpha * 255).toInt(); val r = (c.red * 255).toInt()
    val g = (c.green * 255).toInt(); val b = (c.blue * 255).toInt()
    return (a shl 24) or (r shl 16) or (g shl 8) or b
}

private fun selectionCallback(
    tv: TextView,
    onQuote: (String) -> Unit,
    onChat: (String) -> Unit,
    onGlobal: (String) -> Unit,
    onAsk: (String, String) -> Unit,
    onBoard: (String) -> Unit,
): ActionMode.Callback {
    val idQuote = 1001; val idChat = 1002; val idGlobal = 1003
    val idSimpler = 1004; val idTranslate = 1005; val idElaborate = 1006; val idVerify = 1007
    val idBoard = 1008
    return object : ActionMode.Callback {
        override fun onCreateActionMode(mode: ActionMode, menu: Menu): Boolean {
            menu.add(0, idSimpler, 0, tv.context.getString(R.string.sel_simpler))
            menu.add(0, idElaborate, 1, tv.context.getString(R.string.sel_more))
            menu.add(0, idTranslate, 2, tv.context.getString(R.string.sel_translate))
            menu.add(0, idVerify, 3, tv.context.getString(R.string.sel_check))
            menu.add(0, idQuote, 4, tv.context.getString(R.string.sel_reply))
            menu.add(0, idBoard, 5, tv.context.getString(R.string.sel_to_board))
            menu.add(0, idChat, 6, tv.context.getString(R.string.sel_to_chat_mem))
            menu.add(0, idGlobal, 7, tv.context.getString(R.string.sel_to_global_mem))
            return true
        }
        override fun onPrepareActionMode(mode: ActionMode, menu: Menu) = false
        override fun onActionItemClicked(mode: ActionMode, item: MenuItem): Boolean {
            val s = tv.selectionStart.coerceAtLeast(0)
            val e = tv.selectionEnd.coerceAtLeast(0)
            if (e <= s) return false
            val sel = tv.text.subSequence(minOf(s, e), maxOf(s, e)).toString()
            when (item.itemId) {
                idQuote -> onQuote(sel)
                idBoard -> onBoard(sel)
                idChat -> onChat(sel)
                idGlobal -> onGlobal(sel)
                idSimpler -> onAsk(sel, "simpler")
                idElaborate -> onAsk(sel, "elaborate")
                idTranslate -> onAsk(sel, "translate")
                idVerify -> onAsk(sel, "verify")
                else -> return false
            }
            mode.finish()
            return true
        }
        override fun onDestroyActionMode(mode: ActionMode) {}
    }
}

/** Плашка активной цитаты над строкой ввода. */
@Composable
fun QuoteChip(quote: String, onClear: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Rounded.FormatQuote, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(8.dp))
            Text(
                quote, maxLines = 2, fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onClear) { Text("×", fontSize = 18.sp) }
        }
    }
}

/**
 * #8 «Доска-коллекция чата»: закреплённая заметка из собранных сниппетов.
 * Каждый сниппет можно удалить; всё — скопировать; доску — очистить.
 */
@Composable
fun BoardSheet(
    items: List<String>,
    onRemove: (Int) -> Unit,
    onClear: () -> Unit,
    onCopyAll: () -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).padding(bottom = 24.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.board_title), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                if (items.isNotEmpty()) {
                    Text("${items.size}", style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.outline)
                }
            }
            Spacer(Modifier.size(4.dp))
            Text(stringResource(R.string.board_hint),
                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.outline)
            Spacer(Modifier.size(12.dp))
            if (items.isEmpty()) {
                AltiEmpty(stringResource(R.string.board_empty), Modifier.fillMaxWidth())
            } else {
                Column(Modifier.verticalScroll(rememberScrollState()).fillMaxWidth().heightIn(max = 420.dp)) {
                    items.forEachIndexed { i, snippet ->
                        Surface(
                            color = MaterialTheme.colorScheme.surfaceVariant,
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        ) {
                            Row(Modifier.padding(start = 12.dp, top = 8.dp, bottom = 8.dp, end = 4.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                Text(snippet, fontSize = 14.sp, modifier = Modifier.weight(1f),
                                    color = MaterialTheme.colorScheme.onSurface)
                                TextButton(onClick = { onRemove(i) }) { Text("×", fontSize = 18.sp) }
                            }
                        }
                    }
                }
                Spacer(Modifier.size(8.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = onClear) { Text(stringResource(R.string.board_clear)) }
                    Spacer(Modifier.width(8.dp))
                    TextButton(onClick = onCopyAll) {
                        Icon(Icons.Rounded.ContentCopy, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(stringResource(R.string.board_copy_all))
                    }
                }
            }
        }
    }
}

/** Простой TTS-движок (озвучка ответов). Возвращает функцию speak(text). */
@Composable
fun rememberSpeaker(): (String) -> Unit {
    val ctx = LocalContext.current
    val holder = remember { TtsHolder() }
    DisposableEffect(Unit) {
        holder.tts = TextToSpeech(ctx) { status ->
            if (status == TextToSpeech.SUCCESS) {
                holder.tts?.language = Locale("ru")
                holder.ready = true
            }
        }
        onDispose { holder.tts?.stop(); holder.tts?.shutdown() }
    }
    return { text ->
        holder.tts?.let { it.stop(); it.speak(text.take(3900), TextToSpeech.QUEUE_FLUSH, null, "msg") }
    }
}

private class TtsHolder {
    var tts: TextToSpeech? = null
    var ready = false
}
