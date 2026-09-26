package com.localaiagent.core.tools

import com.localaiagent.core.AgentEvent
import com.localaiagent.core.Message
import com.localaiagent.core.Part
import com.localaiagent.core.Role
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * Инструменты самоконтроля контекстного окна: узнать заполнение и разбивку,
 * сжать старое (модель сама даёт резюме) или выкинуть тяжёлое (tool-результаты/картинки).
 */

private fun cstr(desc: String): JsonObject = buildJsonObject { put("type", "string"); put("description", desc) }

private fun cObj(props: Map<String, JsonObject>, required: List<String>): JsonObject = buildJsonObject {
    put("type", "object")
    putJsonObject("properties") { props.forEach { (k, v) -> put(k, v) } }
    putJsonArray("required") { required.forEach { add(it) } }
}

private fun JsonObject.v(key: String): String = this[key]?.jsonPrimitive?.contentOrNull.orEmpty()

/** Оценка токенов сообщения (как в Session: ~4 символа на токен, картинка ~800). */
private fun estTokens(m: Message): Int =
    (m.content.length + m.parts.sumOf { p -> if (p is Part.Text) p.text.length else 800 }) / 4

class ContextInfoTool : Tool {
    override val name = "context_info"
    override val description =
        "Показывает состояние контекстного окна: всего токенов, занято, свободно, процент, " +
            "и разбивку (системный промпт / твои сообщения / сообщения пользователя / результаты инструментов)."
    override val category = ToolCategory.READ
    override fun schema() = cObj(emptyMap(), emptyList())

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val s = ctx.session ?: return ToolResult.fail("контекст недоступен")
        val total = ctx.contextWindow
        val sys = s.systemPrompt.length / 4
        var user = 0; var assistant = 0; var tool = 0
        for (m in s.messages) when (m.role) {
            Role.USER -> user += estTokens(m)
            Role.ASSISTANT -> assistant += estTokens(m)
            Role.TOOL -> tool += estTokens(m)
            Role.SYSTEM -> {}
        }
        val used = sys + user + assistant + tool
        val pct = if (total > 0) used * 100 / total else 0
        fun row(name: String, t: Int) = "  • $name: $t (${if (used > 0) t * 100 / used else 0}%)"
        return ToolResult(
            buildString {
                append("Контекст: занято $used из $total токенов ($pct%). Свободно ~${(total - used).coerceAtLeast(0)}.\n")
                append("Разбивка:\n")
                append(row("системный промпт", sys)).append("\n")
                append(row("твои ответы", assistant)).append("\n")
                append(row("сообщения пользователя", user)).append("\n")
                append(row("результаты инструментов", tool))
            },
        )
    }
}

class ContextCompressTool : Tool {
    override val name = "context_compress"
    override val description =
        "Сжимает историю: заменяет всё, кроме последних keep_last сообщений, одним кратким " +
            "резюме (его пишешь ТЫ в поле summary — сохрани важные факты и решения). " +
            "Экономит контекст, сохраняя суть."
    override val category = ToolCategory.EDIT
    override fun schema() = cObj(
        mapOf(
            "summary" to cstr("Краткое резюме сворачиваемой части (важные факты/решения)"),
            "keep_last" to cstr("Сколько последних сообщений оставить как есть (по умолчанию 4)"),
        ),
        listOf("summary"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val s = ctx.session ?: return ToolResult.fail("контекст недоступен")
        val summary = args.v("summary").trim()
        if (summary.isEmpty()) return ToolResult.fail("нужно summary")
        val keep = args.v("keep_last").toIntOrNull()?.coerceAtLeast(0) ?: 4
        val msgs = s.messages
        if (msgs.size <= keep + 1) return ToolResult("Сжимать нечего (сообщений мало).")
        val tail = msgs.takeLast(keep)
        val note = Message(Role.USER, "[Сжатый контекст предыдущей части беседы]\n$summary")
        s.loadHistory(listOf(note) + tail)
        ctx.emit(AgentEvent.ContextUsage(s.tokenEstimate()))
        return ToolResult("Сжато. Осталось ${tail.size + 1} сообщений, ~${s.tokenEstimate()} токенов.")
    }
}

class ContextDropTool : Tool {
    override val name = "context_drop"
    override val description =
        "Убирает из контекста тяжёлое: what=tools (все результаты инструментов) | " +
            "images (вложения-картинки из истории). Видимый чат не меняется — только память модели."
    override val category = ToolCategory.EDIT
    override fun schema() = cObj(mapOf("what" to cstr("tools | images")), listOf("what"))

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val s = ctx.session ?: return ToolResult.fail("контекст недоступен")
        val before = s.tokenEstimate()
        val new = when (args.v("what").trim().lowercase()) {
            "tools" -> s.messages.filter { it.role != Role.TOOL }
            "images" -> s.messages.map { m ->
                if (m.parts.any { it is Part.Image }) m.copy(parts = m.parts.filter { it !is Part.Image })
                else m
            }
            else -> return ToolResult.fail("what должно быть: tools | images")
        }
        s.loadHistory(new)
        ctx.emit(AgentEvent.ContextUsage(s.tokenEstimate()))
        return ToolResult("Убрано. Было ~$before, стало ~${s.tokenEstimate()} токенов.")
    }
}
