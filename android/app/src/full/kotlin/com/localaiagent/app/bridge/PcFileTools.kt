package com.localaiagent.app.bridge

import android.util.Base64
import com.localaiagent.core.AgentEvent
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import kotlinx.serialization.json.add
import java.io.File

/** Таймаут файловой операции по мосту — если ПК не реализовал хендлер, не висим вечно. */
private const val FILE_OP_TIMEOUT_MS = 25_000L

private fun b64Decode(s: String): ByteArray = Base64.decode(s, Base64.DEFAULT)
private fun b64Encode(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)

/** Тип файла по расширению — для «посмотреть до того как забрать». */
private fun kindOf(name: String): String {
    val ext = name.substringAfterLast('.', "").lowercase()
    return when (ext) {
        "png", "jpg", "jpeg", "gif", "webp", "bmp", "heic" -> "image"
        "txt", "md", "json", "csv", "xml", "yaml", "yml", "kt", "java", "py",
        "js", "ts", "html", "css", "log", "sh", "toml", "ini", "gradle" -> "text"
        else -> "file"
    }
}

private fun JsonObject.str(key: String): String? = this[key]?.jsonPrimitive?.contentOrNull

/** Папка вложений текущего чата (песочница файлового обмена на телефоне). */
private fun ToolContext.attachmentsDir(): File =
    File(workspaceDir, "attachments").apply { mkdirs() }

private fun notConfigured() = ToolResult.fail("Мост к ПК не настроен (укажи адрес ПК в настройках).")
private fun noAnswer(what: String) = ToolResult.fail(
    "ПК не ответил на «$what». Проверь связь и что на ПК включены файловые хендлеры моста.",
)

// --------------------------------------------------------------------------- обнаружение

/** Что умеет ПК и какая у него рабочая папка (из `ready`/`hello`). Работает без правок ПК. */
class PcCapabilitiesTool(private val config: PcBridgeConfig) : Tool {
    override val name = "pc_capabilities"
    override val description =
        "Узнать, что умеет ПК-агент (список его инструментов) и какая у него рабочая папка. " +
            "Вызывай перед обменом файлами/делегированием, чтобы понять возможности ПК."
    override val category = ToolCategory.READ
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object"); putJsonObject("properties") {}
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        if (!config.enabled) return notConfigured()
        val reply = withTimeoutOrNull(FILE_OP_TIMEOUT_MS) { PcBridgeClient(config).capabilities() }
            ?: return noAnswer("обнаружение ПК")
        // Рабочая папка обмена — это заданная в настройках моста (config.workspace).
        // В `ready` приходит эфемерная авто-папка того сокета, а не папка обмена, —
        // её показывать пользователю нельзя, иначе путь вводит в заблуждение.
        val ws = config.workspace.ifBlank { reply.str("workspace").orEmpty() }
        val caps = reply["capabilities"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull }
        val tools = reply["tools"]?.jsonArray?.mapNotNull { it.jsonObject.str("name") }
        val lines = buildString {
            append("ПК-агент на связи.")
            if (ws.isNotBlank()) append("\nРабочая папка ПК (обмен): $ws")
            if (!caps.isNullOrEmpty()) append("\nВозможности: ${caps.joinToString(", ")}")
            if (!tools.isNullOrEmpty()) append("\nИнструменты ПК (${tools.size}): ${tools.joinToString(", ")}")
        }
        return ToolResult(lines)
    }
}

// --------------------------------------------------------------------------- список/метаданные

