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

/**
 * Читает веб-страницу и возвращает её основной текст (Readability-lite): убирает
 * навигацию/скрипты/подвалы, берёт «статейный» контейнер. Пара к web_search: нашёл
 * ссылку — прочитал страницу. dep-free (jsoup), работает на Dispatchers.IO.
 *
 * Длинные страницы читаются кусками: `offset` сдвигает окно, а в хвосте ответа —
 * прямая подсказка, каким offset дочитать дальше. Полный текст страницы кэшируется
 * в рамках прогона (ctx.scratch), поэтому пагинация не перезагружает страницу.
 */
class FetchUrlTool : Tool {
    override val name = "fetch_url"
    override val description =
        "Загружает страницу по URL и возвращает её основной ЧИТАЕМЫЙ текст (без меню, " +
            "рекламы и скриптов). Используй после web_search или когда пользователь прислал " +
            "ссылку. Длинную страницу отдаёт кусками: если текст обрезан, в конце будет " +
            "offset для следующего вызова — читай дальше, пока нужно. max_chars — размер " +
            "куска (по умолчанию 6000, до 40000), offset — с какого символа читать."
    override val category = ToolCategory.NETWORK

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("url") { put("type", "string"); put("description", "Адрес страницы (http/https)") }
            putJsonObject("max_chars") {
                put("type", "integer"); put("description", "Размер куска в символах (по умолчанию 6000, до 40000)")
            }
            putJsonObject("offset") {
                put("type", "integer")
                put("description", "С какого символа читать (для продолжения длинной страницы; по умолчанию 0)")
            }
        }
        putJsonArray("required") { add("url") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val rawUrl = args["url"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (rawUrl.isEmpty()) return ToolResult.fail("не задан url")
        val url = normalizeUrl(rawUrl)
        val maxChars = args["max_chars"]?.jsonPrimitive?.contentOrNull?.toIntOrNull()
            ?.coerceIn(500, 40_000) ?: 6_000
        val offset = args["offset"]?.jsonPrimitive?.contentOrNull?.toIntOrNull()?.coerceAtLeast(0) ?: 0

        // Кэш полного текста страницы на время прогона — пагинация без перезагрузки.
        val cacheKey = "fetch:$url"
        val cached = ctx.scratch[cacheKey] as? ReadablePage
        val page = cached ?: run {
            val fetched = withContext(Dispatchers.IO) { runCatching { extractReadable(url) }.getOrNull() }
                ?: return ToolResult.fail("не удалось прочитать «$url»")
            ctx.scratch[cacheKey] = fetched
            fetched
        }
        return ToolResult(formatReadable(page, maxChars, offset))
    }
}

/** Извлечённая читаемая страница: заголовок, канонический url и ПОЛНЫЙ текст. */
internal data class ReadablePage(val title: String, val url: String, val body: String)

/** Нормализует URL (добавляет https://, если схемы нет). */
internal fun normalizeUrl(raw: String): String {
    val u = raw.trim().trimEnd('.', ',', ')', ']', '»', '"', '\'')
    return if (u.startsWith("http")) u else "https://$u"
}

private const val FU_UA =
    "Mozilla/5.0 (LocalAIAgent) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"

// Контейнеры, где обычно лежит основной текст статьи (в порядке предпочтения).
private val ARTICLE_SELECTORS = listOf(
    "article", "main", "[role=main]", "#content", "#main", ".post-content",
    ".article-body", ".article__body", ".entry-content", ".content",
)

/**
 * Загружает страницу и извлекает ПОЛНЫЙ читаемый текст (без обрезки). БЛОКИРУЮЩАЯ —
 * вызывать на Dispatchers.IO. Бросает исключение при сбое.
 */
internal fun extractReadable(rawUrl: String): ReadablePage {
    val url = normalizeUrl(rawUrl)
    val doc = Jsoup.connect(url)
        .userAgent(FU_UA).timeout(15_000).followRedirects(true)
        .maxBodySize(8 * 1024 * 1024).get()

    // Выкидываем заведомо не-контентные узлы.
    doc.select("script, style, noscript, nav, header, footer, aside, form, iframe, svg").remove()

    val title = doc.title().trim()
    val root = ARTICLE_SELECTORS.firstNotNullOfOrNull { sel ->
        doc.selectFirst(sel)?.takeIf { it.text().length >= 200 }
    } ?: doc.body() ?: error("пустая страница")

    // Собираем текст по абзацам/заголовкам/пунктам — так читаемее сплошного text().
    val parts = root.select("p, h1, h2, h3, h4, li, blockquote")
        .map { it.text().trim() }
        .filter { it.length > 1 }
    val body = (if (parts.isNotEmpty()) parts.joinToString("\n\n") else root.text())
        .replace(Regex("[ \\t]+"), " ")
        .replace(Regex("\\n{3,}"), "\n\n")
        .trim()

    if (body.isEmpty()) error("на странице не нашлось читаемого текста")
    return ReadablePage(title, url, body)
}

/** Форматирует окно [offset, offset+maxChars) с заголовком и подсказкой «читать дальше». */
internal fun formatReadable(page: ReadablePage, maxChars: Int, offset: Int): String {
    val total = page.body.length
    val start = offset.coerceIn(0, total)
    val end = (start + maxChars).coerceAtMost(total)
    val slice = page.body.substring(start, end)

    val header = if (page.title.isNotEmpty()) "# ${page.title}\n(${page.url})\n\n" else "(${page.url})\n\n"
    val pre = if (start > 0) "…[продолжение с символа $start из $total]\n\n" else ""
    val tail = when {
        end < total ->
            "\n\n…[показаны символы $start–$end из $total. Чтобы прочитать дальше, вызови " +
                "fetch_url с тем же url и offset=$end]"
        start > 0 -> "\n\n…[конец страницы; всего $total символов]"
        else -> ""
    }
    return header + pre + slice + tail
}

/** Удобная обёртка (offset 0) — для deep_research и разовых чтений. Блокирующая. */
internal fun fetchReadable(rawUrl: String, maxChars: Int): String =
    formatReadable(extractReadable(rawUrl), maxChars, 0)
