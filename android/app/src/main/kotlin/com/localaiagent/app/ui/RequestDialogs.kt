@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.localaiagent.app.ui

import androidx.compose.ui.res.stringResource
import com.localaiagent.app.R

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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.BookmarkAdd
import androidx.compose.material.icons.rounded.Bolt
import androidx.compose.material.icons.rounded.Delete
import androidx.compose.material.icons.rounded.Key
import androidx.compose.material.icons.rounded.UploadFile
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.localaiagent.app.FileRequest
import com.localaiagent.app.SecretRequest
import com.localaiagent.app.data.Secret

/** Форма запроса файла от ИИ (инструмент request_file). */
@Composable
fun FileRequestDialog(req: FileRequest, onPick: () -> Unit, onCancel: () -> Unit) {
    AlertDialog(
        onDismissRequest = onCancel,
        icon = { Icon(Icons.Rounded.UploadFile, null, tint = MaterialTheme.colorScheme.primary) },
        title = { Text(stringResource(R.string.req_file_needed)) },
        text = {
            val typesLine = stringResource(R.string.req_types, req.accept)
            val countLine = if (req.multiple) stringResource(R.string.req_multi) else stringResource(R.string.req_single)
            val limitLine = stringResource(R.string.req_limit, req.maxMb)
            val requiredLine = if (req.required) stringResource(R.string.req_required) else ""
            Text(
                buildString {
                    append(req.purpose)
                    append("\n\n")
                    append(typesLine)
                    append(countLine)
                    append(limitLine)
                    append(requiredLine)
                },
                fontSize = 14.sp,
            )
        },
        confirmButton = {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                if (!req.required) TextButton(onClick = onCancel) { Text(stringResource(R.string.req_skip)) }
                TextButton(onClick = onPick) { Text(stringResource(R.string.req_pick_file)) }
            }
        },
    )
}

/** Форма ввода секрета по запросу ИИ. Значение уходит в хранилище, модель его не видит. */
@Composable
fun SecretRequestDialog(
    req: SecretRequest,
    onSubmit: (name: String, value: String, availability: String) -> Unit,
    onCancel: () -> Unit,
) {
    var name by remember { mutableStateOf(req.name) }
    var value by remember { mutableStateOf("") }
    var availability by remember { mutableStateOf("ask") }
    AlertDialog(
        onDismissRequest = onCancel,
        icon = { Icon(Icons.Rounded.Key, null, tint = MaterialTheme.colorScheme.primary) },
        title = { Text(stringResource(R.string.sec_needed)) },
        text = {
            Column {
                if (req.purpose.isNotBlank()) {
                    Text(req.purpose, fontSize = 13.sp, color = MaterialTheme.colorScheme.outline)
                    Spacer(Modifier.size(8.dp))
                }
                OutlinedTextField(
                    value = name, onValueChange = { name = it },
                    label = { Text(stringResource(R.string.sec_name)) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.size(6.dp))
                OutlinedTextField(
                    value = value, onValueChange = { value = it },
                    label = { Text(stringResource(R.string.sec_value_hidden)) }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.size(8.dp))
                Text(stringResource(R.string.sec_access), fontSize = 12.sp, color = MaterialTheme.colorScheme.outline)
                Row {
                    FilterChip(availability == "always", { availability = "always" }, { Text(stringResource(R.string.sec_always)) })
                    Spacer(Modifier.width(8.dp))
                    FilterChip(availability == "ask", { availability = "ask" }, { Text(stringResource(R.string.sec_on_permission)) })
                }
            }
        },
        confirmButton = { TextButton(onClick = { onSubmit(name, value, availability) }) { Text(stringResource(R.string.action_save)) } },
        dismissButton = { TextButton(onClick = onCancel) { Text(stringResource(R.string.action_cancel)) } },
    )
}

/** Предложение ИИ сохранить факт в память (#4) — preview + подтверждение. */
@Composable
fun MemorySuggestDialog(
    suggest: com.localaiagent.app.MemorySuggest,
    onConfirm: (Boolean) -> Unit,
) {
    val scopeLabel = if (suggest.scope == "chat") stringResource(R.string.mem_this_chat) else stringResource(R.string.mem_global)
    AlertDialog(
        onDismissRequest = { onConfirm(false) },
        icon = { Icon(Icons.Rounded.BookmarkAdd, null, tint = MaterialTheme.colorScheme.primary) },
        title = { Text(stringResource(R.string.mem_remember_q)) },
        text = {
            Column {
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = MaterialTheme.colorScheme.surfaceContainerHigh,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(suggest.text, fontSize = 15.sp, modifier = Modifier.padding(12.dp))
                }
                Spacer(Modifier.size(6.dp))
                Text(stringResource(R.string.mem_save_scope, scopeLabel), fontSize = 12.sp, color = MaterialTheme.colorScheme.outline)
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(true) }) { Text(stringResource(R.string.mem_remember)) } },
        dismissButton = { TextButton(onClick = { onConfirm(false) }) { Text(stringResource(R.string.mem_no)) } },
    )
}

