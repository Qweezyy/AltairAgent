@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.localaiagent.app.ui

import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.Send
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.QrCodeScanner
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.localaiagent.app.R
import com.localaiagent.app.servers.JournalRow
import com.localaiagent.app.servers.ServerCard
import com.localaiagent.app.servers.ServerChatItem
import com.localaiagent.app.servers.ServerChatState
import com.localaiagent.app.servers.ServerDetail
import com.localaiagent.app.servers.ServerLink
import com.localaiagent.app.servers.ServerQuestion
import com.localaiagent.app.servers.ServerRoute
import com.localaiagent.app.servers.ServersViewModel
import com.localaiagent.app.servers.parseServerLink
import com.localaiagent.app.ui.theme.plate
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The servers this phone is a body of (PHONE_SERVER_SPEC): the list with how each is reachable,
 * a server's page, and its chats. [initialLink] is a scanned or opened `altair://body` link,
 * confirmed before anything is sent.
 */
@Composable
fun ServersScreen(
    vm: ServersViewModel,
    initialLink: String?,
    openChat: Pair<String, String>?,
    onConsumed: () -> Unit,
    onClose: () -> Unit,
) {
    val ui by vm.ui.collectAsState()
    val ctx = LocalContext.current
    var pending by remember { mutableStateOf<ServerLink?>(null) }

    LaunchedEffect(Unit) { vm.refresh() }
    LaunchedEffect(initialLink, openChat) {
        initialLink?.let { link ->
            pending = parseServerLink(link)
            if (pending == null) Toast.makeText(ctx, ctx.getString(R.string.pair_fail), Toast.LENGTH_SHORT).show()
        }
        openChat?.let { (server, chat) -> vm.openServer(server); vm.openChat(server, chat) }
        if (initialLink != null || openChat != null) onConsumed()
    }
    LaunchedEffect(ui.message) {
        ui.message?.let { Toast.makeText(ctx, it, Toast.LENGTH_LONG).show(); vm.consumeMessage() }
    }

    val scanPrompt = stringResource(R.string.srv_scan_prompt)
    val scan = rememberLauncherForActivityResult(com.journeyapps.barcodescanner.ScanContract()) { res ->
        val text = res.contents ?: return@rememberLauncherForActivityResult
        pending = parseServerLink(text)
        if (pending == null) Toast.makeText(ctx, ctx.getString(R.string.pair_fail), Toast.LENGTH_LONG).show()
    }
    val launchScan = {
        scan.launch(
            com.journeyapps.barcodescanner.ScanOptions()
                .setDesiredBarcodeFormats(com.journeyapps.barcodescanner.ScanOptions.QR_CODE)
                .setPrompt(scanPrompt).setBeepEnabled(false).setOrientationLocked(false),
        )
    }

    FullScreenScaffold(
        title = stringResource(R.string.srv_title),
        onBack = onClose,
        actions = {
            IconButton(onClick = vm::refresh) { Icon(Icons.Rounded.Refresh, stringResource(R.string.srv_refresh)) }
            IconButton(onClick = launchScan) { Icon(Icons.Rounded.QrCodeScanner, stringResource(R.string.srv_scan)) }
        },
    ) { pad ->
        SettingsScroll(pad) {
            Text(
                stringResource(R.string.srv_hint), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (ui.pairing) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(10.dp))
                    Text(stringResource(R.string.srv_pairing))
                }
            }
            if (ui.cards.isEmpty()) {
                AltiEmpty(stringResource(R.string.srv_empty), Modifier.fillMaxWidth())
            } else {
                ui.cards.forEach { card -> ServerCardRow(card) { vm.openServer(card.entry.id) } }
            }
            Button(onClick = launchScan, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Rounded.QrCodeScanner, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text(stringResource(R.string.srv_scan))
            }
            PasteLink { pending = it }
        }
    }

    pending?.let { link ->
        AlertDialog(
            onDismissRequest = { pending = null },
            title = { Text(stringResource(R.string.srv_confirm_title)) },
            text = { Text(stringResource(R.string.srv_confirm_body, link.name.ifBlank { link.serverId })) },
            confirmButton = { TextButton(onClick = { vm.pair(link); pending = null }) { Text(stringResource(R.string.srv_add)) } },
            dismissButton = { TextButton(onClick = { pending = null }) { Text(stringResource(R.string.action_cancel)) } },
        )
    }

    ui.detail?.let { detail ->
        val card = ui.cards.firstOrNull { it.entry.id == detail.serverId }
        ServerPage(vm, detail, card)
    }
    val chat = ui.chat
    if (chat != null && ui.chatServerId != null) {
        val name = ui.cards.firstOrNull { it.entry.id == ui.chatServerId }?.entry?.name.orEmpty()
        ServerChatPage(vm, name, chat)
    }
}

