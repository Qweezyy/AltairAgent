package com.localaiagent.core.tools

import com.localaiagent.core.AgentEvent
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import org.jsoup.Jsoup
import java.net.URLEncoder

/**
 * Показ картинки в ответе: находит релевантное изображение сущности (Wikipedia
 * pageimages + Wikimedia Commons, без ключей) и эмитит [AgentEvent.ShowImage] — UI
 * рисует его в ленте. Fetch JSON через jsoup, парсинг — kotlinx.serialization.
 */
class ShowImageTool : Tool {
    override val name = "show_image"
    override val description =
        "Показывает пользователю картинку по запросу (персонаж, место, устройство, объект). " +
            "Находит релевантное изображение и выводит его прямо в чат. Используй, когда картинка " +
            "помогает понять, о чём речь. Для сугубо технических/абстрактных тем не нужно."
    override val category = ToolCategory.NETWORK

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("query") { put("type", "string"); put("description", "Что показать (с контекстом)") }
            putJsonObject("caption") { put("type", "string"); put("description", "Короткая подпись") }
        }
        putJsonArray("required") { add("query") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val query = args["query"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (query.isEmpty()) return ToolResult.fail("пустой запрос")
        val caption = args["caption"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()

        val url = withContext(Dispatchers.IO) { runCatching { findImageUrl(query) }.getOrNull() }
            ?: return ToolResult("Подходящей картинки по «$query» не нашлось.")

        ctx.emit(AgentEvent.ShowImage(url, caption))
        return ToolResult("Показал пользователю картинку по «$query».")
    }
}

private val JSON = Json { ignoreUnknownKeys = true }
private const val UA =
    "Mozilla/5.0 (LocalAIAgent) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
private val JUNK = Regex("logo|icon|favicon|placeholder|sprite|avatar|banner|\\.svg", RegexOption.IGNORE_CASE)

private fun okImage(url: String): Boolean =
    url.startsWith("http") && !JUNK.containsMatchIn(url)

private fun fetchJson(url: String): JsonObject? = runCatching {
    val body = Jsoup.connect(url).ignoreContentType(true).userAgent(UA).timeout(12000).execute().body()
    JSON.parseToJsonElement(body).jsonObject
}.getOrNull()

/** Лучшая одна картинка по запросу: Wikipedia (ru→en) → Commons. */
internal fun findImageUrl(query: String): String? {
    val q = URLEncoder.encode(query, "UTF-8")
    for (lang in listOf("ru", "en")) {
        // Берём НЕСКОЛЬКО результатов поиска: у топ-хита pageimage может не быть,
        // поэтому идём по порядку релевантности (index) и берём первый с картинкой.
        val url = "https://$lang.wikipedia.org/w/api.php?action=query&generator=search" +
            "&gsrsearch=$q&gsrlimit=6&prop=pageimages&piprop=original|thumbnail&pithumbsize=800&format=json"
        val pages = fetchJson(url)?.get("query")?.jsonObject?.get("pages")?.jsonObject ?: continue
        val ordered = pages.values.map { it.jsonObject }
            .sortedBy { it["index"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: Int.MAX_VALUE }
        for (page in ordered) {
            val src = page["original"]?.jsonObject?.get("source")?.jsonPrimitive?.contentOrNull
                ?: page["thumbnail"]?.jsonObject?.get("source")?.jsonPrimitive?.contentOrNull
            if (src != null && okImage(src)) return src
        }
    }
    // Commons — запасной источник.
    val commons = "https://commons.wikimedia.org/w/api.php?action=query&generator=search" +
        "&gsrsearch=$q&gsrnamespace=6&gsrlimit=6&prop=imageinfo&iiprop=url&iiurlwidth=800&format=json"
    val pages = fetchJson(commons)?.get("query")?.jsonObject?.get("pages")?.jsonObject ?: return null
    for ((_, pageEl) in pages) {
        val info = pageEl.jsonObject["imageinfo"]?.jsonArray?.firstOrNull()?.jsonObject ?: continue
        val src = info["thumburl"]?.jsonPrimitive?.contentOrNull ?: info["url"]?.jsonPrimitive?.contentOrNull
        if (src != null && okImage(src) && Regex("\\.(jpg|jpeg|png|webp)", RegexOption.IGNORE_CASE).containsMatchIn(src)) {
            return src
        }
    }
    return null
}