/**
 * Согласие на выполнение действия (инструменты EXECUTE/NETWORK: run_shell, run_python,
 * pc_agent и т.п.). Три исхода: «один раз» / «всегда в этом прогоне» / «отклонить».
 */
@Composable
fun ApprovalDialog(req: com.localaiagent.app.ApprovalRequest, onDecision: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = { onDecision("deny") },
        // Alti asks, like on the PC cards that need the user.
        icon = { AltiMascot(size = 52.dp, satellites = false, mood = AltiMood.Help) },
        title = { Text(stringResource(R.string.approve_title)) },
        text = {
            Column {
                Text(stringResource(R.string.approve_body, req.name), fontSize = 15.sp)
                if (req.secrets.isNotBlank()) {
                    Spacer(Modifier.size(8.dp))
                    Surface(
                        shape = RoundedCornerShape(12.dp),
                        color = MaterialTheme.colorScheme.errorContainer,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            stringResource(R.string.approve_uses_secrets, req.secrets),
                            fontSize = 13.sp,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            modifier = Modifier.padding(12.dp),
                        )
                    }
                }
                if (req.reason.isNotBlank()) {
                    Spacer(Modifier.size(6.dp))
                    Text(req.reason, fontSize = 12.sp, color = MaterialTheme.colorScheme.outline)
                }
                if (req.detail.isNotBlank()) {
                    Spacer(Modifier.size(8.dp))
                    Surface(
                        shape = RoundedCornerShape(12.dp),
                        color = MaterialTheme.colorScheme.surfaceContainerHigh,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            req.detail,
                            fontSize = 13.sp,
                            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                            modifier = Modifier.padding(12.dp),
                        )
                    }
                }
            }
        },
        confirmButton = {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TextButton(onClick = { onDecision("once") }) { Text(stringResource(R.string.approve_once)) }
                // A secret-bearing call is confirmed every time, so "always" would only mislead.
                if (req.secrets.isBlank()) {
                    TextButton(onClick = { onDecision("always") }) { Text(stringResource(R.string.sec_always)) }
                }
            }
        },
        dismissButton = { TextButton(onClick = { onDecision("deny") }) { Text(stringResource(R.string.approve_reject)) } },
    )
}

/** Подтверждение использования секрета с доступом «с разрешения». */
@Composable
fun SecretConfirmDialog(name: String, onAllow: () -> Unit, onDeny: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDeny,
        icon = { Icon(Icons.Rounded.Key, null, tint = MaterialTheme.colorScheme.primary) },
        title = { Text(stringResource(R.string.secreq_title)) },
        text = { Text(stringResource(R.string.secreq_body, name)) },
        confirmButton = { TextButton(onClick = onAllow) { Text(stringResource(R.string.allow)) } },
        dismissButton = { TextButton(onClick = onDeny) { Text(stringResource(R.string.deny)) } },
    )
}

