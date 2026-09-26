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
import java.io.File
import java.time.LocalDate

/**
 * Первые встроенные инструменты Android-агента (Фаза 1): файлы и память. Без внешних
 * зависимостей. Добавить инструмент = новый класс + строка в [builtinTools].
 */
fun builtinTools(): List<Tool> = listOf(
    ToolSearchTool(),
    CalcTool(),
    WebSearchTool(),
    FetchUrlTool(),
    DeepResearchTool(),
    ShowImageTool(),
    ShowGraphicTool(),
    ShowInteractiveTool(),
    AttachFileTool(),
    ListDirectoryTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    SearchInFileTool(),
    TableQueryTool(),
    RunShellTool(),
    RememberTool(),
    RememberGlobalTool(),
    MemoryViewTool(),
    MemoryRemoveTool(),
    MemoryReplaceTool(),
    SuggestMemoryTool(),
    ContextInfoTool(),
    ContextCompressTool(),
    ContextDropTool(),
    AskTool(),
    RequestFileTool(),
    RequestSecretTool(),
    ListSecretsTool(),
    SetReminderTool(),
    WatchConditionTool(),
    ListRemindersTool(),
    CancelReminderTool(),
    FindSkillsTool(),
    UseSkillTool(),
    CreateSkillTool(),
)

// -------------------------------------------------------------- вспомогательное

private fun strProp(desc: String): JsonObject = buildJsonObject {
    put("type", "string"); put("description", desc)
}

private fun objectSchema(props: Map<String, JsonObject>, required: List<String>): JsonObject =
    buildJsonObject {
        put("type", "object")
        putJsonObject("properties") { props.forEach { (k, v) -> put(k, v) } }
        putJsonArray("required") { required.forEach { add(it) } }
    }

private fun JsonObject.str(key: String): String =
    this[key]?.jsonPrimitive?.contentOrNull.orEmpty()

/** Резолвит путь внутри рабочей папки; запрещает выход за её пределы (песочница). */
internal fun resolveInWorkspace(ctx: ToolContext, rel: String): File {
    val base = File(ctx.workspaceDir).canonicalFile
    val target = File(base, rel.ifBlank { "." }).canonicalFile
    require(target.path == base.path || target.path.startsWith(base.path + File.separator)) {
        "Путь '$rel' выходит за пределы рабочей папки"
    }
    return target
}

// --------------------------------------------------------------------- файлы

class ListDirectoryTool : Tool {
    override val name = "list_directory"
    override val description = "Показывает содержимое папки в рабочей директории."
    override val category = ToolCategory.READ
    override fun schema() = objectSchema(
        mapOf("path" to strProp("Папка относительно рабочей (по умолчанию '.')")),
        emptyList(),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val dir = runCatching { resolveInWorkspace(ctx, args.str("path").ifBlank { "." }) }
            .getOrElse { return ToolResult.fail(it.message ?: "плохой путь") }
        if (!dir.isDirectory) return ToolResult.fail("'${args.str("path")}' — не папка")
        val items = dir.listFiles()?.sortedBy { it.name } ?: emptyList()
        if (items.isEmpty()) return ToolResult("Папка пуста.")
        val body = items.joinToString("\n") { (if (it.isDirectory) "📁 " else "📄 ") + it.name }
        return ToolResult("Содержимое:\n$body")
    }
}