@Composable
private fun PasteLink(onLink: (ServerLink) -> Unit) {
    val clipboard = LocalClipboardManager.current
    var link by remember { mutableStateOf("") }
    OutlinedTextField(
        link, { link = it }, label = { Text(stringResource(R.string.srv_link_label)) },
        singleLine = true, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp),
    )
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        TextButton(onClick = { link = clipboard.getText()?.text?.toString().orEmpty() }) {
            Text(stringResource(R.string.bridge_from_clipboard))
        }
        Spacer(Modifier.weight(1f))
        val parsed = parseServerLink(link)
        Button(onClick = { parsed?.let(onLink) }, enabled = parsed != null) { Text(stringResource(R.string.srv_add)) }
    }
}

@Composable
private fun ServerCardRow(card: ServerCard, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 4.dp).plate(14.dp).clip(RoundedCornerShape(14.dp))
            .clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RouteDot(card)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(card.entry.name, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
            Text(
                routeLabel(card), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun RouteDot(card: ServerCard) {
    val color = when {
        card.checking -> MaterialTheme.colorScheme.outline
        card.route != null -> androidx.compose.ui.graphics.Color(0xFF3FB950)
        else -> MaterialTheme.colorScheme.error
    }
    Box(Modifier.size(10.dp).clip(CircleShape).background(color))
}

@Composable
private fun routeLabel(card: ServerCard): String = when {
    card.checking -> stringResource(R.string.srv_route_checking)
    card.route == ServerRoute.DIRECT -> stringResource(R.string.srv_route_direct)
    card.route == ServerRoute.RELAY -> stringResource(R.string.srv_route_relay)
    else -> card.problem ?: stringResource(R.string.srv_err_offline)
}

// ---------------------------------------------------------------------- a server's page

@Composable
private fun ServerPage(vm: ServersViewModel, detail: ServerDetail, card: ServerCard?) {
    val name = card?.entry?.name ?: detail.serverId
    var confirmStop by remember { mutableStateOf(false) }
    var confirmRemove by remember { mutableStateOf(false) }
    FullScreenScaffold(
        title = name,
        onBack = vm::closeServer,
        actions = {
            IconButton(onClick = vm::reloadServer) { Icon(Icons.Rounded.Refresh, stringResource(R.string.srv_refresh)) }
            IconButton(onClick = { confirmRemove = true }) { Icon(Icons.Outlined.Delete, stringResource(R.string.srv_remove)) }
        },
    ) { pad ->
        SettingsScroll(pad) {
            if (card != null) Text(routeLabel(card), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (detail.loading) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
            detail.problem?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium) }

            detail.status?.let { StatusGroup(it) }

            SettingsGroup(stringResource(R.string.srv_chats)) {
                Row(
                    Modifier.fillMaxWidth().clickable { vm.openChat(detail.serverId, null) }.padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Rounded.Add, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.width(10.dp))
                    Text(stringResource(R.string.srv_new_chat), color = MaterialTheme.colorScheme.primary)
                }
                if (detail.chats.isEmpty() && !detail.loading) {
                    Text(stringResource(R.string.srv_no_chats), Modifier.padding(16.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                detail.chats.forEach { c ->
                    Row(
                        Modifier.fillMaxWidth().clickable { vm.openChat(detail.serverId, c.id) }.padding(horizontal = 16.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Outlined.ChatBubbleOutline, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.width(10.dp))
                        Text(c.title.ifBlank { c.id }, Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        if (c.running) Text(stringResource(R.string.srv_running), style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.primary)
                    }
                }
            }

            Button(
                onClick = { confirmStop = true },
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Rounded.Stop, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text(stringResource(R.string.srv_stop_all))
            }

            SettingsGroup(stringResource(R.string.srv_journal)) {
                when {
                    detail.journalClosed -> Text(stringResource(R.string.srv_journal_closed), Modifier.padding(16.dp),
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    detail.journal.isEmpty() && !detail.loading -> Text(stringResource(R.string.srv_journal_empty), Modifier.padding(16.dp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    else -> detail.journal.forEach { JournalLine(it) }
                }
            }
        }
    }

    if (confirmStop) {
        AlertDialog(
            onDismissRequest = { confirmStop = false },
            title = { Text(stringResource(R.string.srv_stop_all)) },
            text = { Text(stringResource(R.string.srv_stop_all_confirm, name)) },
            confirmButton = { TextButton(onClick = { confirmStop = false; vm.stopAll() }) { Text(stringResource(R.string.srv_stop_all)) } },
            dismissButton = { TextButton(onClick = { confirmStop = false }) { Text(stringResource(R.string.action_cancel)) } },
        )
    }
    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text(stringResource(R.string.srv_remove)) },
            text = { Text(stringResource(R.string.srv_remove_confirm, name)) },
            confirmButton = { TextButton(onClick = { confirmRemove = false; vm.remove(detail.serverId) }) { Text(stringResource(R.string.srv_remove)) } },
            dismissButton = { TextButton(onClick = { confirmRemove = false }) { Text(stringResource(R.string.action_cancel)) } },
        )
    }
}

@Composable
private fun StatusGroup(status: JsonObject) {
    fun s(k: String) = status[k]?.jsonPrimitive?.contentOrNull
    val load = status["load"] as? JsonObject
    fun l(k: String) = load?.get(k)?.jsonPrimitive?.contentOrNull
    val rows = buildList {
        listOfNotNull(s("system"), s("arch")).joinToString(" · ").takeIf { it.isNotBlank() }
            ?.let { add(R.string.srv_stat_system to it) }
        s("cpus")?.let { add(R.string.srv_stat_cpus to it) }
        s("mem_mb")?.toDoubleOrNull()?.let { add(R.string.srv_stat_mem to gb(it)) }
        l("cpu_pct")?.let { add(R.string.srv_stat_load to "$it %") }
        l("mem_used_mb")?.toDoubleOrNull()?.let { add(R.string.srv_stat_mem_used to gb(it)) }
        l("disk_free_mb")?.toDoubleOrNull()?.let { add(R.string.srv_stat_disk to gb(it)) }
        l("tasks")?.let { add(R.string.srv_stat_tasks to it) }
        s("docker")?.let { add(R.string.srv_stat_docker to it) }
    }
    if (rows.isEmpty()) return
    SettingsGroup(stringResource(R.string.srv_status)) {
        rows.forEach { (label, value) ->
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                Text(stringResource(label), Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(value, fontWeight = FontWeight.Medium)
            }
        }
    }
}

private fun gb(mb: Double): String = if (mb >= 1024) String.format(Locale.ROOT, "%.1f GB", mb / 1024) else "${mb.toInt()} MB"

@Composable
private fun JournalLine(r: JournalRow) {
    val time = remember(r.ts) { SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(Date((r.ts * 1000).toLong())) }
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp)) {
        Row {
            Text(time, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(8.dp))
            Text(r.kind, style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.primary)
        }
        if (r.text.isNotBlank()) Text(r.text, style = MaterialTheme.typography.bodySmall, maxLines = 3, overflow = TextOverflow.Ellipsis)
    }
}

// ---------------------------------------------------------------------- a server chat

@Composable
private fun ServerChatPage(vm: ServersViewModel, serverName: String, chat: ServerChatState) {
    var input by remember { mutableStateOf("") }
    val list = rememberLazyListState()
    val count = chat.items.size + if (chat.streaming.isNotBlank() || chat.running) 1 else 0
    LaunchedEffect(count, chat.streaming.length / 200) { if (count > 0) list.animateScrollToItem(count - 1) }

    FullScreenScaffold(
        title = chat.title.ifBlank { serverName },
        onBack = vm::closeChat,
        actions = {
            if (!chat.connected) {
                IconButton(onClick = vm::reconnect) { Icon(Icons.Rounded.Refresh, stringResource(R.string.srv_reconnect)) }
            }
        },
    ) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).imePadding()) {
            if (!chat.connected) {
                Text(
                    stringResource(R.string.srv_disconnected), style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp),
                )
            }
            LazyColumn(
                Modifier.weight(1f).fillMaxWidth(), state = list,
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(chat.items) { ChatLine(it) }
                if (chat.streaming.isNotBlank()) item { MarkdownText(chat.streaming) }
                // A model can take a minute to start (or be retried): show that the server works on it.
                if (chat.running && chat.streaming.isBlank() && chat.approval == null && chat.question == null) item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(10.dp))
                        Text(
                            chat.retry?.let { (n, of) -> stringResource(R.string.srv_retrying, n, of) }
                                ?: stringResource(R.string.srv_working),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    input, { input = it }, placeholder = { Text(stringResource(R.string.srv_message_hint)) },
                    modifier = Modifier.weight(1f).heightIn(max = 160.dp), shape = RoundedCornerShape(20.dp),
                )
                Spacer(Modifier.width(6.dp))
                if (chat.running && input.isBlank()) {
                    IconButton(onClick = vm::stopRun) { Icon(Icons.Rounded.Stop, stringResource(R.string.srv_stop)) }
                } else {
                    IconButton(
                        onClick = { vm.sendTask(input); input = "" },
                        enabled = input.isNotBlank() && chat.connected,
                    ) { Icon(Icons.AutoMirrored.Rounded.Send, stringResource(R.string.srv_send)) }
                }
            }
        }
    }

    chat.approval?.let { a ->
        AlertDialog(
            onDismissRequest = {},
            title = { Text(stringResource(R.string.srv_approval_title)) },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    Text(a.name, fontWeight = FontWeight.SemiBold)
                    if (a.reason.isNotBlank()) Text(a.reason, style = MaterialTheme.typography.bodyMedium)
                    if (a.args.isNotBlank()) Text(a.args, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp))
                }
            },
            confirmButton = {
                Column(horizontalAlignment = Alignment.End) {
                    TextButton(onClick = { vm.answerApproval("once") }) { Text(stringResource(R.string.srv_allow_once)) }
                    TextButton(onClick = { vm.answerApproval("project") }) { Text(stringResource(R.string.srv_allow_project)) }
                    TextButton(onClick = { vm.answerApproval("global") }) { Text(stringResource(R.string.srv_allow_global)) }
                    TextButton(onClick = { vm.answerApproval("deny") }) {
                        Text(stringResource(R.string.srv_deny), color = MaterialTheme.colorScheme.error)
                    }
                }
            },
        )
    }
    chat.question?.let { QuestionDialog(it, vm::answerQuestion) }
}

