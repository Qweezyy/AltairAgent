package com.localaiagent.app.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.CloudSync
import androidx.compose.material.icons.rounded.Delete
import androidx.compose.material.icons.rounded.Extension
import androidx.compose.material.icons.rounded.FileOpen
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.School
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.localaiagent.app.R
import com.localaiagent.app.SkillInfo
import com.localaiagent.app.mcp.McpConfig
import com.localaiagent.app.mcp.McpServer

/** Everything the Plugins screen can ask the view model to do. */
data class PluginActions(
    /** (previousName or null for a new server, name, url, transport, token) → saved? */
    val saveServer: (String?, String, String, String, String) -> Boolean = { _, _, _, _, _ -> false },
    val importJson: (String) -> Boolean = { false },
    val removeServer: (String) -> Unit = {},
    val toggleServer: (String, Boolean) -> Unit = { _, _ -> },
    val refresh: () -> Unit = {},
    val testServer: (String?, String, String, String, String) -> Unit = { _, _, _, _, _ -> },
    val sync: () -> Unit = {},
    val importSkill: (Uri) -> Unit = {},
    val confirmSkillReplace: (Boolean) -> Unit = {},
    val deleteSkill: (String) -> Unit = {},
    val clearNotice: () -> Unit = {},
)

/**
 * The Plugins screen: MCP servers (external tools) and installed skills. The phone talks to servers
 * directly; the full build can also pull servers and skills from the PC over the bridge.
 */
@Composable
fun PluginsScreen(
    servers: List<McpServer>,
    status: Map<String, String>,
    notice: String,
    busy: Boolean,
    skills: List<SkillInfo>,
    skillReplaceAsk: List<String>,
    syncSupported: Boolean,
    actions: PluginActions,
    onClose: () -> Unit,
) {
    // null — no dialog; "" — a new server; otherwise the name of the server being edited.
    var editing by remember { mutableStateOf<String?>(null) }
    var deletingServer by remember { mutableStateOf<McpServer?>(null) }
    var openedSkill by remember { mutableStateOf<SkillInfo?>(null) }
    var deletingSkill by remember { mutableStateOf<SkillInfo?>(null) }
    val pickSkill = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) actions.importSkill(uri)
    }

    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize()) {
                Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onClose) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, stringResource(R.string.action_back)) }
                    Text(stringResource(R.string.plugins_title), style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
                    if (busy) {
                        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(8.dp))
                    }
                    IconButton(onClick = actions.refresh) { Icon(Icons.Rounded.Refresh, stringResource(R.string.action_refresh)) }
                    IconButton(onClick = { actions.clearNotice(); editing = "" }) { Icon(Icons.Rounded.Add, stringResource(R.string.plugins_add_server)) }
                }

                LazyColumn(Modifier.fillMaxWidth().weight(1f).padding(horizontal = 12.dp)) {
                    item { Hint(stringResource(R.string.plugins_mcp_hint)) }
                    if (servers.isEmpty()) {
                        item { Hint(stringResource(R.string.plugins_no_servers)) }
                    } else {
                        items(servers, key = { "srv:" + it.name }) { s ->
                            McpServerRow(
                                s, status[s.name].orEmpty(),
                                onToggle = actions.toggleServer,
                                onEdit = { actions.clearNotice(); editing = s.name },
                                onRemove = { deletingServer = s },
                            )
                        }
                    }

                    item {
                        Row(
                            Modifier.fillMaxWidth().padding(top = 18.dp, bottom = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Icon(Icons.Rounded.School, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.outline)
                            Spacer(Modifier.width(8.dp))
                            Text(
                                stringResource(R.string.plugins_skills_header, skills.size),
                                style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.outline,
                                modifier = Modifier.weight(1f),
                            )
                            TextButton(onClick = { pickSkill.launch(arrayOf("text/*", "application/zip", "application/octet-stream")) }) {
                                Icon(Icons.Rounded.FileOpen, null, Modifier.size(18.dp))
                                Spacer(Modifier.width(6.dp))
                                Text(stringResource(R.string.skill_import))
                            }
                        }
                    }
                    item { Hint(stringResource(R.string.skills_hint)) }
                    if (skills.isEmpty()) {
                        item {
                            Hint(stringResource(if (syncSupported) R.string.plugins_no_skills_pc else R.string.plugins_no_skills))
                        }
                    } else {
                        items(skills, key = { "skill:" + it.name }) { sk ->
                            SkillRow(sk, onOpen = { openedSkill = sk }, onDelete = { deletingSkill = sk })
                        }
                    }
                }

                if (notice.isNotBlank()) {
                    Text(
                        notice, fontSize = 12.sp, color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp).heightIn(max = 120.dp)
                            .verticalScroll(rememberScrollState()),
                    )
                }
                if (syncSupported) {
                    Row(Modifier.fillMaxWidth().padding(12.dp)) {
                        OutlinedButton(onClick = actions.sync, enabled = !busy, modifier = Modifier.weight(1f)) {
                            Icon(Icons.Rounded.CloudSync, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text(stringResource(R.string.plugins_sync_pc))
                        }
                    }
                }
            }
        }
    }

    editing?.let { key ->
        val existing = servers.firstOrNull { it.name == key }
        McpEditDialog(
            existing = existing,
            notice = notice,
            busy = busy,
            onSave = { n, u, t, tok -> if (actions.saveServer(existing?.name, n, u, t, tok)) editing = null },
            onImportJson = { if (actions.importJson(it)) editing = null },
            onTest = { n, u, t, tok -> actions.testServer(existing?.name, n, u, t, tok) },
            onCancel = { editing = null },
        )
    }
    deletingServer?.let { s ->
        ConfirmDelete(
            title = stringResource(R.string.mcp_delete_title, s.name),
            body = stringResource(R.string.mcp_delete_body) +
                (if (s.fromPc) "\n" + stringResource(R.string.mcp_delete_pc_note) else ""),
            onConfirm = { actions.removeServer(s.name); deletingServer = null },
            onCancel = { deletingServer = null },
        )
    }
    openedSkill?.let { sk -> SkillViewDialog(sk, onClose = { openedSkill = null }) }
    deletingSkill?.let { sk ->
        ConfirmDelete(
            title = stringResource(R.string.skill_delete_title, sk.name),
            body = if (sk.fromPc) stringResource(R.string.skill_delete_pc_note) else "",
            onConfirm = { actions.deleteSkill(sk.name); deletingSkill = null },
            onCancel = { deletingSkill = null },
        )
    }
    if (skillReplaceAsk.isNotEmpty()) {
        AlertDialog(
            onDismissRequest = { actions.confirmSkillReplace(false) },
            title = { Text(stringResource(R.string.skill_replace_title)) },
            text = { Text(stringResource(R.string.skill_replace_body, skillReplaceAsk.joinToString(", "))) },
            confirmButton = { TextButton(onClick = { actions.confirmSkillReplace(true) }) { Text(stringResource(R.string.skill_replace_ok)) } },
            dismissButton = { TextButton(onClick = { actions.confirmSkillReplace(false) }) { Text(stringResource(R.string.action_cancel)) } },
        )
    }
}

