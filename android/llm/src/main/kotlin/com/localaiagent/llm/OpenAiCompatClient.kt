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
import kotlinx.coroutines.delay
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import kotlin.math.min
import kotlin.random.Random

/**
 * Клиент к любому OpenAI-совместимому API (OpenRouter, Ollama, LM Studio, vLLM).
 * Стриминг через SSE, сборка tool_calls из чанков, ретраи с backoff — как в
 * Python-версии (core/llm/openai_client.py). BYOK: ключ не логируется.
 *
 * TODO (Фаза 1+): ротация НЕСКОЛЬКИХ ключей/сервисов при лимите — как на ПК.
 */
class OpenAiCompatClient(
    private val config: LlmConfig,
) : LlmClient {

    // HttpClient держим внутри, чтобы Ktor не протекал в модуль :app.
    // ВАЖНО: у CIO дефолтный requestTimeout 15 c — мало для медленных провайдеров
    // (gateyourway отвечает 15–16 c). Поднимаем до таймаута из конфига.
    private val http: HttpClient = HttpClient(CIO) {
        engine { requestTimeout = config.timeoutMs }
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
    ): AssistantTurn {
        val attempts = maxOf(1, config.maxRetries)
        var lastError: Throwable? = null
        // What the user has already seen of this answer. A broken stream is continued from here
        // (the model gets its own partial reply and is asked to go on), so the answer is never
        // started over and never printed twice.
        val shown = StringBuilder()
        for (attempt in 1..attempts) {
            val acc = StreamAcc()
            val request = if (shown.isEmpty()) messages
            else messages + Message(Role.ASSISTANT, shown.toString()) + Message(Role.USER, CONTINUE_PROMPT)
            try {
                val turn = streamOnce(
                    request, tools, { shown.append(it); onText?.invoke(it) }, onReasoning, maxTokens, acc,
                )
                val prefix = shown.substring(0, shown.length - acc.content.length)
                return if (prefix.isEmpty()) turn else turn.copy(content = prefix + turn.content)
            } catch (t: Throwable) {
                if (t is kotlinx.coroutines.CancellationException) throw t
                lastError = t
                // A half-received tool call cannot be continued as text: start this answer over.
                if (acc.sawToolCall && shown.isNotEmpty()) {
                    onDiscard?.invoke(shown.length)
                    shown.clear()
                }
                if (attempt == attempts) break
                val delaySec = min(2.0 * attempt, 10.0) + Random.nextDouble(0.2, 0.8)
                onRetry?.invoke(attempt + 1, attempts, delaySec, t.message ?: t.toString())
                delay((delaySec * 1000).toLong())
            }
        }
        throw RuntimeException(
            "Could not reach the model after $attempts attempts: ${lastError?.message}",
        )
    }

    /** What one streaming attempt received before it ended or broke. */
    private class StreamAcc {
        val content = StringBuilder()
        var sawToolCall = false
    }

    private suspend fun streamOnce(
        messages: List<Message>,
        tools: List<JsonObject>?,
        onText: (suspend (String) -> Unit)?,
        onReasoning: (suspend (String) -> Unit)?,
        maxTokens: Int?,
        acc: StreamAcc = StreamAcc(),
    ): AssistantTurn {
        val body = buildRequest(messages, tools, maxTokens)
        val url = config.baseUrl.trimEnd('/') + "/chat/completions"

        val sbContent = StringBuilder()
        val sbReasoning = StringBuilder()
        val calls = LinkedHashMap<Int, MutableToolCall>()
        var finishReason = "stop"
        var usage = Usage()
        // A stream that just stops, with neither [DONE] nor a finish_reason, was cut off.
        var completed = false

        http.preparePost(url) {
            contentType(ContentType.Application.Json)
            headers {
                append(HttpHeaders.Authorization, "Bearer ${config.apiKey}")
                if (config.baseUrl.contains("openrouter")) {
                    append("HTTP-Referer", "https://localhost")
                    append("X-Title", "Local AI Agent")
                }
            }
            setBody(json.encodeToString(JsonObject.serializer(), body))
        }.execute { response ->
            if (!response.status.isSuccess()) {
                // Ошибку провайдера показываем явно, а не молчим пустым ответом.
                throw RuntimeException("HTTP ${response.status.value}: ${response.bodyAsText().take(400)}")
            }
            val channel = response.bodyAsChannel()
            while (true) {
                val line = channel.readUTF8Line() ?: break
                if (!line.startsWith("data:")) continue
                val data = line.removePrefix("data:").trim()
                if (data == "[DONE]") { completed = true; break }
                if (data.isEmpty()) continue

                val chunk = runCatching { json.parseToJsonElement(data).jsonObject }.getOrNull() ?: continue
                chunk["usage"]?.jsonObject?.let { u ->
                    usage = Usage(
                        promptTokens = u["prompt_tokens"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 0,
                        completionTokens = u["completion_tokens"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 0,
                        totalTokens = u["total_tokens"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 0,
                    )
                }
                val choice = chunk["choices"]?.jsonArray?.firstOrNull()?.jsonObject ?: continue
                choice["finish_reason"]?.jsonPrimitive?.contentOrNull?.let { finishReason = it; completed = true }
                val delta = choice["delta"]?.jsonObject ?: continue

                delta["content"]?.jsonPrimitive?.contentOrNull?.let {
                    if (it.isNotEmpty()) { sbContent.append(it); acc.content.append(it); onText?.invoke(it) }
                }
                (delta["reasoning"] ?: delta["reasoning_content"])?.jsonPrimitive?.contentOrNull?.let {
                    if (it.isNotEmpty()) { sbReasoning.append(it); onReasoning?.invoke(it) }
                }
                delta["tool_calls"]?.jsonArray?.let { if (it.isNotEmpty()) acc.sawToolCall = true }
                delta["tool_calls"]?.jsonArray?.forEach { tcEl ->
                    val tc = tcEl.jsonObject
                    val index = tc["index"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 0
                    val call = calls.getOrPut(index) { MutableToolCall() }
                    tc["id"]?.jsonPrimitive?.contentOrNull?.let { call.id = it }
                    tc["function"]?.jsonObject?.let { fn ->
                        // ВАЖНО: некоторые провайдеры (gateyourway) шлют полное имя в первом
                        // чанке, а дальше — пустое name="" в чанках с кусками arguments.
                        // Поэтому НЕ перезаписываем непустое имя пустым — накапливаем куски.
                        fn["name"]?.jsonPrimitive?.contentOrNull?.let { call.name += it }
                        fn["arguments"]?.jsonPrimitive?.contentOrNull?.let { call.args.append(it) }
                    }
                }
            }
            if (!completed) throw java.io.IOException("The answer stream ended before the model finished")
        }

        return AssistantTurn(
            content = sbContent.toString(),
            reasoning = sbReasoning.toString(),
            toolCalls = calls.entries.sortedBy { it.key }.mapNotNull { (i, c) ->
                if (c.name.isBlank()) null
                else ToolCall(id = c.id.ifBlank { "call_$i" }, name = c.name, arguments = c.args.toString())
            },
            usage = usage,
            finishReason = finishReason,
        )
    }

    private fun buildRequest(
        messages: List<Message>,
        tools: List<JsonObject>?,
        maxTokens: Int?,
    ): JsonObject = buildJsonObject {
        put("model", config.model)
        put("stream", true)
        config.temperature?.let { put("temperature", it) }
        maxTokens?.let { put("max_tokens", it) }
        putJsonArray("messages") {
            for (m in messages) addJsonObject {
                put("role", m.role.name.lowercase())
                // Мультимодал: если в сообщении есть картинки — content уходит массивом
                // {type:text}+{type:image_url}. Иначе — обычной строкой.
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

    override suspend fun close() { http.close() }

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
    }
}
