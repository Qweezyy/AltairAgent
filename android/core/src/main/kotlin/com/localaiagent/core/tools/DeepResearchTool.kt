package com.localaiagent.core.tools

import com.localaiagent.core.AgentEvent
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * Мини-«глубокое исследование»: ищет в интернете, ОТКРЫВАЕТ несколько источников и
 * возвращает их читаемый текст со ссылками — модель дальше синтезирует ответ с
 * цитированием. Лёгкая версия ПК-пайплайна: без рендер-браузера, только jsoup.
 */
class DeepResearchTool : Tool {
    override val name = "deep_research"
    override val description =
        "Исследует тему: ищет в интернете И читает несколько найденных страниц, возвращая " +
            "их основной текст со ссылками. Используй для вопросов, где нужны детали из " +
            "нескольких источников (обзор, сравнение, «расскажи подробно про…»). Дороже " +
            "web_search — для простых фактов хватит его."
    override val category = ToolCategory.NETWORK

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("query") { put("type", "string"); put("description", "Тема/вопрос исследования") }
            putJsonObject("max_sources") {
                put("type", "integer"); put("description", "Сколько страниц открыть (1–5, по умолчанию 3)")
            }
        }
        putJsonArray("required") { add("query") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val query = args["query"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (query.isEmpty()) return ToolResult.fail("пустая тема")
        val maxSources = (args["max_sources"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 3)
            .coerceIn(1, 5)
        // На источник берём меньше символов, чтобы уложиться в контекст.
        val perSource = (12_000 / maxSources).coerceIn(1_500, 4_000)

        return withContext(Dispatchers.IO) {
            val hits = searchWeb(query, maxSources * 2)
            if (hits.isEmpty()) return@withContext ToolResult("По теме «$query» ничего не нашлось.")

            val sections = mutableListOf<String>()
            var read = 0
            for (hit in hits) {
                if (read >= maxSources) break
                ctx.emit(AgentEvent.ToolStarted(randomToolId(), "fetch_url", buildJsonObject { put("url", hit.url) }))
                val text = runCatching { fetchReadable(hit.url, perSource) }.getOrNull()
                if (text.isNullOrBlank()) {
                    ctx.emit(AgentEvent.ToolFinished(hit.url, "fetch_url", false, "пропущено (пусто/ошибка)"))
                    continue
                }
                read++
                sections += "## Источник $read: ${hit.title}\n$text"
                ctx.emit(AgentEvent.ToolFinished(hit.url, "fetch_url", true, "прочитано ${text.length} символов"))
            }
            if (sections.isEmpty()) {
                return@withContext ToolResult("Нашёл ссылки по «$query», но ни одну страницу не удалось прочитать.")
            }
            ToolResult(
                "Материалы по теме «$query» ($read источник(ов)). Синтезируй ответ и сошлись " +
                    "на источники ссылками:\n\n" + sections.joinToString("\n\n---\n\n"),
            )
        }
    }
}

private fun randomToolId(): String {
    val pool = "0123456789abcdef"
    return "dr_" + buildString { repeat(8) { append(pool[(0..15).random()]) } }
}
