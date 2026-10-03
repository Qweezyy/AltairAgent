package com.localaiagent.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Контракты ядра — зеркалят Python-агента (core/agent, core/events.py), чтобы обе
 * реализации были концептуально одинаковыми. Ничего Android-специфичного здесь нет.
 */

enum class Role { SYSTEM, USER, ASSISTANT, TOOL }

@Serializable
sealed interface Part {
    @Serializable
    data class Text(val text: String) : Part

    /** Мультимодальная часть: data:-URI картинки (base64). */
    @Serializable
    data class Image(val dataUri: String) : Part
}

@Serializable
data class Message(
    val role: Role,
    val content: String = "",
    val parts: List<Part> = emptyList(),
    /** Для сообщений роли ASSISTANT с вызовами инструментов. */
    val toolCalls: List<ToolCall> = emptyList(),
    /** Для сообщений роли TOOL: id вызова, на который отвечаем. */
    val toolCallId: String? = null,
)

@Serializable
data class ToolCall(
    val id: String,
    val name: String,
    val arguments: String = "",
)

@Serializable
data class Usage(
    val promptTokens: Int = 0,
    val completionTokens: Int = 0,
    val totalTokens: Int = 0,
    /** Prompt tokens the provider served from its cache. */
    val cachedTokens: Int = 0,
)

/** Результат одного обращения к модели. */
data class AssistantTurn(
    val content: String = "",
    val reasoning: String = "",
    val toolCalls: List<ToolCall> = emptyList(),
    val usage: Usage = Usage(),
    val finishReason: String = "stop",
) {
    val wantsTools: Boolean get() = toolCalls.isNotEmpty()
}

/**
 * Поток событий агента — как на ПК (RunStarted, TextDelta, ToolStarted…). UI —
 * тонкий потребитель этого потока.
 */
sealed interface AgentEvent {
    data class RunStarted(val runId: String, val model: String) : AgentEvent
    data class TextDelta(val text: String) : AgentEvent
    data class ReasoningDelta(val text: String) : AgentEvent
    data class ToolStarted(val callId: String, val name: String, val args: JsonObject) : AgentEvent
    data class ToolFinished(
        val callId: String, val name: String, val ok: Boolean, val output: String,
    ) : AgentEvent
    /**
     * The stream broke mid-answer and the request is being retried from the start: the text
     * streamed by the failed attempt must be taken back, or the retry would print the answer twice.
     */
    data class TextRetracted(val chars: Int) : AgentEvent
    data class Reconnecting(
        val attempt: Int, val maxAttempts: Int, val delaySeconds: Double, val reason: String,
    ) : AgentEvent
    /** Сколько токенов сейчас занимает контекст — для кольца у строки ввода. */
    data class ContextUsage(val tokens: Int) : AgentEvent
    data class ShowImage(val url: String, val caption: String = "") : AgentEvent
    /** Встроенная графика/интерактив (SVG или HTML) в ответе. */
    data class ShowHtml(val html: String, val caption: String = "") : AgentEvent
    /** Прикрепить файл (из песочницы/ПК) в ответ: видео/аудио/документ/таблица. */
    data class ShowFile(val path: String, val caption: String = "") : AgentEvent
    data class RunFinished(val text: String, val usage: Usage) : AgentEvent
    data class RunFailed(val message: String) : AgentEvent
}
