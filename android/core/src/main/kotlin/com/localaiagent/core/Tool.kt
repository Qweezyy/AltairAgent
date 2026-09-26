package com.localaiagent.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Точка расширения №1: инструмент. Добавить инструмент = реализовать этот интерфейс
 * и добавить его в список builtinTools() (как «класс + регистрация» в Python-агенте).
 */

enum class ToolCategory { READ, EDIT, EXECUTE, NETWORK }

/**
 * Если инструмент положит текст в `ctx.scratch[TERMINAL_ANSWER_KEY]`, цикл агента
 * завершит прогон этим текстом, не переспрашивая модель (инструмент сам стримит
 * финальный ответ — например, pc_agent проксирует ответ ПК-агента).
 */
const val TERMINAL_ANSWER_KEY = "__terminal_answer__"

enum class Verdict { ALLOW, ASK, DENY }

/** Всё, что инструмент получает от окружения. Расширяется по мере надобности. */
interface ToolContext {
    /** Рабочая папка чата (песочница файловых инструментов). */
    val workspaceDir: String
    /** Папка общей (глобальной) памяти — одна на все чаты. По умолчанию = рабочая. */
    val globalMemoryDir: String get() = workspaceDir
    /** Сессия диалога — для инструментов контекста (context_info/compress/drop). */
    val session: Session? get() = null
    /** Размер контекстного окна активной модели (токенов). */
    val contextWindow: Int get() = 128_000
    /** The run's tool registry (tool_search needs it to load deferred tools). */
    val registry: ToolRegistry? get() = null
    /** Общая память инструментов в пределах одного прогона. */
    val scratch: MutableMap<String, Any?>
    /** Спросить подтверждение у пользователя (для опасных действий). */
    suspend fun approve(name: String, reason: String, args: JsonObject): Boolean
    /**
     * Запросить у пользователя ввод через UI-форму (kind: "ask" | "file" | "secret").
     * Блокирует до ответа пользователя; возвращает ответ как JSON. Если UI недоступен —
     * пустой объект.
     */
    suspend fun requestUi(kind: String, payload: JsonObject): JsonObject = JsonObject(emptyMap())
    /** Значение секрета по имени (с учётом доступности; может спросить пользователя). null — недоступен. */
    suspend fun secret(name: String): String? = null
    /** Список секретов для модели — только имена/доступность/назначение, БЕЗ значений. */
    fun secretsInfo(): List<String> = emptyList()
    /** Эмит события наружу (например, показать картинку). */
    suspend fun emit(event: AgentEvent)
}

data class ToolResult(
    val content: String,
    val ok: Boolean = true,
) {
    companion object {
        fun fail(message: String): ToolResult = ToolResult("ERROR: $message", ok = false)
    }
}

interface Tool {
    val name: String
    val description: String
    val category: ToolCategory

    /** JSON-Schema аргументов (как в Python-инструментах). */
    fun schema(): JsonObject

    suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult

    /**
     * Автовердикт: спрашиваем согласие только у EXECUTE-инструментов (реальное исполнение
     * кода/команд: run_shell, run_python, pc_agent). READ/EDIT и NETWORK (веб-поиск, чтение
     * страниц, картинки, файловый обмен по мосту) в этом приложении безопасны и идут без спроса.
     * Инструмент может переопределить (например, pc_agent разрешает себя сам — делегирование
     * и есть согласие пользователя).
     */
    fun autoVerdict(args: JsonObject, ctx: ToolContext): Verdict =
        if (category == ToolCategory.EXECUTE) Verdict.ASK else Verdict.ALLOW
}

/** Реестр инструментов прогона. Пустой в Фазе 0 — инструменты добавляются в Фазе 1. */
class ToolRegistry(tools: List<Tool> = emptyList()) {
    private val byName = tools.associateBy { it.name }
    fun all(): List<Tool> = byName.values.toList()
    fun get(name: String): Tool? = byName[name]
    fun names(): List<String> = byName.keys.toList()
    /**
     * Полные function-спеки для модели: {name, description, parameters}. Клиент
     * оборачивает каждый как {"type":"function","function": spec}.
     */
    fun schemas(): List<JsonObject> = schemas(null)

    /** Specs for [names] only (deferred tool loading), or for every tool when [names] is null. */
    fun schemas(names: Collection<String>?): List<JsonObject> =
        byName.values.filter { names == null || it.name in names }.map { t ->
        buildJsonObject {
            put("name", t.name)
            put("description", t.description)
            put("parameters", t.schema())
        }
    }
}