/** Список файлов в рабочей папке ПК (можно glob). «Что вообще там лежит». */
class PcListFilesTool(private val config: PcBridgeConfig) : Tool {
    override val name = "pc_list_files"
    override val description =
        "Список файлов в рабочей папке ПК (опционально по glob, напр. \"out/**\" или \"*.pdf\"). " +
            "Используй, чтобы узнать, что лежит на ПК, ПЕРЕД тем как что-то забирать."
    override val category = ToolCategory.READ
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("glob") { put("type", "string"); put("description", "glob-маска (необязательно)") }
        }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        if (!config.enabled) return notConfigured()
        val glob = args.str("glob")
        val cmd = buildJsonObject {
            put("type", BridgeProtocol.LIST_FILES)
            if (!glob.isNullOrBlank()) put("glob", glob)
        }
        val reply = withTimeoutOrNull(FILE_OP_TIMEOUT_MS) {
            PcBridgeClient(config).oneShot(cmd, setOf(BridgeProtocol.R_FILES))
        } ?: return noAnswer("список файлов ПК")
        val items = reply["items"]?.jsonArray ?: return ToolResult("На ПК нет файлов по этому запросу.")
        if (items.isEmpty()) return ToolResult("На ПК нет файлов по этому запросу.")
        val rows = items.take(200).joinToString("\n") { el ->
            val o = el.jsonObject
            val p = o.str("path").orEmpty()
            val bytes = o["bytes"]?.jsonPrimitive?.long ?: 0
            val kind = o.str("kind") ?: kindOf(p)
            "• $p (${humanBytes(bytes)}, $kind)"
        }
        return ToolResult("Файлы на ПК:\n$rows")
    }
}

/** Метаданные одного файла ПК (размер/дата/тип) — «посмотреть до того как забрать». */
class PcStatFileTool(private val config: PcBridgeConfig) : Tool {
    override val name = "pc_stat_file"
    override val description =
        "Метаданные файла на ПК (существует ли, размер, дата, тип) по относительному пути. " +
            "Проверяй перед pc_read_file/pc_fetch_file, особенно размер (лимит обмена 25 МБ)."
    override val category = ToolCategory.READ
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("path") { put("type", "string"); put("description", "путь относительно папки ПК") }
        }
        putJsonArray("required") { add("path") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        if (!config.enabled) return notConfigured()
        val path = args.str("path")?.trim().orEmpty()
        if (path.isEmpty()) return ToolResult.fail("не указан path")
        val cmd = buildJsonObject { put("type", BridgeProtocol.STAT_FILE); put("path", path) }
        val reply = withTimeoutOrNull(FILE_OP_TIMEOUT_MS) {
            PcBridgeClient(config).oneShot(cmd, setOf(BridgeProtocol.R_FILE_STAT))
        } ?: return noAnswer("метаданные файла")
        val exists = reply["exists"]?.jsonPrimitive?.contentOrNull?.toBoolean() ?: false
        if (!exists) return ToolResult("Файла «$path» на ПК нет.")
        val bytes = reply["bytes"]?.jsonPrimitive?.long ?: 0
        val kind = reply.str("kind") ?: kindOf(path)
        val mtime = reply.str("mtime").orEmpty()
        return ToolResult("«$path»: ${humanBytes(bytes)}, тип $kind${if (mtime.isNotBlank()) ", изменён $mtime" else ""}.")
    }
}

// --------------------------------------------------------------------------- чтение/скачивание

/** Прочитать ТЕКСТОВЫЙ файл ПК прямо в ответ модели (короткий). */
class PcReadFileTool(private val config: PcBridgeConfig) : Tool {
    override val name = "pc_read_file"
    override val description =
        "Прочитать текстовый файл с ПК и вернуть его содержимое (для анализа). Для картинок/" +
            "бинарных файлов используй pc_fetch_file. Большие файлы читай после pc_stat_file."
    override val category = ToolCategory.READ
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("path") { put("type", "string"); put("description", "путь относительно папки ПК") }
        }
        putJsonArray("required") { add("path") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        if (!config.enabled) return notConfigured()
        val path = args.str("path")?.trim().orEmpty()
        if (path.isEmpty()) return ToolResult.fail("не указан path")
        val reply = fetchFile(config, path) ?: return noAnswer("чтение файла ПК")
        if (reply.str("type") == BridgeProtocol.R_FILE_MISSING) return ToolResult("Файла «$path» на ПК нет.")
        val b64 = reply.str("b64") ?: return ToolResult.fail("ПК не прислал содержимое")
        val text = runCatching { String(b64Decode(b64), Charsets.UTF_8) }.getOrNull()
            ?: return ToolResult.fail("файл не текстовый — используй pc_fetch_file")
        val capped = if (text.length > 20_000) text.take(20_000) + "\n… (обрезано)" else text
        return ToolResult("Содержимое «$path» с ПК:\n$capped")
    }
}

