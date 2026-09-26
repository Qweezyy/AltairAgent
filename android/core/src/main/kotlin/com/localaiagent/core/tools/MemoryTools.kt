package com.localaiagent.core.tools

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.File
import java.time.LocalDate

/**
 * Полноценная работа с памятью (сверх remember/remember_global): просмотр по
 * номерам, удаление, замена пункта. scope = "chat" (память этого чата) | "global".
 */

private fun mstr(desc: String): JsonObject = buildJsonObject { put("type", "string"); put("description", desc) }

private fun mObjSchema(props: Map<String, JsonObject>, required: List<String>): JsonObject = buildJsonObject {
    put("type", "object")
    putJsonObject("properties") { props.forEach { (k, v) -> put(k, v) } }
    putJsonArray("required") { required.forEach { add(it) } }
}

private fun JsonObject.g(key: String): String = this[key]?.jsonPrimitive?.contentOrNull.orEmpty()

private fun memoryFile(ctx: ToolContext, scope: String): File =
    if (scope.trim().lowercase() == "global") File(File(ctx.globalMemoryDir), "global.md")
    else File(File(ctx.workspaceDir), ".agent/memory.md")

private fun header(scope: String): String =
    if (scope.trim().lowercase() == "global") "# Общая память" else "# Память чата"

/** Пункты (bullet-строки) файла памяти — по порядку. */
private fun bullets(f: File): List<String> =
    if (!f.isFile) emptyList()
    else f.readText().lines().filter { it.trimStart().startsWith("- ") }

private fun writeBullets(f: File, scope: String, items: List<String>) {
    f.parentFile?.mkdirs()
    if (items.isEmpty()) { f.writeText("${header(scope)}\n\n"); return }
    f.writeText("${header(scope)}\n\n" + items.joinToString("\n") { it.trimEnd() } + "\n")
}

/** Просмотр памяти с номерами (для последующего удаления/замены). */
class MemoryViewTool : Tool {
    override val name = "memory_view"
    override val description =
        "Показывает пункты памяти с номерами. scope: chat (этот чат) | global (общая). " +
            "Используй перед memory_remove/memory_replace, чтобы узнать номера."
    override val category = ToolCategory.READ
    override fun schema() = mObjSchema(mapOf("scope" to mstr("chat | global")), listOf("scope"))

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val scope = args.g("scope")
        val items = bullets(memoryFile(ctx, scope))
        if (items.isEmpty()) return ToolResult("Память ($scope) пуста.")
        return ToolResult(items.mapIndexed { i, s -> "${i + 1}. ${s.trimStart().removePrefix("- ")}" }.joinToString("\n"))
    }
}

