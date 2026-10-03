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
        /** The previous step failed (a tool error, failing tests): an adaptive client thinks harder. */
        escalate: Boolean = false,
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
    /** adaptive | low | medium | high | provider (send nothing). */
    val reasoningEffort: String = "adaptive",
    /** A healthy stream is never cut by its length: only by silence or a missing first byte. */
    val connectTimeoutMs: Long = 15_000,
    val silenceTimeoutMs: Long = 60_000,
    val firstByteTimeoutMs: Long = 90_000,
    /** A first byte slower than this, twice in a row, makes the provider "sick" for a while. */
    val slowFirstByteMs: Long = 40_000,
    /** Models (each with its own provider and key) that take the steps while this one is sick. */
    val fallbacks: List<LlmConfig> = emptyList(),
    /** Pauses between attempts grow 2/4/8 s plus jitter; scaled down in tests. */
    val retryDelayScale: Double = 1.0,
)