@Composable
private fun Hint(text: String) {
    Text(text, fontSize = 12.sp, color = MaterialTheme.colorScheme.outline, modifier = Modifier.padding(vertical = 6.dp))
}

@Composable
private fun ConfirmDelete(title: String, body: String, onConfirm: () -> Unit, onCancel: () -> Unit) {
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text(title) },
        text = if (body.isBlank()) null else ({ Text(body) }),
        confirmButton = {
            TextButton(onClick = onConfirm) { Text(stringResource(R.string.action_delete), color = MaterialTheme.colorScheme.error) }
        },
        dismissButton = { TextButton(onClick = onCancel) { Text(stringResource(R.string.action_cancel)) } },
    )
}

@Composable
private fun McpServerRow(
    s: McpServer,
    status: String,
    onToggle: (String, Boolean) -> Unit,
    onEdit: () -> Unit,
    onRemove: () -> Unit,
) {
    Surface(
        Modifier.fillMaxWidth().padding(vertical = 5.dp).clickable(onClick = onEdit), shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Rounded.Extension, null, tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(s.name, color = MaterialTheme.colorScheme.onSurface)
                    if (s.fromPc) {
                        Spacer(Modifier.width(6.dp))
                        Text(stringResource(R.string.mcp_from_pc), fontSize = 10.sp, color = MaterialTheme.colorScheme.outline)
                    }
                }
                Text(s.url, fontSize = 11.sp, color = MaterialTheme.colorScheme.outline, maxLines = 1)
                val line = (if (s.transport == "sse") "SSE" else "HTTP") + (if (status.isNotBlank()) "  ·  $status" else "")
                Text(line, fontSize = 11.sp, color = MaterialTheme.colorScheme.outline, maxLines = 2)
            }
            Switch(checked = s.enabled, onCheckedChange = { onToggle(s.name, it) })
            IconButton(onClick = onRemove) { Icon(Icons.Rounded.Delete, stringResource(R.string.action_delete)) }
        }
    }
}