/** Удаление пункта памяти по номеру или подстроке. */
class MemoryRemoveTool : Tool {
    override val name = "memory_remove"
    override val description =
        "Удаляет пункт памяти. Укажи index (номер из memory_view) ИЛИ contains (подстрока). " +
            "scope: chat | global."
    override val category = ToolCategory.EDIT
    override fun schema() = mObjSchema(
        mapOf(
            "scope" to mstr("chat | global"),
            "index" to mstr("Номер пункта (из memory_view)"),
            "contains" to mstr("Подстрока для поиска пункта (альтернатива index)"),
        ),
        listOf("scope"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val scope = args.g("scope")
        val f = memoryFile(ctx, scope)
        val items = bullets(f).toMutableList()
        if (items.isEmpty()) return ToolResult.fail("память ($scope) пуста")
        val idx = args.g("index").toIntOrNull()?.let { it - 1 }
        val target = when {
            idx != null && idx in items.indices -> idx
            args.g("contains").isNotBlank() -> items.indexOfFirst { it.contains(args.g("contains"), ignoreCase = true) }
            else -> -1
        }
        if (target < 0) return ToolResult.fail("пункт не найден (проверь index/contains через memory_view)")
        val removed = items.removeAt(target)
        writeBullets(f, scope, items)
        return ToolResult("Удалено: ${removed.trimStart().removePrefix("- ")}")
    }
}

/** Предлагает пользователю сохранить факт в память (он подтверждает одним тапом). */
class SuggestMemoryTool : Tool {
    override val name = "suggest_memory"
    override val description =
        "Предлагает пользователю сохранить факт в память — он подтвердит/отклонит одним тапом " +
            "(показывается preview). Используй для фактов О ПОЛЬЗОВАТЕЛЕ и договорённостей, чтобы " +
            "он видел и контролировал, что запоминается. scope: chat (этот чат) | global (везде). " +
            "Для внутренних заметок без подтверждения — remember/remember_global."
    override val category = ToolCategory.READ
    override fun schema() = mObjSchema(
        mapOf(
            "text" to mstr("Короткий факт для сохранения (одно утверждение)"),
            "scope" to mstr("chat | global (по умолчанию global)"),
        ),
        listOf("text"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val text = args.g("text").split(Regex("\\s+")).joinToString(" ").trim()
        if (text.isEmpty()) return ToolResult.fail("нечего сохранять")
        val scope = args.g("scope").trim().lowercase().ifBlank { "global" }
        val answer = ctx.requestUi("memory", buildJsonObject { put("text", text); put("scope", scope) })
        val save = answer["save"]?.jsonPrimitive?.booleanOrNull == true
        if (!save) return ToolResult("Пользователь решил не сохранять этот факт.")
        val f = memoryFile(ctx, scope)
        return runCatching {
            val existing = if (f.isFile) f.readText() else ""
            if (existing.contains(text)) return ToolResult("Уже было в памяти.")
            f.parentFile?.mkdirs()
            val head = if (existing.isBlank()) "${header(scope)}\n\n" else existing.trimEnd() + "\n"
            f.writeText(head + "- ${LocalDate.now()}: $text\n")
            ToolResult("Пользователь подтвердил — сохранено в память ($scope): $text")
        }.getOrElse { ToolResult.fail("не удалось сохранить: ${it.message}") }
    }
}

/** Замена текста пункта памяти (по номеру или find→replace). */
class MemoryReplaceTool : Tool {
    override val name = "memory_replace"
    override val description =
        "Меняет пункт памяти. Вариант A: index + new_text (заменить весь пункт). " +
            "Вариант B: find + replace (замена подстроки во всей памяти). scope: chat | global."
    override val category = ToolCategory.EDIT
    override fun schema() = mObjSchema(
        mapOf(
            "scope" to mstr("chat | global"),
            "index" to mstr("Номер пункта (для варианта A)"),
            "new_text" to mstr("Новый текст пункта (для варианта A)"),
            "find" to mstr("Что искать (для варианта B)"),
            "replace" to mstr("На что заменить (для варианта B)"),
        ),
        listOf("scope"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val scope = args.g("scope")
        val f = memoryFile(ctx, scope)
        val items = bullets(f).toMutableList()
        if (items.isEmpty()) return ToolResult.fail("память ($scope) пуста")
        val idx = args.g("index").toIntOrNull()?.let { it - 1 }
        // Вариант A: замена пункта по номеру.
        if (idx != null && args.g("new_text").isNotBlank()) {
            if (idx !in items.indices) return ToolResult.fail("нет пункта №${idx + 1}")
            items[idx] = "- ${args.g("new_text").trim()}"
            writeBullets(f, scope, items)
            return ToolResult("Пункт №${idx + 1} обновлён.")
        }
        // Вариант B: find→replace по всем пунктам.
        val find = args.g("find")
        if (find.isNotBlank()) {
            var n = 0
            val updated = items.map { if (it.contains(find)) { n++; it.replace(find, args.g("replace")) } else it }
            if (n == 0) return ToolResult.fail("подстрока '$find' не найдена")
            writeBullets(f, scope, updated)
            return ToolResult("Заменено вхождений: $n.")
        }
        return ToolResult.fail("укажи index+new_text или find+replace")
    }
}