/** Вкладка «Хранилище секретов»: список + добавление + управление доступом. */
@Composable
fun SecretsScreen(
    secrets: List<Secret>,
    onAdd: (name: String, value: String, availability: String, purpose: String) -> Unit,
    onRemove: (String) -> Unit,
    onSetAvailability: (String, String) -> Unit,
    onClose: () -> Unit,
) {
    var adding by remember { mutableStateOf(false) }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize()) {
                Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onClose) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, stringResource(R.string.action_back)) }
                    Text(stringResource(R.string.secrets_title), style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
                    IconButton(onClick = { adding = true }) { Icon(Icons.Rounded.Add, stringResource(R.string.action_add)) }
                }
                if (secrets.isEmpty()) {
                    AltiEmpty(stringResource(R.string.secrets_empty), Modifier.fillMaxWidth())
                } else {
                    LazyColumn(Modifier.fillMaxWidth().padding(horizontal = 12.dp)) {
                        items(secrets) { s -> SecretRow(s, onRemove, onSetAvailability) }
                    }
                }
            }
        }
    }
    if (adding) {
        SecretEditDialog(onSave = { n, v, a, p -> onAdd(n, v, a, p); adding = false }, onCancel = { adding = false })
    }
}

@Composable
private fun SecretRow(s: Secret, onRemove: (String) -> Unit, onSetAvailability: (String, String) -> Unit) {
    Surface(
        Modifier.fillMaxWidth().padding(vertical = 5.dp), shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Rounded.Key, null, tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(s.name, color = MaterialTheme.colorScheme.onSurface)
                Text("•••••••  ·  " + (if (s.availability == "always") stringResource(R.string.sec_state_always) else stringResource(R.string.sec_state_ask)),
                    fontSize = 12.sp, color = MaterialTheme.colorScheme.outline)
                if (s.purpose.isNotBlank()) Text(s.purpose, fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
            }
            val next = if (s.availability == "always") "ask" else "always"
            TextButton(onClick = { onSetAvailability(s.name, next) }) {
                Text(if (s.availability == "always") stringResource(R.string.sec_switch_ask) else stringResource(R.string.sec_switch_always), fontSize = 12.sp)
            }
            IconButton(onClick = { onRemove(s.name) }) { Icon(Icons.Rounded.Delete, stringResource(R.string.action_delete)) }
        }
    }
}

@Composable
private fun SecretEditDialog(
    onSave: (name: String, value: String, availability: String, purpose: String) -> Unit,
    onCancel: () -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var value by remember { mutableStateOf("") }
    var purpose by remember { mutableStateOf("") }
    var availability by remember { mutableStateOf("ask") }
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text(stringResource(R.string.sec_new)) },
        text = {
            Column {
                OutlinedTextField(name, { name = it }, label = { Text(stringResource(R.string.mcp_field_name)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.size(6.dp))
                OutlinedTextField(
                    value, { value = it }, label = { Text(stringResource(R.string.sec_value)) }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.size(6.dp))
                OutlinedTextField(purpose, { purpose = it }, label = { Text(stringResource(R.string.sec_purpose)) }, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.size(8.dp))
                Row {
                    FilterChip(availability == "always", { availability = "always" }, { Text(stringResource(R.string.sec_always)) })
                    Spacer(Modifier.width(8.dp))
                    FilterChip(availability == "ask", { availability = "ask" }, { Text(stringResource(R.string.sec_on_permission)) })
                }
            }
        },
        confirmButton = { TextButton(onClick = { if (name.isNotBlank()) onSave(name, value, availability, purpose) }) { Text(stringResource(R.string.action_save)) } },
        dismissButton = { TextButton(onClick = onCancel) { Text(stringResource(R.string.action_cancel)) } },
    )
}