/** Скачать файл ПК в папку вложений телефона (картинки/бинарь); показать, если картинка. */
class PcFetchFileTool(private val config: PcBridgeConfig) : Tool {
    override val name = "pc_fetch_file"
    override val description =
        "Скачать файл с ПК в этот чат (в папку вложений телефона). Годится для картинок и любых " +
            "файлов. Картинка сразу покажется в чате. Вернёт локальный путь."
    override val category = ToolCategory.NETWORK
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("path") { put("type", "string"); put("description", "путь относительно папки ПК") }
        }
        putJsonArray("required") { add("path") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        if (!config.enabled) return notConfigured()
        val path = args.str("path")?.trim().orEmpty()
        if (path.isEmpty()) return ToolResult.fail("не указан path")
        val reply = fetchFile(config, path) ?: return noAnswer("скачивание файла ПК")
        if (reply.str("type") == BridgeProtocol.R_FILE_MISSING) return ToolResult("Файла «$path» на ПК нет.")
        val b64 = reply.str("b64") ?: return ToolResult.fail("ПК не прислал содержимое")
        val bytes = runCatching { b64Decode(b64) }.getOrNull() ?: return ToolResult.fail("не удалось раскодировать файл")
        val name = path.substringAfterLast('/')
        val dest = File(ctx.attachmentsDir(), name)
        runCatching { dest.writeBytes(bytes) }.getOrElse { return ToolResult.fail("не удалось сохранить: ${it.message}") }
        if (kindOf(name) == "image") ctx.emit(AgentEvent.ShowImage(dest.absolutePath, "С ПК: $name"))
        return ToolResult("Файл «$path» скачан с ПК в этот чат: ${dest.absolutePath} (${humanBytes(bytes.size.toLong())}).")
    }
}

// --------------------------------------------------------------------------- отправка на ПК

/** Отправить локальный файл чата на ПК (в inbox/). */
class PcSendFileTool(private val config: PcBridgeConfig) : Tool {
    override val name = "pc_send_file"
    override val description =
        "Отправить файл из этого чата на ПК (в папку inbox/ рабочей папки ПК). Укажи имя файла " +
            "из вложений чата. Используй, чтобы дать ПК-агенту файл для обработки."
    override val category = ToolCategory.NETWORK
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("name") { put("type", "string"); put("description", "имя файла из вложений чата") }
        }
        putJsonArray("required") { add("name") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        if (!config.enabled) return notConfigured()
        val name = args.str("name")?.trim()?.substringAfterLast('/').orEmpty()
        if (name.isEmpty()) return ToolResult.fail("не указано имя файла")
        val src = File(ctx.attachmentsDir(), name)
        if (!src.isFile) return ToolResult.fail("файла «$name» нет во вложениях этого чата")
        if (src.length() > BridgeProtocol.MAX_FILE_BYTES) {
            return ToolResult.fail("файл больше 25 МБ — по мосту не отправить")
        }
        val bytes = runCatching { src.readBytes() }.getOrElse { return ToolResult.fail("не удалось прочитать файл") }
        val cmd = buildJsonObject {
            put("type", BridgeProtocol.PUT_FILE)
            put("path", "inbox/$name")
            put("b64", b64Encode(bytes))
        }
        val reply = withTimeoutOrNull(FILE_OP_TIMEOUT_MS) {
            PcBridgeClient(config).oneShot(cmd, setOf(BridgeProtocol.R_PUT_OK, BridgeProtocol.R_PUT_ERROR))
        } ?: return noAnswer("отправку файла на ПК")
        if (reply.str("type") == BridgeProtocol.R_PUT_ERROR) {
            return ToolResult.fail("ПК отклонил файл: ${reply.str("message").orEmpty()}")
        }
        return ToolResult("Файл «$name» отправлен на ПК в inbox/ (${humanBytes(bytes.size.toLong())}).")
    }
}

// --------------------------------------------------------------------------- общее

/** get_file → ответ (`file` или `file.missing`). Общая часть чтения/скачивания. */
private suspend fun fetchFile(config: PcBridgeConfig, path: String): JsonObject? =
    withTimeoutOrNull(FILE_OP_TIMEOUT_MS) {
        val cmd = buildJsonObject { put("type", BridgeProtocol.GET_FILE); put("path", path) }
        PcBridgeClient(config).oneShot(cmd, setOf(BridgeProtocol.R_FILE, BridgeProtocol.R_FILE_MISSING))
    }

