package com.localaiagent.core.tools

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
 * Секреты: модель просит пользователя ввести секрет (форма), но НЕ видит значение.
 * Использовать секрет в командах — через плейсхолдер {{secret:ИМЯ}} (run_shell подставит).
 */

class RequestSecretTool : Tool {
    override val name = "request_secret"
    override val description =
        "Просит пользователя ввести секрет (API-ключ, токен, пароль) в защищённую форму. " +
            "name — предлагаемое имя (пользователь может изменить), purpose — зачем нужен. " +
            "Ты НЕ увидишь значение; используй его в командах как {{secret:ИМЯ}} (run_shell подставит). " +
            "Секрет сохраняется в хранилище и доступен во всех чатах."
    override val category = ToolCategory.READ

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("name") { put("type", "string"); put("description", "Имя секрета (напр. OPENAI_API_KEY)") }
            putJsonObject("purpose") { put("type", "string"); put("description", "Зачем нужен (покажем пользователю)") }
        }
        putJsonArray("required") { add("name") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val answer = ctx.requestUi("secret", args)
        val saved = answer["name"]?.jsonPrimitive?.contentOrNull
        return if (saved.isNullOrBlank()) ToolResult("Пользователь не ввёл секрет.")
        else ToolResult("Секрет «$saved» сохранён (значение скрыто от тебя). Используй как {{secret:$saved}}.")
    }
}

class ListSecretsTool : Tool {
    override val name = "list_secrets"
    override val description =
        "Показывает доступные секреты (имена, доступность always/ask, назначение) — БЕЗ значений. " +
            "Используй, чтобы понять, какие ключи уже есть, прежде чем просить новый."
    override val category = ToolCategory.READ
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object"); putJsonObject("properties") {}
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val info = ctx.secretsInfo()
        return if (info.isEmpty()) ToolResult("Секретов пока нет. Нужен — вызови request_secret.")
        else ToolResult("Доступные секреты (используй как {{secret:ИМЯ}}):\n" + info.joinToString("\n") { "• $it" })
    }
}
