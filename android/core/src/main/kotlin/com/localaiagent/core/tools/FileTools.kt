package com.localaiagent.core.tools

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
import java.io.File
import java.math.BigDecimal
import java.math.MathContext
import java.util.zip.ZipFile

/**
 * Работа с файлами для модели (Задача 4): чтение текстовых И офисных форматов
 * (docx/xlsx/pptx — через zip/xml, без тяжёлых зависимостей), точные операции над
 * большими таблицами (csv/xlsx) и редактирование файлов прямо на телефоне.
 */

// -------------------------------------------------------- извлечение текста

/** Расширения, которые читаем как обычный UTF-8 текст. */
private val TEXT_EXT = setOf(
    "txt", "md", "markdown", "csv", "tsv", "json", "xml", "html", "htm", "yaml", "yml",
    "toml", "ini", "cfg", "conf", "log", "sql", "kt", "kts", "java", "py", "js", "ts",
    "tsx", "jsx", "c", "h", "cpp", "hpp", "cc", "rs", "go", "rb", "php", "sh", "bat",
    "gradle", "properties", "env", "gitignore", "dockerfile", "makefile", "svg",
)

private fun unescapeXml(s: String): String {
    var r = s
    // Числовые ссылки: &#1040; (dec) и &#x410; (hex) — office так хранит кириллицу.
    r = Regex("&#(\\d+);").replace(r) { m ->
        m.groupValues[1].toIntOrNull()?.let { String(Character.toChars(it)) } ?: m.value
    }
    r = Regex("&#[xX]([0-9a-fA-F]+);").replace(r) { m ->
        m.groupValues[1].toIntOrNull(16)?.let { String(Character.toChars(it)) } ?: m.value
    }
    return r.replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", "\"").replace("&apos;", "'").replace("&amp;", "&")
}

/** Текст из .docx (word/document.xml): абзацы <w:p>, текст в <w:t>. */
internal fun extractDocx(file: File): String {
    ZipFile(file).use { zip ->
        val entry = zip.getEntry("word/document.xml") ?: return ""
        val xml = zip.getInputStream(entry).bufferedReader(Charsets.UTF_8).use { it.readText() }
        val withBreaks = xml
            .replace(Regex("</w:p>"), "\n")
            .replace(Regex("<w:tab/?>"), "\t")
            .replace(Regex("<w:br/?>"), "\n")
        val text = withBreaks.replace(Regex("<[^>]+>"), "")
        return unescapeXml(text).replace(Regex("\n{3,}"), "\n\n").trim()
    }
}

/** Текст из .pptx: все слайды, тексты в <a:t>. */
internal fun extractPptx(file: File): String {
    ZipFile(file).use { zip ->
        val slides = zip.entries().toList()
            .filter { it.name.matches(Regex("ppt/slides/slide\\d+\\.xml")) }
            .sortedBy { it.name.filter { c -> c.isDigit() }.toIntOrNull() ?: 0 }
        val sb = StringBuilder()
        for ((i, e) in slides.withIndex()) {
            val xml = zip.getInputStream(e).bufferedReader(Charsets.UTF_8).use { it.readText() }
            val texts = Regex("<a:t>(.*?)</a:t>", RegexOption.DOT_MATCHES_ALL).findAll(xml)
                .map { unescapeXml(it.groupValues[1]) }.filter { it.isNotBlank() }.toList()
            if (texts.isNotEmpty()) {
                sb.append("## Слайд ${i + 1}\n").append(texts.joinToString("\n")).append("\n\n")
            }
        }
        return sb.toString().trim()
    }
}

/** Публичный ридер текста файла (для превью в библиотеке приложения). */
fun readableFileText(file: File): String? = extractText(file)

/** Публичный парсер таблицы csv/tsv/xlsx в строки (для табличного просмотра в UI). */
fun parseTableRows(file: File): List<List<String>> = runCatching { parseTable(file) }.getOrDefault(emptyList())

