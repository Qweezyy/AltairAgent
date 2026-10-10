package com.localaiagent.llm

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * What keeps model answers coming on flaky providers, as measured on the PC (core/llm/reliability.py):
 * failures that arrive as a "200 OK" answer, reasoning effort per provider dialect, a breaker that
 * moves steps to a fallback model while a provider is slow or down, and a per-provider limit of
 * concurrent requests. All of it is process-wide and pure enough to test without a network.
 */

/** Reasoning effort settings, as stored in LlmConfig.reasoningEffort. */
object Effort {
    const val ADAPTIVE = "adaptive"
    const val LOW = "low"
    const val MEDIUM = "medium"
    const val HIGH = "high"
    /** Send nothing: the provider decides. */
    const val PROVIDER = "provider"

    /**
     * The effort to request for one step. Adaptive is low, and high on the step right after a failed
     * tool or failing tests ([escalate]); a fixed setting is sent as is; [PROVIDER] sends nothing.
     */
    fun resolve(setting: String, escalate: Boolean): String? = when (setting) {
        PROVIDER -> null
        LOW, MEDIUM, HIGH -> setting
        else -> if (escalate) HIGH else LOW
    }
}

/** The request field that carries reasoning effort, in the dialect the provider understands. */
internal data class EffortField(val name: String, val value: JsonElement)

/**
 * OpenRouter takes `reasoning: {effort}`; Z.ai-like providers (GateYourWay, z.ai, bigmodel, and glm
 * models not via OpenRouter) take `thinking: {type: enabled|disabled}`; everyone else
 * `reasoning_effort`. Null effort means the field is not sent.
 */
internal fun effortField(baseUrl: String, model: String, effort: String?): EffortField? {
    if (effort == null) return null
    val url = baseUrl.lowercase()
    val m = model.lowercase()
    return when {
        // Gemini (anywhere but OpenRouter) obeys reasoning_effort and ignores the Z.ai "thinking" field,
        // even behind GateYourWay: measured 2026-10-10, "thinking: disabled" changed nothing.
        "gemini" in m && "openrouter.ai" !in url -> EffortField("reasoning_effort", JsonPrimitive(effort))
        "openrouter.ai" in url -> EffortField(
            "reasoning", buildJsonObject { put("effort", if (effort == "minimal") Effort.LOW else effort) },
        )
        "gateyourway" in url || "z.ai" in url || "bigmodel.cn" in url || model.lowercase().startsWith("glm") ->
            EffortField(
                "thinking",
                buildJsonObject { put("type", if (effort == Effort.LOW || effort == "minimal") "disabled" else "enabled") },
            )
        else -> EffortField("reasoning_effort", JsonPrimitive(effort))
    }
}

/** Why a "successful" answer is really a failure; null when it is a real answer. */
internal object FakeAnswer {
    const val CUT = "cut"
    const val EMPTY = "empty"
    const val GATEWAY = "gateway"
    const val UNPROCESSED = "unprocessed"

    /** Gateway error texts that arrive as the model's answer with status 200. */
    val GATEWAY_PHRASES = listOf(
        "the request could not be completed",
        "please retry later, or reduce the request",
        "upstream request failed",
        "no healthy upstream",
        "service temporarily unavailable",
    )

    /** An error text from the gateway: short and holding one of the known phrases. */
    fun isGatewayText(text: String): Boolean =
        text.length < 400 && GATEWAY_PHRASES.any { text.lowercase().contains(it) }

    /**
     * Classifies a finished turn without tool calls. [requestChars] is the text size of the request
     * (images excluded): a provider that reports far fewer prompt tokens than that did not read it.
     */
    fun classify(content: String, hasToolCalls: Boolean, promptTokens: Int, requestChars: Int): String? {
        if (hasToolCalls) return null
        if (content.isBlank()) return EMPTY
        if (isGatewayText(content)) return GATEWAY
        val expected = requestChars / 4.5
        if (expected > 2000 && promptTokens > 0 && promptTokens < 0.5 * expected) return UNPROCESSED
        return null
    }
}

/** An answer that looked fine but was a failure; retried like a broken connection. */
internal class FakeAnswerException(val reason: String) : RuntimeException(
    when (reason) {
        FakeAnswer.CUT -> "The model kept generating without end"
        FakeAnswer.EMPTY -> "The model sent an empty answer"
        FakeAnswer.GATEWAY -> "The provider sent an error text instead of an answer"
        else -> "The provider did not process the request"
    },
)

/** The first byte of the answer did not come in time. */
internal class FirstByteTimeoutException(seconds: Long) :
    java.io.IOException("No answer from the model for $seconds s")

/** The answer stopped flowing: no data for too long after it had started. */
internal class StreamSilenceException(seconds: Long) :
    java.io.IOException("The answer stopped coming for $seconds s")

/**
 * The model declined (a safety or content filter) and sent nothing. Asking again bills the whole prompt
 * again and gets the same refusal, so this is an honest error, not a retry.
 */