@Composable
private fun SkillRow(sk: SkillInfo, onOpen: () -> Unit, onDelete: () -> Unit) {
    Surface(
        Modifier.fillMaxWidth().padding(vertical = 4.dp).clickable(onClick = onOpen), shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Row(Modifier.padding(start = 12.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Rounded.School, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(sk.name, color = MaterialTheme.colorScheme.onSurface, fontSize = 14.sp)
                    if (sk.fromPc) {
                        Spacer(Modifier.width(6.dp))
                        Text(stringResource(R.string.mcp_from_pc), fontSize = 10.sp, color = MaterialTheme.colorScheme.outline)
                    }
                }
                if (sk.description.isNotBlank()) {
                    Text(sk.description, fontSize = 11.sp, color = MaterialTheme.colorScheme.outline, maxLines = 2)
                }
            }
            IconButton(onClick = onDelete) { Icon(Icons.Rounded.Delete, stringResource(R.string.action_delete)) }
        }
    }
}

@Composable
private fun SkillViewDialog(sk: SkillInfo, onClose: () -> Unit) {
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text(sk.name) },
        text = {
            Column(Modifier.heightIn(max = 460.dp).verticalScroll(rememberScrollState())) {
                if (sk.description.isNotBlank()) {
                    Text(sk.description, fontSize = 13.sp, color = MaterialTheme.colorScheme.outline)
                    Spacer(Modifier.size(8.dp))
                }
                Text(sk.body, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
                if (sk.files.isNotEmpty()) {
                    Spacer(Modifier.size(8.dp))
                    Text(
                        stringResource(R.string.skill_files, sk.files.joinToString(", ")),
                        fontSize = 11.sp, color = MaterialTheme.colorScheme.outline,
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text(stringResource(R.string.action_close)) } },
    )
}

@Composable
private fun McpEditDialog(
    existing: McpServer?,
    notice: String,
    busy: Boolean,
    onSave: (name: String, url: String, transport: String, token: String) -> Unit,
    onImportJson: (String) -> Unit,
    onTest: (name: String, url: String, transport: String, token: String) -> Unit,
    onCancel: () -> Unit,
) {
    var pasteMode by remember { mutableStateOf(false) }
    var jsonText by remember { mutableStateOf("") }
    var name by remember { mutableStateOf(existing?.name.orEmpty()) }
    var url by remember { mutableStateOf(existing?.url.orEmpty()) }
    var token by remember { mutableStateOf("") }
    var transport by remember { mutableStateOf(existing?.transport ?: "http") }
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text(stringResource(if (existing == null) R.string.mcp_new_server else R.string.mcp_edit_server)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (existing == null) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(!pasteMode, { pasteMode = false }, { Text(stringResource(R.string.mcp_manual)) })
                        FilterChip(pasteMode, { pasteMode = true }, { Text(stringResource(R.string.mcp_paste_json)) })
                    }
                    Spacer(Modifier.size(6.dp))
                }
                if (pasteMode) {
                    Text(stringResource(R.string.mcp_paste_hint), fontSize = 12.sp, color = MaterialTheme.colorScheme.outline)
                    Spacer(Modifier.size(6.dp))
                    OutlinedTextField(
                        jsonText, { jsonText = it }, label = { Text(stringResource(R.string.mcp_json_field)) },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 140.dp),
                        textStyle = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    )
                } else {
                    OutlinedTextField(name, { name = it }, label = { Text(stringResource(R.string.mcp_field_name)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.size(6.dp))
                    OutlinedTextField(url, { url = it }, label = { Text(stringResource(R.string.mcp_field_url)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.size(6.dp))
                    OutlinedTextField(
                        token, { token = it },
                        label = {
                            Text(stringResource(if (existing?.headers?.isNotEmpty() == true) R.string.mcp_field_token_keep else R.string.mcp_field_token))
                        },
                        singleLine = true, visualTransformation = PasswordVisualTransformation(),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    if (existing != null && existing.headers.isNotEmpty()) {
                        Text(
                            stringResource(
                                R.string.mcp_headers,
                                existing.headers.entries.joinToString(", ") { (k, v) -> "$k: ${McpConfig.maskHeader(k, v)}" },
                            ),
                            fontSize = 11.sp, color = MaterialTheme.colorScheme.outline,
                        )
                    }
                    Spacer(Modifier.size(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(transport == "http", { transport = "http" }, { Text("HTTP") })
                        FilterChip(transport == "sse", { transport = "sse" }, { Text("SSE") })
                    }
                    Spacer(Modifier.size(4.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        TextButton(onClick = { onTest(name, url, transport, token) }, enabled = !busy) {
                            Text(stringResource(R.string.mcp_test_connection))
                        }
                        if (busy) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    }
                }
                if (notice.isNotBlank()) {
                    Text(notice, fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { if (pasteMode) onImportJson(jsonText) else onSave(name, url, transport, token) }) {
                Text(stringResource(if (pasteMode) R.string.action_add else R.string.action_save))
            }
        },
        dismissButton = { TextButton(onClick = onCancel) { Text(stringResource(R.string.action_cancel)) } },
    )
}
