package com.localaiagent.core.tools

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.serialization.json.JsonArray
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
 * Раунд вопросов пользователю (как AskUserQuestion). До 5 вопросов, у каждого до 5
 * вариантов; тип: single (один) | multi (несколько) | rank (ранжировать). Для single/multi
 * можно свой вариант. К каждому варианту — короткое пояснение; можно пометить рекомендованный.
 * Блокирует до ответа пользователя и возвращает его выбор модели.
 */
class AskTool : Tool {
    override val name = "ask"
    override val description =
        "Задаёт пользователю раунд вопросов с вариантами (форма в приложении) и ждёт ответ. " +
            "Используй, когда нужно уточнить намерение/предпочтения перед действием. " +
            "questions: до 5 шт. Тип: single (выбрать один) | multi (несколько) | rank (расставить по важности). " +
            "У каждого варианта дай короткое explanation; можно пометить recommended=true. " +
            "allow_custom=true (для single/multi) добавит поле «свой вариант»."
    override val category = ToolCategory.READ

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("questions") {
                put("type", "array")
                put("description", "1–5 вопросов")
                putJsonObject("items") {
                    put("type", "object")
                    putJsonObject("properties") {
                        putJsonObject("id") { put("type", "string") }
                        putJsonObject("title") { put("type", "string"); put("description", "Текст вопроса") }
                        putJsonObject("type") {
                            put("type", "string")
                            putJsonArray("enum") { add("single"); add("multi"); add("rank") }
                        }
                        putJsonObject("allow_custom") { put("type", "boolean"); put("description", "Поле своего ответа (single/multi)") }
                        putJsonObject("options") {
                            put("type", "array")
                            put("description", "До 5 вариантов")
                            putJsonObject("items") {
                                put("type", "object")
                                putJsonObject("properties") {
                                    putJsonObject("id") { put("type", "string") }
                                    putJsonObject("label") { put("type", "string") }
                                    putJsonObject("explanation") { put("type", "string"); put("description", "Короткое пояснение варианта") }
                                    putJsonObject("recommended") { put("type", "boolean") }
                                }
                                putJsonArray("required") { add("id"); add("label") }
                            }
                        }
                    }
                    putJsonArray("required") { add("id"); add("title"); add("type"); add("options") }
                }
            }
        }
        putJsonArray("required") { add("questions") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val questions = args["questions"]?.jsonArray
        if (questions.isNullOrEmpty()) return ToolResult.fail("нужен непустой список questions")
        // Пробрасываем вопросы в UI как есть; получаем ответы пользователя.
        val payload = buildJsonObject { put("questions", questions) }
        val answers = ctx.requestUi("ask", payload)
        val arr = answers["answers"]?.jsonArray
        if (arr.isNullOrEmpty()) return ToolResult("Пользователь закрыл форму без ответа.")
        return ToolResult(formatAnswers(questions, arr))
    }

    private fun formatAnswers(questions: JsonArray, answers: JsonArray): String {
        val titleById = questions.associate { q ->
            val o = q.jsonObject
            (o["id"]?.jsonPrimitive?.contentOrNull ?: "") to (o["title"]?.jsonPrimitive?.contentOrNull ?: "")
        }
        val labelById = mutableMapOf<String, String>()
        questions.forEach { q ->
            q.jsonObject["options"]?.jsonArray?.forEach { opt ->
                val oo = opt.jsonObject
                val id = oo["id"]?.jsonPrimitive?.contentOrNull ?: return@forEach
                labelById[id] = oo["label"]?.jsonPrimitive?.contentOrNull ?: id
            }
        }
        val sb = StringBuilder("Ответы пользователя:\n")
        answers.forEach { a ->
            val ao = a.jsonObject
            val qid = ao["questionId"]?.jsonPrimitive?.contentOrNull ?: ""
            val title = titleById[qid] ?: qid
            val selected = ao["selected"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull } ?: emptyList()
            val ranking = ao["ranking"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull } ?: emptyList()
            val custom = ao["custom"]?.jsonPrimitive?.contentOrNull.orEmpty()
            sb.append("• $title: ")
            when {
                ranking.isNotEmpty() ->
                    sb.append("по важности → " + ranking.mapIndexed { i, id -> "${i + 1}) ${labelById[id] ?: id}" }.joinToString(", "))
                selected.isNotEmpty() -> sb.append(selected.joinToString(", ") { labelById[it] ?: it })
                else -> sb.append("—")
            }
            if (custom.isNotBlank()) sb.append("; свой вариант: «$custom»")
            sb.append("\n")
        }
        return sb.toString().trimEnd()
    }
}