/** Общий извлекатель текста: office → текст; текстовые → как есть; иначе null. */
internal fun extractText(file: File): String? {
    val ext = file.extension.lowercase()
    return when (ext) {
        "docx" -> runCatching { extractDocx(file) }.getOrNull()
        "pptx" -> runCatching { extractPptx(file) }.getOrNull()
        "xlsx" -> runCatching { parseXlsx(file).joinToString("\n") { it.joinToString(" | ") } }.getOrNull()
        in TEXT_EXT -> runCatching { file.readText(Charsets.UTF_8) }.getOrNull()
        else -> if (file.name.equals("Dockerfile", true) || file.name.equals("Makefile", true))
            runCatching { file.readText() }.getOrNull() else null
    }
}

// -------------------------------------------------------- таблицы (csv/xlsx)

/** Парсит csv/xlsx в строки ячеек. */
internal fun parseTable(file: File): List<List<String>> = when (file.extension.lowercase()) {
    "csv" -> parseCsv(file.readText(Charsets.UTF_8), ',')
    "tsv" -> parseCsv(file.readText(Charsets.UTF_8), '\t')
    "xlsx" -> parseXlsx(file)
    else -> emptyList()
}

/** Простой CSV-парсер с кавычками. */
internal fun parseCsv(text: String, sep: Char): List<List<String>> {
    val rows = mutableListOf<List<String>>()
    val row = mutableListOf<String>()
    val cell = StringBuilder()
    var inQuotes = false
    var i = 0
    fun endCell() { row.add(cell.toString()); cell.clear() }
    fun endRow() { endCell(); rows.add(row.toList()); row.clear() }
    while (i < text.length) {
        val c = text[i]
        when {
            inQuotes -> when {
                c == '"' && text.getOrNull(i + 1) == '"' -> { cell.append('"'); i++ }
                c == '"' -> inQuotes = false
                else -> cell.append(c)
            }
            c == '"' -> inQuotes = true
            c == sep -> endCell()
            c == '\r' -> {}
            c == '\n' -> endRow()
            else -> cell.append(c)
        }
        i++
    }
    if (cell.isNotEmpty() || row.isNotEmpty()) endRow()
    return rows.filter { it.any { s -> s.isNotBlank() } }
}

/** Парсит первый лист .xlsx в строки (через sharedStrings + sheet xml). */
internal fun parseXlsx(file: File): List<List<String>> {
    ZipFile(file).use { zip ->
        val shared = zip.getEntry("xl/sharedStrings.xml")?.let { e ->
            val xml = zip.getInputStream(e).bufferedReader(Charsets.UTF_8).use { it.readText() }
            Regex("<si>(.*?)</si>", RegexOption.DOT_MATCHES_ALL).findAll(xml).map { si ->
                Regex("<t[^>]*>(.*?)</t>", RegexOption.DOT_MATCHES_ALL).findAll(si.groupValues[1])
                    .joinToString("") { unescapeXml(it.groupValues[1]) }
            }.toList()
        } ?: emptyList()

        val sheetEntry = zip.entries().toList()
            .filter { it.name.matches(Regex("xl/worksheets/sheet\\d+\\.xml")) }
            .minByOrNull { it.name } ?: return emptyList()
        val xml = zip.getInputStream(sheetEntry).bufferedReader(Charsets.UTF_8).use { it.readText() }

        val rows = mutableListOf<List<String>>()
        for (rowM in Regex("<row[^>]*>(.*?)</row>", RegexOption.DOT_MATCHES_ALL).findAll(xml)) {
            val cells = sortedMapOf<Int, String>()
            for (cM in Regex("<c\\s+([^>]*?)(?:/>|>(.*?)</c>)", RegexOption.DOT_MATCHES_ALL).findAll(rowM.groupValues[1])) {
                val attrs = cM.groupValues[1]
                val body = cM.groupValues[2]
                val ref = Regex("r=\"([A-Z]+)\\d+\"").find(attrs)?.groupValues?.get(1) ?: continue
                val type = Regex("t=\"([^\"]+)\"").find(attrs)?.groupValues?.get(1)
                val vRaw = Regex("<v>(.*?)</v>", RegexOption.DOT_MATCHES_ALL).find(body)?.groupValues?.get(1)
                val inline = Regex("<t[^>]*>(.*?)</t>", RegexOption.DOT_MATCHES_ALL).find(body)?.groupValues?.get(1)
                val value = when (type) {
                    "s" -> shared.getOrNull(vRaw?.toIntOrNull() ?: -1) ?: ""
                    "inlineStr" -> unescapeXml(inline ?: "")
                    else -> vRaw ?: ""
                }
                cells[colIndex(ref)] = value
            }
            val maxCol = cells.keys.maxOrNull() ?: -1
            rows.add((0..maxCol).map { cells[it] ?: "" })
        }
        return rows
    }
}