class ReadFileTool : Tool {
    override val name = "read_file"
    override val description =
        "Читает файл из рабочей папки и возвращает его ТЕКСТ. Понимает txt/md/csv/json/код " +
            "и офисные docx/pptx/xlsx (извлекает текст). Большой файл отдаёт кусками: если " +
            "текст обрезан, в конце будет offset для следующего вызова. Для точных подсчётов " +
            "по таблицам используй read_table."
    override val category = ToolCategory.READ
    override fun schema() = objectSchema(
        mapOf(
            "path" to strProp("Путь к файлу относительно рабочей папки"),
            "offset" to strProp("С какого символа читать (для больших файлов; по умолчанию 0)"),
            "max_chars" to strProp("Размер куска (по умолчанию 6000, до 40000)"),
        ),
        listOf("path"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val f = runCatching { resolveInWorkspace(ctx, args.str("path")) }
            .getOrElse { return ToolResult.fail(it.message ?: "плохой путь") }
        if (!f.isFile) return ToolResult.fail("Файл '${args.str("path")}' не найден")
        val text = runCatching { extractText(f) }.getOrNull()
            ?: return ToolResult.fail("Формат '${f.extension}' не поддержан для чтения текстом.")
        val maxChars = args.str("max_chars").toIntOrNull()?.coerceIn(500, 40_000) ?: 6_000
        val offset = args.str("offset").toIntOrNull()?.coerceAtLeast(0) ?: 0
        val total = text.length
        val start = offset.coerceIn(0, total)
        val end = (start + maxChars).coerceAtMost(total)
        val head = if (start > 0) "…[продолжение с символа $start из $total]\n\n" else ""
        val tail = if (end < total) "\n\n…[показаны символы $start–$end из $total. Дальше: offset=$end]" else ""
        return ToolResult(head + text.substring(start, end) + tail)
    }
}

class WriteFileTool : Tool {
    override val name = "write_file"
    override val description = "Создаёт или перезаписывает файл в рабочей папке."
    override val category = ToolCategory.EDIT
    override fun schema() = objectSchema(
        mapOf(
            "path" to strProp("Путь к файлу относительно рабочей папки"),
            "content" to strProp("Полное содержимое файла"),
        ),
        listOf("path", "content"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val f = runCatching { resolveInWorkspace(ctx, args.str("path")) }
            .getOrElse { return ToolResult.fail(it.message ?: "плохой путь") }
        return runCatching {
            f.parentFile?.mkdirs()
            f.writeText(args.str("content"))
            ToolResult("Записано: ${args.str("path")} (${f.length()} байт)")
        }.getOrElse { ToolResult.fail("не удалось записать: ${it.message}") }
    }
}

// --------------------------------------------------------------------- память

class RememberTool : Tool {
    override val name = "remember"
    override val description =
        "Сохраняет короткий устойчивый факт/урок в память ЭТОГО чата (.agent/memory.md). " +
            "Не скупись на важное, но не строчи по любому поводу; не сохраняй секреты."
    override val category = ToolCategory.READ
    override fun schema() = objectSchema(
        mapOf("text" to strProp("Короткий факт или урок (одно утверждение)")),
        listOf("text"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val note = args.str("text").split(Regex("\\s+")).joinToString(" ").trim()
        if (note.isEmpty()) return ToolResult.fail("нечего запоминать")
        val f = File(File(ctx.workspaceDir), ".agent/memory.md")
        return appendMemory(f, "# Память чата", note)
    }
}

/** Общая память — одна на все чаты (как MemoryStore на ПК). Для устойчивых фактов. */
class RememberGlobalTool : Tool {
    override val name = "remember_global"
    override val description =
        "Сохраняет факт в ОБЩУЮ память (видна во всех чатах): предпочтения пользователя, " +
            "устойчивые факты о нём, важные договорённости. Для локального к чату — remember."
    override val category = ToolCategory.READ
    override fun schema() = objectSchema(
        mapOf("text" to strProp("Короткий устойчивый факт (одно утверждение)")),
        listOf("text"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val note = args.str("text").split(Regex("\\s+")).joinToString(" ").trim()
        if (note.isEmpty()) return ToolResult.fail("нечего запоминать")
        val f = File(File(ctx.globalMemoryDir), "global.md")
        return appendMemory(f, "# Общая память", note)
    }
}

private fun appendMemory(f: File, header: String, note: String): ToolResult = runCatching {
    val existing = if (f.isFile) f.readText() else ""
    if (existing.contains(note)) return ToolResult("Уже записано.")
    f.parentFile?.mkdirs()
    val head = if (existing.isBlank()) "$header\n\n" else existing.trimEnd() + "\n"
    f.writeText(head + "- ${LocalDate.now()}: $note\n")
    ToolResult("Запомнил: $note")
}.getOrElse { ToolResult.fail("не удалось сохранить: ${it.message}") }
