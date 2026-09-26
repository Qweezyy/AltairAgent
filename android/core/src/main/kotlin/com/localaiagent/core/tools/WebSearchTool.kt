package com.localaiagent.core.tools

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import kotlinx.serialization.json.add
import org.jsoup.Jsoup
import java.net.URLDecoder

/**
 * Поиск в интернете без ключей: DuckDuckGo HTML, с фолбэком на lite-версию. Fetch и
 * парсинг делает jsoup (блокирующе → на Dispatchers.IO). Возвращает заголовок, ссылку
 * и сниппет — как Python-версия `web_search`.
 */
class WebSearchTool : Tool {
    override val name = "web_search"
    override val description =
        "Ищет в интернете (DuckDuckGo) и возвращает заголовки, ссылки и краткие описания. " +
            "Используй для актуальных фактов, документации, новостей — всего, чего не знаешь наверняка."
    override val category = ToolCategory.NETWORK

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("query") { put("type", "string"); put("description", "Поисковый запрос") }
            putJsonObject("max_results") { put("type", "integer"); put("description", "Сколько результатов (1–10)") }
        }
        putJsonArray("required") { add("query") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val query = args["query"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (query.isEmpty()) return ToolResult.fail("пустой запрос")
        val limit = (args["max_results"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 6).coerceIn(1, 10)

        val hits = withContext(Dispatchers.IO) { searchWeb(query, limit) }
        if (hits.isEmpty()) return ToolResult("По запросу «$query» ничего не нашлось.")

        val body = hits.joinToString("\n\n") { "• ${it.title}\n  ${it.url}\n  ${it.snippet}" }
        return ToolResult("Результаты поиска по «$query»:\n\n$body")
    }
}

/** Результат поиска. Общий тип для web_search и deep_research. */
data class SearchHit(val title: String, val url: String, val snippet: String)

private const val SEARCH_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"

/**
 * Поиск DuckDuckGo (HTML → фолбэк lite). БЛОКИРУЮЩИЙ — вызывать на Dispatchers.IO.
 * Переиспользуется deep_research'ем (поиск → чтение источников).
 */
internal fun searchWeb(query: String, limit: Int): List<SearchHit> =
    runCatching { searchHtml(query, limit) }.getOrNull()?.takeIf { it.isNotEmpty() }
        ?: runCatching { searchLite(query, limit) }.getOrElse { emptyList() }

private fun searchHtml(query: String, limit: Int): List<SearchHit> {
    val doc = Jsoup.connect("https://html.duckduckgo.com/html/")
        .userAgent(SEARCH_UA).timeout(15000).data("q", query).post()
    return doc.select("div.result").mapNotNull { block ->
        val a = block.selectFirst("a.result__a") ?: return@mapNotNull null
        val url = cleanUrl(a.attr("href"))
        if (url.isBlank()) return@mapNotNull null
        SearchHit(a.text(), url, block.selectFirst(".result__snippet")?.text().orEmpty())
    }.take(limit)
}

private fun searchLite(query: String, limit: Int): List<SearchHit> {
    val doc = Jsoup.connect("https://lite.duckduckgo.com/lite/")
        .userAgent(SEARCH_UA).timeout(15000).data("q", query).get()
    val links = doc.select("a.result-link")
    val snippets = doc.select(".result-snippet")
    return links.mapIndexedNotNull { i, a ->
        val url = cleanUrl(a.attr("href"))
        if (url.isBlank()) return@mapIndexedNotNull null
        SearchHit(a.text(), url, snippets.getOrNull(i)?.text().orEmpty())
    }.take(limit)
}

/** DDG прячет реальный адрес за редиректом `...?uddg=<encoded>` — достаём его. */
private fun cleanUrl(href: String): String {
    val h = if (href.startsWith("//")) "https:$href" else href
    val idx = h.indexOf("uddg=")
    if (idx >= 0) {
        val raw = h.substring(idx + 5).substringBefore("&")
        return runCatching { URLDecoder.decode(raw, "UTF-8") }.getOrDefault(h)
    }
    return if (h.startsWith("http")) h else ""
}