private fun colIndex(letters: String): Int {
    var idx = 0
    for (c in letters) idx = idx * 26 + (c.uppercaseChar() - 'A' + 1)
    return idx - 1
}

// -------------------------------------------------------- инструменты

/**
 * Точные операции над таблицами csv/xlsx: заголовки, строки (с пагинацией),
 * агрегаты (sum/avg/min/max/count) — считает Kotlin, поэтому ответы ТОЧНЫЕ даже
 * на огромных таблицах, без ошибок модели в арифметике.
 */
class TableQueryTool : Tool {
    override val name = "read_table"
    override val description =
        "Точно работает с таблицами (.csv/.tsv/.xlsx) в рабочей папке — используй для " +
            "любых данных/подсчётов по таблицам. op: 'info' (размер+заголовки), 'head' " +
            "(первые строки, offset/limit), 'sum'|'avg'|'min'|'max'|'count' по столбцу " +
            "column (имя из заголовка или номер с 0), 'find' (строки, где column==value). " +
            "Агрегаты считаются точно — не считай их в уме."
    override val category = ToolCategory.READ

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("path") { put("type", "string"); put("description", "Путь к .csv/.xlsx в рабочей папке") }
            putJsonObject("op") { put("type", "string"); put("description", "info|head|sum|avg|min|max|count|find") }
            putJsonObject("column") { put("type", "string"); put("description", "Столбец: имя из заголовка или номер (с 0)") }
            putJsonObject("value") { put("type", "string"); put("description", "Значение для op=find") }
            putJsonObject("offset") { put("type", "integer"); put("description", "op=head: с какой строки (по умолчанию 0)") }
            putJsonObject("limit") { put("type", "integer"); put("description", "op=head: сколько строк (по умолчанию 20)") }
        }
        putJsonArray("required") { add("path"); add("op") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult = withContext(Dispatchers.IO) {
        val rel = args.strv("path")
        val file = runCatching { resolveInWorkspace(ctx, rel) }.getOrElse { return@withContext ToolResult.fail(it.message ?: "плохой путь") }
        if (!file.isFile) return@withContext ToolResult.fail("файл '$rel' не найден")
        val rows = runCatching { parseTable(file) }.getOrElse { return@withContext ToolResult.fail("не удалось разобрать таблицу: ${it.message}") }
        if (rows.isEmpty()) return@withContext ToolResult.fail("таблица пуста или формат не поддержан")

        val header = rows.first()
        val data = rows.drop(1)
        val op = args.strv("op").lowercase()

        fun colIdx(spec: String): Int? {
            spec.toIntOrNull()?.let { return it }
            val i = header.indexOfFirst { it.trim().equals(spec.trim(), ignoreCase = true) }
            return if (i >= 0) i else null
        }
        fun numbers(ci: Int): List<BigDecimal> = data.mapNotNull {
            it.getOrNull(ci)?.trim()?.replace(",", ".")?.toBigDecimalOrNull()
        }

        return@withContext when (op) {
            "info" -> ToolResult(
                "Таблица «${file.name}»: строк данных ${data.size}, столбцов ${header.size}.\n" +
                    "Заголовки: " + header.mapIndexed { i, h -> "[$i] $h" }.joinToString(", "),
            )
            "head" -> {
                val off = args.intv("offset") ?: 0
                val lim = (args.intv("limit") ?: 20).coerceIn(1, 200)
                val slice = data.drop(off).take(lim)
                val body = (listOf(header) + slice).joinToString("\n") { it.joinToString(" | ") }
                val tail = if (off + lim < data.size) "\n\n…[строки $off–${off + slice.size} из ${data.size}. Дальше: offset=${off + lim}]" else ""
                ToolResult(body + tail)
            }
            "sum", "avg", "min", "max" -> {
                val ci = colIdx(args.strv("column")) ?: return@withContext ToolResult.fail("нет столбца '${args.strv("column")}'")
                val nums = numbers(ci)
                if (nums.isEmpty()) return@withContext ToolResult.fail("в столбце нет чисел")
                val res = when (op) {
                    "sum" -> nums.reduce { a, b -> a.add(b) }
                    "avg" -> nums.reduce { a, b -> a.add(b) }.divide(BigDecimal(nums.size), MathContext(30))
                    "min" -> nums.min()
                    else -> nums.max()
                }
                ToolResult("$op(${header.getOrNull(ci) ?: ci}) = ${res.stripTrailingZeros().toPlainString()} (по ${nums.size} значениям)")
            }
            "count" -> {
                val spec = args.strv("column")
                if (spec.isBlank()) ToolResult("Строк данных: ${data.size}")
                else {
                    val ci = colIdx(spec) ?: return@withContext ToolResult.fail("нет столбца '$spec'")
                    val nonEmpty = data.count { it.getOrNull(ci)?.isNotBlank() == true }
                    ToolResult("Непустых в «${header.getOrNull(ci) ?: ci}»: $nonEmpty из ${data.size}")
                }
            }
            "find" -> {
                val ci = colIdx(args.strv("column")) ?: return@withContext ToolResult.fail("нет столбца '${args.strv("column")}'")
                val needle = args.strv("value").trim()
                val hits = data.filter { it.getOrNull(ci)?.trim().equals(needle, ignoreCase = true) }
                if (hits.isEmpty()) ToolResult("Совпадений «$needle» в «${header.getOrNull(ci)}» не найдено.")
                else ToolResult(
                    "Найдено ${hits.size}:\n" + (listOf(header) + hits.take(50)).joinToString("\n") { it.joinToString(" | ") },
                )
            }
            else -> ToolResult.fail("неизвестная операция '$op'")
        }
    }
}

