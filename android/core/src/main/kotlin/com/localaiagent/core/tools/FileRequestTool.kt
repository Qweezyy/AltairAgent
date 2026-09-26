package com.localaiagent.core.tools

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * Просит пользователя прикрепить нужный файл (форма в приложении). Модель задаёт:
 * какие типы принимает, один/несколько, макс. размер, обязательность и назначение.
 * Возвращает пути прикреплённых файлов (в папке чата) — их можно читать read_file/read_table.
 */
class RequestFileTool : Tool {
    override val name = "request_file"
    override val description =
        "Просит пользователя прикрепить файл (откроется форма). Указывай purpose (зачем нужен). " +
            "accept — типы (напр. 'image/*', 'application/pdf', 'csv' или '*/*'); multiple — можно " +
            "несколько; max_mb — лимит; required — обязателен ли. Возвращает пути файлов для чтения."
    override val category = ToolCategory.READ

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("purpose") { put("type", "string"); put("description", "Зачем нужен файл (покажем пользователю)") }
            putJsonObject("accept") { put("type", "string"); put("description", "Допустимые типы (mime/расширения или */*)") }
            putJsonObject("multiple") { put("type", "boolean"); put("description", "Разрешить несколько файлов") }
            putJsonObject("max_mb") { put("type", "number"); put("description", "Максимальный размер, МБ") }
            putJsonObject("required") { put("type", "boolean"); put("description", "Обязателен ли файл") }
        }
        putJsonArray("required") { add("purpose") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val answer = ctx.requestUi("file", args)
        val files = answer["files"]?.jsonArray
        if (files.isNullOrEmpty()) {
            val required = args["required"]?.jsonPrimitive?.contentOrNull?.toBoolean() ?: false
            return ToolResult(if (required) "Пользователь не прикрепил обязательный файл." else "Файл не прикреплён.")
        }
        val lines = files.joinToString("\n") { f ->
            val o = f.jsonObject
            val path = o["path"]?.jsonPrimitive?.contentOrNull ?: ""
            val name = o["name"]?.jsonPrimitive?.contentOrNull ?: path
            "• $path ($name)"
        }
        return ToolResult(
            "Пользователь прикрепил файлы (в папке чата) — читай read_file/read_table:\n$lines",
        )
    }
}
