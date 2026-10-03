package com.localaiagent.llm

import com.localaiagent.core.AssistantTurn
import com.localaiagent.core.LlmClient
import com.localaiagent.core.LlmConfig
import com.localaiagent.core.Message
import com.localaiagent.core.Part
import com.localaiagent.core.Role
import com.localaiagent.core.ToolCall
import com.localaiagent.core.Usage
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.HttpTimeoutConfig
import io.ktor.client.request.headers
import io.ktor.client.request.preparePost
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsChannel
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.utils.io.readUTF8Line
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import kotlin.math.min
import kotlin.random.Random

/**
 * A client for any OpenAI-compatible API (OpenRouter, GateYourWay, Ollama, LM Studio, vLLM): SSE
 * streaming, tool calls assembled from chunks, and the reliability layer from the PC (see
 * Reliability.kt) — silence-based timeouts, failures disguised as answers retried, reasoning effort
 * per provider dialect, fallback models behind a breaker, a per-provider concurrency limit.
 * BYOK: the key is never logged.
 */
class OpenAiCompatClient(
    private val config: LlmConfig,
) : LlmClient {

    // One HTTP client per provider in the chain. The whole-request timeout is off: a long but
    // healthy answer must never be cut; silence and a missing first byte end an attempt instead.
    private val clients = HashMap<String, HttpClient>()
    private fun http(cfg: LlmConfig): HttpClient = clients.getOrPut(cfg.baseUrl) {
        HttpClient(CIO) {
            // CIO has its own 15 s whole-request limit; 0 turns it off.
            engine { requestTimeout = 0 }
            install(HttpTimeout) {
                requestTimeoutMillis = HttpTimeoutConfig.INFINITE_TIMEOUT_MS
                connectTimeoutMillis = cfg.connectTimeoutMs
                socketTimeoutMillis = cfg.silenceTimeoutMs
            }
        }
    }
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = false }

    override suspend fun complete(
        messages: List<Message>,
        tools: List<JsonObject>?,
        onText: (suspend (String) -> Unit)?,
        onReasoning: (suspend (String) -> Unit)?,
        onRetry: (suspend (attempt: Int, max: Int, delaySeconds: Double, reason: String) -> Unit)?,
        maxTokens: Int?,
        onDiscard: (suspend (chars: Int) -> Unit)?,
        escalate: Boolean,
    ): AssistantTurn {
        // The main model first, then fallbacks; sick providers are skipped (all sick: try the main).
        val chain = (listOf(config) + config.fallbacks)
            .filter { !ProviderHealth.isSick(ProviderHealth.key(it.baseUrl, it.model)) }
            .ifEmpty { listOf(config) }
        val run = Run(messages, tools, onText, onReasoning, onRetry, maxTokens, onDiscard, escalate)
        var lastError: Throwable? = null
        var tried = 0
        for ((i, cfg) in chain.withIndex()) {
            val hasNext = i < chain.lastIndex
            // With a fallback behind it, the main model gets two attempts, not five.
            val attempts = if (hasNext) 2 else maxOf(1, cfg.maxRetries)
            try {
                return run.attempts(cfg, attempts)
            } catch (e: CancellationException) {
                throw e
            } catch (e: ModelLoopException) {
                throw e
            } catch (e: Throwable) {
                lastError = e
                tried += attempts
                ProviderHealth.markSick(ProviderHealth.key(cfg.baseUrl, cfg.model))
                if (hasNext) {
                    onRetry?.invoke(tried + 1, tried + 2, 0.0, "switching to ${chain[i + 1].model}: ${e.message}")
                }
            }
        }
        throw RuntimeException("Could not reach the model after $tried attempts: ${lastError?.message}")
    }

    /** The model generated without end twice: an honest error, not more retries. */
    private class ModelLoopException : RuntimeException("The model generated without end twice")

    /** One call: what the user has seen so far survives across attempts and providers. */
    private inner class Run(
        val messages: List<Message>,
        val tools: List<JsonObject>?,
        val onText: (suspend (String) -> Unit)?,
        val onReasoning: (suspend (String) -> Unit)?,
        val onRetry: (suspend (Int, Int, Double, String) -> Unit)?,
        val maxTokens: Int?,
        val onDiscard: (suspend (Int) -> Unit)?,
        val escalate: Boolean,
    ) {
        // What the user has already seen of this answer. A broken stream is continued from here
        // (the model gets its own partial reply and is asked to go on), so the answer is never
        // started over and never printed twice.
        val shown = StringBuilder()
        var loops = 0

        suspend fun discardShown() {
            if (shown.isNotEmpty()) { onDiscard?.invoke(shown.length); shown.clear() }
        }

        suspend fun attempts(cfg: LlmConfig, attempts: Int): AssistantTurn {
            var lastError: Throwable? = null
            var attempt = 1
            while (attempt <= attempts) {
                val acc = StreamAcc()
                val request = if (shown.isEmpty()) messages
                else messages + Message(Role.ASSISTANT, shown.toString()) + Message(Role.USER, CONTINUE_PROMPT)
                // After a runaway generation the retry thinks less, which is what breaks the loop.
                val effort = if (loops > 0) Effort.LOW else Effort.resolve(cfg.reasoningEffort, escalate)
                try {
                    val turn = streamOnce(cfg, request, tools, { shown.append(it); onText?.invoke(it) },
                        onReasoning, maxTokens, effort, acc)
                    val prefix = shown.substring(0, shown.length - acc.emitted)
                    return if (prefix.isEmpty()) turn else turn.copy(content = prefix + turn.content)
                } catch (t: Throwable) {
                    if (t is CancellationException) throw t
                    if (t is EffortRejectedException) {
                        // Sent once, refused: never again for this provider, and the attempt is free.
                        ProviderHealth.rejectEffortField(cfg.baseUrl)
                        continue
                    }
                    lastError = t
                    when {
                        t is FakeAnswerException -> {
                            if (t.reason == FakeAnswer.CUT && ++loops >= 2) throw ModelLoopException()
                            // A disguised failure is not continued: whatever was shown goes.
                            discardShown()
                        }
                        // A half-received tool call cannot be continued as text: start this answer over.
                        acc.sawToolCall -> discardShown()
                    }
                    if (attempt == attempts) break
                    val delaySec = (min(2.0 * (1 shl (attempt - 1)), 10.0) + Random.nextDouble(0.2, 0.8)) *
                        cfg.retryDelayScale
                    onRetry?.invoke(attempt + 1, attempts, delaySec, t.message ?: t.toString())
                    delay((delaySec * 1000).toLong())
                    attempt++
                }
            }
            throw lastError ?: RuntimeException("no attempts")
        }
    }

    /** What one streaming attempt received before it ended or broke. */
    private class StreamAcc {
        /** Characters passed on to the screen (the opening is held back until it is safe). */
        var emitted = 0
        var sawToolCall = false
    }

    private class EffortRejectedException : RuntimeException("effort field rejected")

    private suspend fun streamOnce(
        cfg: LlmConfig,
        messages: List<Message>,
        tools: List<JsonObject>?,
        onText: (suspend (String) -> Unit)?,
        onReasoning: (suspend (String) -> Unit)?,
        maxTokens: Int?,
        effort: String?,
        acc: StreamAcc,
    ): AssistantTurn {
        val field = if (ProviderHealth.effortFieldRejected(cfg.baseUrl)) null
        else effortField(cfg.baseUrl, cfg.model, effort)
        val body = buildRequest(cfg, messages, tools, maxTokens, field)
        val url = cfg.baseUrl.trimEnd('/') + "/chat/completions"
        val healthKey = ProviderHealth.key(cfg.baseUrl, cfg.model)
        val limiter = ConcurrencyLimiter.forUrl(cfg.baseUrl)

        val sbContent = StringBuilder()
        val sbReasoning = StringBuilder()
        val calls = LinkedHashMap<Int, MutableToolCall>()
        var finishReason = "stop"
        var usage = Usage()
        // A stream that just stops, with neither [DONE] nor a finish_reason, was cut off.
        var completed = false
        // The opening of the answer is held back until it cannot be a gateway error text, so such a
        // text never lands in the chat.
        val held = StringBuilder()
        suspend fun emit(text: String) {
            if (text.isEmpty()) return
            acc.emitted += text.length
            onText?.invoke(text)
        }
        suspend fun take(text: String) {
            if (acc.emitted > 0) { emit(text); return }
            held.append(text)
            if (held.length >= HOLD_CHARS && !FakeAnswer.GATEWAY_PHRASES.any { held.toString().lowercase().contains(it) }) {
                val out = held.toString(); held.clear(); emit(out)
            }
        }

        limiter.acquire()
        var released = false
        suspend fun release() { if (!released) { released = true; limiter.release() } }
        try {
            val started = System.currentTimeMillis()
            coroutineScope {
                // The first byte has its own deadline; once data flows, only silence ends the stream.
                val watchdog = launch {
                    delay(cfg.firstByteTimeoutMs)
                    throw FirstByteTimeoutException(cfg.firstByteTimeoutMs / 1000)
                }
                http(cfg).preparePost(url) {
                    contentType(ContentType.Application.Json)
                    headers {
                        append(HttpHeaders.Authorization, "Bearer ${cfg.apiKey}")
                        if (cfg.baseUrl.contains("openrouter")) {
                            append("HTTP-Referer", "https://localhost")
                            append("X-Title", "Local AI Agent")
                        }
                    }
                    setBody(json.encodeToString(JsonObject.serializer(), body))
                }.execute { response ->
                    if (!response.status.isSuccess()) {
                        watchdog.cancel()
                        val text = response.bodyAsText().take(400)
                        val code = response.status.value
                        if (code == 400 && field != null && text.contains(field.name)) throw EffortRejectedException()
                        // Counted while this request is still in flight, as the limit must be.
                        if (isConcurrencyLimit(code, text)) limiter.onConcurrencyLimited()
                        throw RuntimeException("HTTP $code: $text")
                    }
                    val channel = response.bodyAsChannel()
                    var first = true
                    while (true) {
                        val line = channel.readUTF8Line() ?: break
                        if (first) {
                            first = false
                            watchdog.cancel()
                            ProviderHealth.noteFirstByte(healthKey, System.currentTimeMillis() - started, cfg.slowFirstByteMs)
                        }
                        if (!line.startsWith("data:")) continue
                        val data = line.removePrefix("data:").trim()
                        if (data == "[DONE]") { completed = true; break }
                        if (data.isEmpty()) continue

                        val chunk = runCatching { json.parseToJsonElement(data).jsonObject }.getOrNull() ?: continue
                        (chunk["usage"] as? JsonObject)?.let { u ->
                            usage = Usage(
                                promptTokens = u["prompt_tokens"].str()?.toIntOrNull() ?: 0,
                                completionTokens = u["completion_tokens"].str()?.toIntOrNull() ?: 0,
                                totalTokens = u["total_tokens"].str()?.toIntOrNull() ?: 0,
                                cachedTokens = ((u["prompt_tokens_details"] as? JsonObject)?.get("cached_tokens"))
                                    .str()?.toIntOrNull() ?: 0,
                            )
                        }
                        val choice = (chunk["choices"] as? JsonArray)?.firstOrNull() as? JsonObject ?: continue
                        choice["finish_reason"].str()?.let { finishReason = it; completed = true }
                        val delta = choice["delta"] as? JsonObject ?: continue

                        delta["content"].str()?.let {
                            if (it.isNotEmpty()) {
                                sbContent.append(it)
                                // A runaway generation: stopped here and retried with less thinking.
                                if (sbContent.length > LOOP_CHARS) throw FakeAnswerException(FakeAnswer.CUT)
                                take(it)
                            }
                        }
                        (delta["reasoning"].str() ?: delta["reasoning_content"].str())?.let {
                            if (it.isNotEmpty()) { sbReasoning.append(it); onReasoning?.invoke(it) }
                        }
                        val toolCalls = delta["tool_calls"] as? JsonArray
                        if (!toolCalls.isNullOrEmpty()) acc.sawToolCall = true
                        toolCalls?.forEach { tcEl ->
                            val tc = tcEl as? JsonObject ?: return@forEach
                            val index = tc["index"].str()?.toIntOrNull() ?: 0
                            val call = calls.getOrPut(index) { MutableToolCall() }
                            tc["id"].str()?.let { call.id = it }
                            (tc["function"] as? JsonObject)?.let { fn ->
                                // Some providers (gateyourway) send the full name in the first chunk and an
                                // empty name="" with the later argument chunks: never overwrite with "".
                                fn["name"].str()?.let { call.name += it }
                                fn["arguments"].str()?.let { call.args.append(it) }
                            }
                        }
                    }
                    watchdog.cancel()
                    if (!completed) throw java.io.IOException("The answer stream ended before the model finished")
                }
            }
        } finally {
            release()
        }

        val turn = AssistantTurn(
            content = sbContent.toString(),
            reasoning = sbReasoning.toString(),
            toolCalls = calls.entries.sortedBy { it.key }.mapNotNull { (i, c) ->
                if (c.name.isBlank()) null
                else ToolCall(id = c.id.ifBlank { "call_$i" }, name = c.name, arguments = c.args.toString())
            },
            usage = usage,
            finishReason = finishReason,
        )
        FakeAnswer.classify(turn.content, turn.toolCalls.isNotEmpty(), usage.promptTokens, requestChars(messages))
            ?.let { throw FakeAnswerException(it) }
        // A real answer: what was held back goes out now.
        emit(held.toString())
        limiter.onSuccess()
        return turn
    }

    /** The text size of a request, images left out. */
    private fun requestChars(messages: List<Message>): Int =
        messages.sumOf { it.content.length + it.toolCalls.sumOf { c -> c.arguments.length } }

    private fun buildRequest(
        cfg: LlmConfig,
        messages: List<Message>,
        tools: List<JsonObject>?,
        maxTokens: Int?,
        effort: EffortField?,
    ): JsonObject = buildJsonObject {
        put("model", cfg.model)
        put("stream", true)
        cfg.temperature?.let { put("temperature", it) }
        maxTokens?.let { put("max_tokens", it) }
        effort?.let { put(it.name, it.value) }
        putJsonArray("messages") {
            for (m in messages) addJsonObject {
                put("role", m.role.name.lowercase())
                // Multimodal: with pictures, content goes as an array of text + image_url parts.
                val images = m.parts.filterIsInstance<Part.Image>()
                if (images.isEmpty()) {
                    put("content", m.content)
                } else {
                    putJsonArray("content") {
                        if (m.content.isNotBlank()) {
                            addJsonObject { put("type", "text"); put("text", m.content) }
                        }
                        for (img in images) addJsonObject {
                            put("type", "image_url")
                            putJsonObject("image_url") { put("url", img.dataUri) }
                        }
                    }
                }
                m.toolCallId?.let { put("tool_call_id", it) }
                if (m.toolCalls.isNotEmpty()) {
                    putJsonArray("tool_calls") {
                        for (c in m.toolCalls) addJsonObject {
                            put("id", c.id)
                            put("type", "function")
                            putJsonObject("function") {
                                put("name", c.name)
                                put("arguments", c.arguments.ifBlank { "{}" })
                            }
                        }
                    }
                }
            }
        }
        if (!tools.isNullOrEmpty()) {
            putJsonArray("tools") {
                for (t in tools) addJsonObject {
                    put("type", "function")
                    put("function", t)
                }
            }
            put("tool_choice", "auto")
        }
    }

    override suspend fun close() { clients.values.forEach { it.close() } }

    private class MutableToolCall {
        var id: String = ""
        var name: String = ""
        val args = StringBuilder()
    }

    companion object {
        /** Sent after the model's own partial reply when its stream broke, so it goes on from there. */
        const val CONTINUE_PROMPT =
            "[The connection dropped in the middle of your answer. Continue it exactly from where it " +
                "stopped: do not repeat anything already written and do not start over.]"

        /** The opening held back until it cannot be a gateway error text. */
        const val HOLD_CHARS = 160

        /** An answer longer than this is a runaway generation. */
        const val LOOP_CHARS = 160_000
    }
}

/**
 * A field read as text, or null when it is missing, JSON null or not a primitive. Providers send
 * "usage": null, "reasoning": null and the like in stream chunks; a strict read would throw on them.
 */
private fun JsonElement?.str(): String? = (this as? JsonPrimitive)?.contentOrNull
