@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.localaiagent.app.ui

import androidx.compose.ui.res.stringResource
import com.localaiagent.app.R

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ArrowDownward
import androidx.compose.material.icons.rounded.ArrowUpward
import androidx.compose.material.icons.rounded.Star
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.localaiagent.app.AskAnswer
import com.localaiagent.app.AskQuestion
import com.localaiagent.app.AskRequest

private const val CUSTOM = "__custom__"

/**
 * Форма раунда вопросов от ИИ (инструмент ask). Три типа: single/multi/rank.
 * single/multi — с пояснениями, пометкой «рекомендую» и полем своего ответа.
 * rank — расстановка вариантов по важности (стрелками вверх/вниз).
 */
@Composable
fun AskDialog(request: AskRequest, onSubmit: (List<AskAnswer>) -> Unit, onCancel: () -> Unit) {
    // Состояние ответов по каждому вопросу.
    val single = remember { mutableStateMapOf<String, String>() }          // qid -> optionId | CUSTOM
    val multi = remember { mutableStateMapOf<String, MutableList<String>>() }
    val custom = remember { mutableStateMapOf<String, String>() }
    val ranks = remember { mutableStateMapOf<String, MutableList<String>>() }
    remember(request) {
        request.questions.forEach { q ->
            if (q.type == "multi") multi[q.id] = mutableStateListOf()
            if (q.type == "rank") ranks[q.id] = mutableStateListOf<String>().apply { addAll(q.options.map { it.id }) }
        }
        true
    }

    Dialog(onDismissRequest = onCancel, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(
            Modifier.fillMaxWidth().padding(12.dp), shape = RoundedCornerShape(22.dp),
            color = MaterialTheme.colorScheme.surface,
        ) {
            Column(Modifier.padding(18.dp)) {
                Text(stringResource(R.string.ask_title), style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface)
                Spacer(Modifier.size(8.dp))
                Column(Modifier.verticalScroll(rememberScrollState()).heightIn(max = 520.dp)) {
                    request.questions.forEach { q ->
                        QuestionBlock(q, single, multi, custom, ranks)
                        Spacer(Modifier.size(14.dp))
                    }
                }
                Spacer(Modifier.size(6.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onCancel) { Text(stringResource(R.string.action_cancel)) }
                    Spacer(Modifier.width(4.dp))
                    TextButton(onClick = {
                        onSubmit(buildAnswers(request, single, multi, custom, ranks))
                    }) { Text(stringResource(R.string.ask_done)) }
                }
            }
        }
    }
}

@Composable
private fun QuestionBlock(
    q: AskQuestion,
    single: MutableMap<String, String>,
    multi: MutableMap<String, MutableList<String>>,
    custom: MutableMap<String, String>,
    ranks: MutableMap<String, MutableList<String>>,
) {
    Text(q.title, fontWeight = FontWeight.SemiBold, fontSize = 16.sp,
        color = MaterialTheme.colorScheme.onSurface)
    Spacer(Modifier.size(6.dp))
    when (q.type) {
        "rank" -> RankOptions(q, ranks)
        "multi" -> q.options.forEach { opt ->
            val list = multi.getOrPut(q.id) { mutableStateListOf() }
            OptionRow(opt.label, opt.explanation, opt.recommended, checkbox = true, selected = opt.id in list) {
                if (opt.id in list) list.remove(opt.id) else list.add(opt.id)
            }
        }
        else -> q.options.forEach { opt ->
            OptionRow(opt.label, opt.explanation, opt.recommended, checkbox = false, selected = single[q.id] == opt.id) {
                single[q.id] = opt.id
            }
        }
    }
    // Поле своего варианта — только single/multi с allow_custom.
    if (q.allowCustom && q.type != "rank") {
        if (q.type != "multi") {
            OptionRow(stringResource(R.string.ask_custom), "", false, checkbox = false, selected = single[q.id] == CUSTOM) {
                single[q.id] = CUSTOM
            }
        }
        OutlinedTextField(
            value = custom[q.id] ?: "", onValueChange = { custom[q.id] = it },
            modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
            placeholder = { Text(stringResource(R.string.ask_custom_hint)) }, singleLine = true,
        )
    }
}

@Composable
private fun OptionRow(
    label: String, explanation: String, recommended: Boolean,
    checkbox: Boolean, selected: Boolean, onClick: () -> Unit,
) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.Top) {
        if (checkbox) Checkbox(checked = selected, onCheckedChange = { onClick() })
        else RadioButton(selected = selected, onClick = onClick)
        Column(Modifier.padding(top = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(label, fontWeight = if (recommended) FontWeight.Bold else FontWeight.Normal,
                    color = MaterialTheme.colorScheme.onSurface)
                if (recommended) {
                    Spacer(Modifier.width(6.dp))
                    Icon(Icons.Rounded.Star, stringResource(R.string.ask_recommended), Modifier.size(15.dp),
                        tint = MaterialTheme.colorScheme.primary)
                    Text(" " + stringResource(R.string.ask_recommended), fontSize = 11.sp, color = MaterialTheme.colorScheme.primary)
                }
            }
            if (explanation.isNotBlank()) {
                Text(explanation, fontSize = 12.sp, color = MaterialTheme.colorScheme.outline,
                    modifier = Modifier.padding(end = 8.dp))
            }
        }
    }
}

@Composable
private fun RankOptions(q: AskQuestion, ranks: MutableMap<String, MutableList<String>>) {
    val order = ranks.getOrPut(q.id) { mutableStateListOf<String>().apply { addAll(q.options.map { it.id }) } }
    val labelById = q.options.associate { it.id to it.label }
    val explById = q.options.associate { it.id to it.explanation }
    Column {
        order.forEachIndexed { i, id ->
            Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("${i + 1}.", Modifier.width(24.dp), color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold)
                Column(Modifier.weight(1f)) {
                    Text(labelById[id] ?: id, color = MaterialTheme.colorScheme.onSurface)
                    (explById[id] ?: "").takeIf { it.isNotBlank() }?.let {
                        Text(it, fontSize = 12.sp, color = MaterialTheme.colorScheme.outline)
                    }
                }
                IconButton(onClick = { if (i > 0) { order.removeAt(i); order.add(i - 1, id) } }, enabled = i > 0) {
                    Icon(Icons.Rounded.ArrowUpward, stringResource(R.string.ask_above), Modifier.size(20.dp))
                }
                IconButton(
                    onClick = { if (i < order.size - 1) { order.removeAt(i); order.add(i + 1, id) } },
                    enabled = i < order.size - 1,
                ) { Icon(Icons.Rounded.ArrowDownward, stringResource(R.string.ask_below), Modifier.size(20.dp)) }
            }
        }
    }
}

private fun buildAnswers(
    request: AskRequest,
    single: Map<String, String>,
    multi: Map<String, MutableList<String>>,
    custom: Map<String, String>,
    ranks: Map<String, MutableList<String>>,
): List<AskAnswer> = request.questions.map { q ->
    val customText = custom[q.id].orEmpty().trim()
    when (q.type) {
        "rank" -> AskAnswer(q.id, emptyList(), "", ranks[q.id]?.toList() ?: q.options.map { it.id })
        "multi" -> AskAnswer(q.id, multi[q.id]?.toList() ?: emptyList(), customText, emptyList())
        else -> {
            val sel = single[q.id]
            if (sel == CUSTOM || sel == null) AskAnswer(q.id, emptyList(), customText, emptyList())
            else AskAnswer(q.id, listOf(sel), if (sel == CUSTOM) customText else "", emptyList())
        }
    }
}