/**
 * Точечный поиск по файлу (grep-like): находит строки с подстрокой и возвращает их
 * с номерами и контекстом. Для больших файлов — модель сначала ищет, потом читает
 * нужный участок (read_file offset) или правит (edit_file). Понимает и office-файлы.
 */
class SearchInFileTool : Tool {
    override val name = "search_in_file"
    override val description =
        "Ищет строки с подстрокой в файле рабочей папки (в т.ч. docx/xlsx/txt/код) и " +
            "возвращает их с номерами строк и контекстом — используй для больших файлов, " +
            "чтобы точечно НАЙТИ нужное место, а потом прочитать (read_file) или изменить " +
            "(edit_file). Параметры: query, ignore_case, context (строк вокруг), max_matches."
    override val category = ToolCategory.READ

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("path") { put("type", "string"); put("description", "Файл в рабочей папке") }
            putJsonObject("query") { put("type", "string"); put("description", "Что искать (подстрока)") }
            putJsonObject("ignore_case") { put("type", "boolean"); put("description", "Игнорировать регистр (по умолчанию true)") }
            putJsonObject("context") { put("type", "integer"); put("description", "Строк контекста вокруг (0–5, по умолчанию 0)") }
            putJsonObject("max_matches") { put("type", "integer"); put("description", "Максимум совпадений (по умолчанию 40)") }
        }
        putJsonArray("required") { add("path"); add("query") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult = withContext(Dispatchers.IO) {
        val file = runCatching { resolveInWorkspace(ctx, args.strv("path")) }
            .getOrElse { return@withContext ToolResult.fail(it.message ?: "плохой путь") }
        if (!file.isFile) return@withContext ToolResult.fail("файл не найден")
        val query = args.strv("query")
        if (query.isEmpty()) return@withContext ToolResult.fail("пустой запрос")
        val text = runCatching { extractText(file) }.getOrNull()
            ?: return@withContext ToolResult.fail("формат не читается текстом")
        val ic = args["ignore_case"]?.jsonPrimitive?.contentOrNull?.toBoolean() ?: true
        val ctxN = (args.intv("context") ?: 0).coerceIn(0, 5)
        val maxN = (args.intv("max_matches") ?: 40).coerceIn(1, 200)

        val lines = text.split("\n")
        val hitIdx = lines.indices.filter { lines[it].contains(query, ignoreCase = ic) }
        if (hitIdx.isEmpty()) return@withContext ToolResult("«$query» не найдено в ${file.name}.")

        val sb = StringBuilder("Найдено ${hitIdx.size} совпадений в ${file.name} (показаны первые ${minOf(hitIdx.size, maxN)}):\n")
        for (i in hitIdx.take(maxN)) {
            val from = (i - ctxN).coerceAtLeast(0)
            val to = (i + ctxN).coerceAtMost(lines.lastIndex)
            for (j in from..to) {
                val marker = if (j == i) ">" else " "
                sb.append("$marker ${j + 1}: ${lines[j].take(200)}\n")
            }
            if (ctxN > 0) sb.append("---\n")
        }
        ToolResult(sb.toString().trimEnd())
    }
}

