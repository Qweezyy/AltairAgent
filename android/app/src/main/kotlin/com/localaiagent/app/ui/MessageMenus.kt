package com.localaiagent.app.ui

import android.content.Context
import android.view.ActionMode
import android.view.Menu
import android.view.MenuItem
import android.view.View
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.TextToolbar
import androidx.compose.ui.platform.TextToolbarStatus
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.dp
import com.localaiagent.app.R

/** One row of a message or chat menu. */
class MenuEntry(val icon: ImageVector, val label: String, val onClick: () -> Unit)

/**
 * The app's popup menu for messages and the chat (as in ChatGPT, in the Altair style): a plate with a
 * hairline, an optional quiet header (the message time), optional extra content, then the rows.
 */
@Composable
fun AltairMenu(
    expanded: Boolean,
    onDismiss: () -> Unit,
    entries: List<MenuEntry>,
    header: String? = null,
    offset: DpOffset = DpOffset(0.dp, 6.dp),
    top: (@Composable ColumnScope.() -> Unit)? = null,
) {
    val alt = com.localaiagent.app.ui.theme.LocalAltair.current
    DropdownMenu(
        expanded = expanded,
        onDismissRequest = onDismiss,
        offset = offset,
        shape = RoundedCornerShape(22.dp),
        containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        border = BorderStroke(1.dp, alt.hairStrong),
        shadowElevation = 16.dp,
        modifier = Modifier.widthIn(min = 240.dp),
    ) {
        header?.let {
            Text(
                it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.outline,
                modifier = Modifier.padding(start = 20.dp, end = 20.dp, top = 8.dp, bottom = 6.dp),
            )
        }
        top?.invoke(this)
        entries.forEach { e ->
            DropdownMenuItem(
                text = { Text(e.label, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium) },
                leadingIcon = { Icon(e.icon, null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.onSurface) },
                onClick = { onDismiss(); e.onClick() },
                contentPadding = PaddingValues(horizontal = 20.dp, vertical = 4.dp),
            )
        }
    }
}

/** "Today, 11:02 PM" / "Yesterday, …" / a date: the time a message was written; null if unknown. */
fun messageTimeLabel(context: Context, time: Long): String? {
    if (time <= 0L) return null
    val clock = android.text.format.DateFormat.getTimeFormat(context).format(java.util.Date(time))
    return when {
        android.text.format.DateUtils.isToday(time) -> context.getString(R.string.time_today, clock)
        android.text.format.DateUtils.isToday(time + android.text.format.DateUtils.DAY_IN_MILLIS) ->
            context.getString(R.string.time_yesterday, clock)
        else -> android.text.format.DateFormat.getMediumDateFormat(context).format(java.util.Date(time)) + ", " + clock
    }
}

/** Shares text through the system share sheet. */
fun shareText(context: Context, text: String) {
    val send = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(android.content.Intent.EXTRA_TEXT, text)
    }
    runCatching {
        context.startActivity(
            android.content.Intent.createChooser(send, null).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}

/** What can be done with text selected in an answer (beyond copy). */
enum class SelectionAction(val label: Int) {
    SIMPLER(R.string.sel_simpler), MORE(R.string.sel_more), TRANSLATE(R.string.sel_translate),
    VERIFY(R.string.sel_check), QUOTE(R.string.sel_reply), BOARD(R.string.sel_to_board),
    CHAT_MEMORY(R.string.sel_to_chat_mem), SHARED_MEMORY(R.string.sel_to_global_mem),
}

/**
 * The clipboard seen by an answer's selection. Compose gives no access to the selected text, so an app
 * action asks the selection to copy and catches the text here, before it reaches the system clipboard
 * (which would also show Android's "pasted from your clipboard" notice). A plain Copy goes through.
 */
class CapturingClipboard(private val real: androidx.compose.ui.platform.ClipboardManager) :
    androidx.compose.ui.platform.ClipboardManager by real {
    var capture: ((String) -> Unit)? = null

    override fun setText(annotatedString: androidx.compose.ui.text.AnnotatedString) {
        val c = capture
        if (c != null) { capture = null; c(annotatedString.text) } else real.setText(annotatedString)
    }
}

/**
 * The floating toolbar for text selected by a long press in an answer: the system's Copy and Select all
 * plus the app's actions, which get the selected text through [clipboard].
 */
class AnswerTextToolbar(
    private val view: View,
    private val clipboard: CapturingClipboard,
    private val onAction: (SelectionAction, String) -> Unit,
) : TextToolbar {
    private var mode: ActionMode? = null
    private var rect = Rect.Zero
    override var status: TextToolbarStatus = TextToolbarStatus.Hidden
        private set

    override fun showMenu(
        rect: Rect,
        onCopyRequested: (() -> Unit)?,
        onPasteRequested: (() -> Unit)?,
        onCutRequested: (() -> Unit)?,
        onSelectAllRequested: (() -> Unit)?,
    ) {
        this.rect = rect
        val callback = object : ActionMode.Callback2() {
            override fun onCreateActionMode(mode: ActionMode, menu: Menu): Boolean {
                if (onCopyRequested != null) menu.add(0, ID_COPY, 0, android.R.string.copy)
                if (onSelectAllRequested != null) menu.add(0, ID_ALL, 1, android.R.string.selectAll)
                if (onCopyRequested != null) SelectionAction.entries.forEachIndexed { i, a ->
                    menu.add(0, ID_BASE + i, 2 + i, view.context.getString(a.label))
                }
                return true
            }

            override fun onPrepareActionMode(mode: ActionMode, menu: Menu) = false

            override fun onActionItemClicked(mode: ActionMode, item: MenuItem): Boolean {
                when (val id = item.itemId) {
                    ID_COPY -> onCopyRequested?.invoke()
                    ID_ALL -> { onSelectAllRequested?.invoke(); return true }
                    else -> {
                        val action = SelectionAction.entries.getOrNull(id - ID_BASE) ?: return false
                        val text = selectedText(onCopyRequested ?: return false)
                        if (text.isNotBlank()) onAction(action, text)
                    }
                }
                mode.finish()
                return true
            }

            override fun onDestroyActionMode(mode: ActionMode) {
                this@AnswerTextToolbar.mode = null
                status = TextToolbarStatus.Hidden
            }

            override fun onGetContentRect(mode: ActionMode, view: View, outRect: android.graphics.Rect) {
                val r = this@AnswerTextToolbar.rect
                outRect.set(r.left.toInt(), r.top.toInt(), r.right.toInt(), r.bottom.toInt())
            }
        }
        val current = mode
        if (current == null) {
            status = TextToolbarStatus.Shown
            mode = view.startActionMode(callback, ActionMode.TYPE_FLOATING)
        } else {
            current.invalidateContentRect()
        }
    }

    override fun hide() {
        status = TextToolbarStatus.Hidden
        mode?.finish()
        mode = null
    }

    private fun selectedText(copy: () -> Unit): String {
        var text = ""
        clipboard.capture = { text = it }
        copy()
        clipboard.capture = null
        return text
    }

    private companion object {
        const val ID_COPY = 1
        const val ID_ALL = 2
        const val ID_BASE = 100
    }
}
