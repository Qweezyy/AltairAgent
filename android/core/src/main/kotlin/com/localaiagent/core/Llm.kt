package com.localaiagent.core

import kotlinx.serialization.json.JsonObject

/**
 * Точка расширения №2: провайдер LLM. Новый провайдер = реализовать этот интерфейс
 * в модуле :llm. Повторяет наш Python openai_client (стрим + ретраи + ротация).
 */
interface LlmClient {
    suspend fun complete(
        messages: List<Message>,
        tools: List<JsonObject>? = null,
        onText: (suspend (String) -> Unit)? = null,
        onReasoning: (suspend (String) -> Unit)? = null,
        onRetry: (suspend (attempt: Int, max: Int, delaySeconds: Double, reason: String) -> Unit)? = null,
        maxTokens: Int? = null,
        /** Text already streamed was thrown away (a retry had to start over); undo [chars] of it. */
        onDiscard: (suspend (chars: Int) -> Unit)? = null,
    ): AssistantTurn

    suspend fun close() {}
}

/** Конфигурация модели (BYOK). Ключ приходит из безопасного хранилища, не логируется. */
data class LlmConfig(
    val baseUrl: String = "https://openrouter.ai/api/v1",
    val model: String = "anthropic/claude-sonnet-4.5",
    val apiKey: String = "",
    val temperature: Double? = 0.3,
    val timeoutMs: Long = 120_000,
    val maxRetries: Int = 5,
)