internal class ModelRefusedException(reason: String) :
    RuntimeException("The model declined to answer ($reason). Rephrase the request or remove the attachment it objects to.")

/** finish_reason values of a refusal (OpenAI-style and Gemini's own, lower-cased). */
internal val REFUSAL_REASONS = setOf(
    "content_filter", "safety", "prohibited_content", "blocklist", "spii", "recitation", "image_safety",
)

/**
 * How long to wait for the first byte of an answer: the base wait, plus time for the model to read and
 * think over a big prompt, plus time to upload the request from a slow phone network. A small chat
 * gets about the base; a 230K-token prompt with a video several minutes.
 */
internal fun firstByteBudgetMs(
    baseMs: Long, per100kTokensMs: Long, uploadBytesPerSec: Long,
    estimatedTokens: Long, bodyBytes: Long,
): Long {
    val reading = estimatedTokens * per100kTokensMs / 100_000
    val upload = if (uploadBytesPerSec > 0) bodyBytes * 1000 / uploadBytesPerSec else 0
    return (baseMs + reading + upload).coerceAtMost(15 * 60_000L)
}

/**
 * Per-provider health shared by all chats. A provider is sick for [SICK_FOR_MS] after it failed all
 * its attempts, or after its first byte came later than the slow threshold twice in a row: slow
 * periods last hours, and retrying into them does not help. Steps then go to the fallback.
 */
object ProviderHealth {
    const val SICK_FOR_MS = 10 * 60_000L

    /** The clock, replaceable in tests. */
    var now: () -> Long = { System.currentTimeMillis() }

    private val sickUntil = HashMap<String, Long>()
    private val slowStreak = HashMap<String, Int>()
    private val rejectedEffortField = HashSet<String>()

    fun key(baseUrl: String, model: String) = baseUrl.trimEnd('/').lowercase() + "|" + model

    @Synchronized fun isSick(key: String): Boolean = (sickUntil[key] ?: 0L) > now()

    @Synchronized fun markSick(key: String) {
        sickUntil[key] = now() + SICK_FOR_MS
        slowStreak.remove(key)
    }

    /** Records how long the first byte took; two slow ones in a row make the provider sick. */
    @Synchronized fun noteFirstByte(key: String, millis: Long, slowMillis: Long) {
        if (millis <= slowMillis) { slowStreak.remove(key); return }
        val n = (slowStreak[key] ?: 0) + 1
        if (n >= 2) markSick(key) else slowStreak[key] = n
    }

    /** A provider that answered 400 to the effort field never gets it again. */
    @Synchronized fun rejectEffortField(baseUrl: String) { rejectedEffortField += baseUrl.trimEnd('/').lowercase() }

    @Synchronized fun effortFieldRejected(baseUrl: String) = baseUrl.trimEnd('/').lowercase() in rejectedEffortField

    @Synchronized fun reset() {
        sickUntil.clear(); slowStreak.clear(); rejectedEffortField.clear()
        now = { System.currentTimeMillis() }
    }
}

/**
 * Concurrent requests to one provider. Unlimited until the provider answers 429 about concurrency;
 * then the limit becomes (in flight - 1), at least 1, and grows back by one after every
 * [GROW_AFTER] clean answers. Waiting for a slot suspends, it never blocks a thread.
 */
class ConcurrencyLimiter {
    private val mutex = Mutex()
    private val changes = MutableStateFlow(0L)
    var limit: Int = Int.MAX_VALUE
        private set
    var inFlight: Int = 0
        private set
    private var clean = 0

    suspend fun acquire() {
        while (true) {
            val seen = changes.value
            val got = mutex.withLock {
                if (inFlight < limit) { inFlight++; true } else false
            }
            if (got) return
            changes.first { it != seen }
        }
    }

    suspend fun release() {
        mutex.withLock { inFlight = (inFlight - 1).coerceAtLeast(0) }
        changes.value++
    }

    /** Call while the failed request still counts as in flight. */
    suspend fun onConcurrencyLimited() {
        mutex.withLock {
            limit = (inFlight - 1).coerceAtLeast(1)
            clean = 0
        }
    }

    suspend fun onSuccess() {
        mutex.withLock {
            if (limit == Int.MAX_VALUE) return@withLock
            clean++
            if (clean >= GROW_AFTER) { limit++; clean = 0 }
        }
        changes.value++
    }

    companion object {
        const val GROW_AFTER = 20
        private val byUrl = HashMap<String, ConcurrencyLimiter>()

        @Synchronized fun forUrl(baseUrl: String): ConcurrencyLimiter =
            byUrl.getOrPut(baseUrl.trimEnd('/').lowercase()) { ConcurrencyLimiter() }

        @Synchronized fun resetAll() = byUrl.clear()
    }
}

/** A 429 that is about parallel requests rather than rate or quota. */
internal fun isConcurrencyLimit(status: Int, body: String): Boolean =
    status == 429 && body.lowercase().let { "concurren" in it || "parallel" in it || "simultaneous" in it }