// --------------------------------------------------------------------------- память

/**
 * Синхронизация ОБЩЕЙ памяти телефон ↔ ПК. Форматы хранения разные (телефон — .md
 * с буллетами, ПК — memory.json), поэтому обмениваемся ТЕКСТАМИ фактов, а не файлом.
 * Телефон шлёт свои факты, ПК доливает недостающие к себе и возвращает объединённый
 * список; телефон дописывает то, чего нет у него. На выходе — общий набор с обеих сторон.
 */
class PcSyncMemoryTool(private val config: PcBridgeConfig) : Tool {
    override val name = "pc_sync_memory"
    override val description =
        "Синхронизировать общую память (факты о пользователе/предпочтения/цели) с ПК: " +
            "телефон и ПК обмениваются фактами и после синхронизации знают одно и то же. " +
            "Вызывай, когда пользователь просит «синхронизируй память» или чтобы ПК знал то же, что телефон."
    override val category = ToolCategory.NETWORK
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object"); putJsonObject("properties") {}
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        if (!config.enabled) return notConfigured()
        val file = File(ctx.globalMemoryDir, "global.md")
        val phoneFacts = extractFacts(if (file.isFile) file.readText() else "")
        val cmd = buildJsonObject {
            put("type", BridgeProtocol.SYNC_MEMORY)
            put("scope", "global")
            putJsonArray("facts") { phoneFacts.forEach { add(it) } }
        }
        val reply = withTimeoutOrNull(FILE_OP_TIMEOUT_MS) {
            PcBridgeClient(config).oneShot(cmd, setOf(BridgeProtocol.R_MEMORY_SYNC))
        } ?: return noAnswer("синхронизацию памяти")
        val pcFacts = reply["facts"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull?.trim() }?.filter { it.isNotEmpty() }
            ?: emptyList()
        // Что пришло с ПК и чего у телефона ещё нет (по нормализованному тексту).
        val have = phoneFacts.map { norm(it) }.toHashSet()
        val incoming = pcFacts.filter { norm(it) !in have }
        if (incoming.isNotEmpty()) {
            val existing = if (file.isFile) file.readText() else ""
            val head = if (existing.isBlank()) "# Общая память\n\n" else existing.trimEnd() + "\n"
            val today = java.time.LocalDate.now()
            file.parentFile?.mkdirs()
            file.writeText(head + incoming.joinToString("\n") { "- $today: $it" } + "\n")
        }
        return ToolResult(
            "Память синхронизирована: отправлено на ПК ${phoneFacts.size}, получено с ПК новых ${incoming.size}. " +
                "Всего фактов в общей памяти: ${have.size + incoming.size}.",
        )
    }

    /** Тексты фактов из global.md: строки-буллеты без префикса «- » и ведущей даты. */
    private fun extractFacts(md: String): List<String> =
        md.lineSequence()
            .map { it.trim() }
            .filter { it.startsWith("- ") }
            .map { it.removePrefix("- ").trim() }
            .map { it.replaceFirst(Regex("^\\d{4}-\\d{2}-\\d{2}:\\s*"), "") } // убрать «2026-08-31: »
            .filter { it.isNotEmpty() }
            .toList()

    private fun norm(s: String): String = s.lowercase().replace(Regex("\\s+"), " ").trim()
}

/** Набор файловых инструментов моста — регистрируется, когда ПК настроен. */
fun pcFileTools(config: PcBridgeConfig): List<Tool> = listOf(
    PcCapabilitiesTool(config),
    PcListFilesTool(config),
    PcStatFileTool(config),
    PcReadFileTool(config),
    PcFetchFileTool(config),
    PcSendFileTool(config),
    PcSyncMemoryTool(config),
)

private fun humanBytes(n: Long): String = when {
    n >= 1_048_576 -> "%.1f МБ".format(n / 1_048_576.0)
    n >= 1024 -> "%.0f КБ".format(n / 1024.0)
    else -> "$n Б"
}