/**
 * Редактирование файла на телефоне: замена текста (find→replace) или добавление
 * в конец. Песочница — рабочая папка чата.
 */
class EditFileTool : Tool {
    override val name = "edit_file"
    override val description =
        "Редактирует текстовый файл в рабочей папке: mode='replace' меняет find→replace " +
            "(all=true — все вхождения), mode='append' дописывает текст в конец. Для полной " +
            "перезаписи используй write_file."
    override val category = ToolCategory.EDIT

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("path") { put("type", "string"); put("description", "Путь к файлу в рабочей папке") }
            putJsonObject("mode") { put("type", "string"); put("description", "replace | append") }
            putJsonObject("find") { put("type", "string"); put("description", "Что заменить (для replace)") }
            putJsonObject("replace") { put("type", "string"); put("description", "На что / что дописать") }
            putJsonObject("all") { put("type", "boolean"); put("description", "Заменить все вхождения (replace)") }
        }
        putJsonArray("required") { add("path"); add("mode") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult = withContext(Dispatchers.IO) {
        val file = runCatching { resolveInWorkspace(ctx, args.strv("path")) }
            .getOrElse { return@withContext ToolResult.fail(it.message ?: "плохой путь") }
        val mode = args.strv("mode").lowercase()
        return@withContext runCatching {
            when (mode) {
                "append" -> {
                    file.parentFile?.mkdirs()
                    file.appendText(args.strv("replace"))
                    ToolResult("Дописано в ${file.name} (${file.length()} байт).")
                }
                "replace" -> {
                    if (!file.isFile) return@runCatching ToolResult.fail("файл не найден")
                    val find = args.strv("find")
                    if (find.isEmpty()) return@runCatching ToolResult.fail("нужен find")
                    val text = file.readText()
                    if (!text.contains(find)) return@runCatching ToolResult.fail("текст «${find.take(40)}» не найден")
                    val all = args["all"]?.jsonPrimitive?.contentOrNull?.toBoolean() ?: false
                    val out = if (all) text.replace(find, args.strv("replace"))
                    else text.replaceFirst(find, args.strv("replace"))
                    file.writeText(out)
                    ToolResult("Заменено в ${file.name}.")
                }
                else -> ToolResult.fail("mode должен быть replace или append")
            }
        }.getOrElse { ToolResult.fail("не удалось отредактировать: ${it.message}") }
    }
}

private fun JsonObject.strv(key: String): String = this[key]?.jsonPrimitive?.contentOrNull.orEmpty()
private fun JsonObject.intv(key: String): Int? = this[key]?.jsonPrimitive?.contentOrNull?.toIntOrNull()