@Composable
private fun ChatLine(item: ServerChatItem) {
    when (item) {
        is ServerChatItem.User -> Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
            Text(
                item.text,
                Modifier.widthIn(max = 320.dp).plate(16.dp, tray = false).padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
        is ServerChatItem.Assistant -> MarkdownText(item.text)
        is ServerChatItem.Tool -> {
            var open by remember { mutableStateOf(false) }
            val mark = when (item.ok) { null -> "…"; true -> "✓"; false -> "✕" }
            Column(Modifier.fillMaxWidth().clickable { open = !open }) {
                Text(
                    "$mark ${item.name}  ${item.args}", style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace, maxLines = if (open) 6 else 1, overflow = TextOverflow.Ellipsis,
                    color = if (item.ok == false) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (open && item.output.isNotBlank()) {
                    Text(
                        item.output.take(2000), style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 14.dp, top = 4.dp),
                    )
                }
            }
        }
        is ServerChatItem.Note -> Text(
            item.text, style = MaterialTheme.typography.bodySmall,
            color = if (item.error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun QuestionDialog(q: ServerQuestion, onAnswer: (Map<Int, List<String>>) -> Unit) {
    val chosen = remember(q.requestId) {
        mutableStateMapOf<Int, List<String>>().apply {
            q.items.forEachIndexed { i, item -> item.options.firstOrNull { it.recommended }?.let { put(i, listOf(it.label)) } }
        }
    }
    AlertDialog(
        onDismissRequest = {},
        title = { Text(stringResource(R.string.srv_question_title)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                q.items.forEachIndexed { i, item ->
                    Text(item.question, fontWeight = FontWeight.SemiBold)
                    item.options.forEach { op ->
                        val picked = chosen[i].orEmpty().contains(op.label)
                        Row(
                            Modifier.fillMaxWidth().clickable {
                                val now = chosen[i].orEmpty()
                                chosen[i] = if (item.multiple) (if (picked) now - op.label else now + op.label) else listOf(op.label)
                            },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            if (item.multiple) Checkbox(picked, null) else RadioButton(picked, null)
                            Spacer(Modifier.width(6.dp))
                            Column {
                                Text(op.label)
                                if (op.description.isNotBlank()) Text(op.description, style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            OutlinedButton(onClick = { onAnswer(chosen.toMap()) }, enabled = q.items.indices.all { chosen[it].orEmpty().isNotEmpty() }) {
                Text(stringResource(R.string.srv_answer))
            }
        },
    )
}
