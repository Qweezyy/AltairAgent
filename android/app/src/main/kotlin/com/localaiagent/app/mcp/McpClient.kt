package com.localaiagent.app.mcp

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.ResponseBody
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** A remote MCP tool: name, description and the JSON Schema of its arguments. */
data class McpToolSpec(val name: String, val description: String, val inputSchema: JsonObject)

/**
 * MCP client over HTTP, two transports:
 *  - "http": Streamable HTTP (2025-03-26). One POST endpoint; the reply is the body (application/json)
 *    or a short SSE stream in the body. The session travels in the Mcp-Session-Id header.
 *  - "sse": the older HTTP+SSE (2024-11-05). A GET opens a stream, the server sends an `endpoint`
 *    event with the POST URL, and replies come back on the open GET stream.
 *
 * Every operation (listTools / callTool) is its own initialize→… session: calls from a phone are rare,
 * and this keeps no connection state to go stale. All network waits are cancellable, so a timeout or
 * a stopped run never leaves a request hanging.
 */
class McpClient(private val server: McpServer) {

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = false }
    private val idSeq = AtomicInteger(1)

    /** The server's tools (initialize + tools/list). */
    suspend fun listTools(): List<McpToolSpec> = withSession { send ->
        val result = send(rpc("tools/list", buildJsonObject {}))
        result["tools"]?.jsonArray?.mapNotNull { el ->
            val o = el.jsonObject
            val n = o["name"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            McpToolSpec(
                name = n,
                description = o["description"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                inputSchema = o["inputSchema"]?.jsonObject ?: emptyObjectSchema(),
            )
        } ?: emptyList()
    }

    /** Calls a tool. Returns (ok, text result). */
    suspend fun callTool(name: String, args: JsonObject): Pair<Boolean, String> = withSession { send ->
        val result = send(rpc("tools/call", buildJsonObject { put("name", name); put("arguments", args) }))
        val isError = result["isError"]?.jsonPrimitive?.booleanOrNull == true
        !isError to renderContent(result)
    }

    // ------------------------------------------------------------- transport

    private suspend fun <T> withSession(block: suspend (send: suspend (JsonObject) -> JsonObject) -> T): T {
        // Auth headers never go over plain http:// to a public host (see NetPolicy).
        if (server.headers.isNotEmpty() && !com.localaiagent.core.NetPolicy.credentialsAllowed(server.url)) {
            throw IOException("Refusing to send MCP credentials over unencrypted http:// to a public address.")
        }
        return withSessionUnchecked(block)
    }

    private suspend fun <T> withSessionUnchecked(block: suspend (send: suspend (JsonObject) -> JsonObject) -> T): T =
        withContext(Dispatchers.IO) {
            if (server.transport.equals("sse", ignoreCase = true)) sseSession(block) else httpSession(block)
        }

    private suspend fun <T> httpSession(block: suspend (suspend (JsonObject) -> JsonObject) -> T): T {
        var sessionId: String? = null

        suspend fun post(msg: JsonObject, expectResponse: Boolean): JsonObject? {
            val builder = Request.Builder().url(server.url)
                .post(json.encodeToString(JsonObject.serializer(), msg).toRequestBody(JSON_MEDIA))
                .header("Accept", "application/json, text/event-stream")
            server.headers.forEach { (k, v) -> builder.header(k, v) }
            sessionId?.let { builder.header("Mcp-Session-Id", it) }
            HTTP.newCall(builder.build()).await().use { resp ->
                resp.header("Mcp-Session-Id")?.let { sessionId = it }
                if (!resp.isSuccessful) {
                    throw IOException("HTTP ${resp.code}: ${resp.body?.string()?.take(300).orEmpty()}")
                }
                if (!expectResponse) return null
                val body = resp.body ?: throw IOException("empty server response")
                val ct = resp.header("Content-Type").orEmpty()
                return if (ct.contains("text/event-stream")) {
                    readSseForId(body, msg["id"]?.jsonPrimitive?.contentOrNull)
                } else {
                    json.parseToJsonElement(body.string()).jsonObject
                }
            }
        }

        val initResp = post(rpc("initialize", initParams()), true)!!
        initResp["error"]?.let { throw IOException(rpcError(it)) }
        runCatching { post(notify("notifications/initialized"), false) }

        val send: suspend (JsonObject) -> JsonObject = { req ->
            val resp = post(req, true)!!
            resp["error"]?.let { throw IOException(rpcError(it)) }
            resp["result"]?.jsonObject ?: JsonObject(emptyMap())
        }
        return block(send)
    }

    private suspend fun <T> sseSession(block: suspend (suspend (JsonObject) -> JsonObject) -> T): T = coroutineScope {
        val getBuilder = Request.Builder().url(server.url).header("Accept", "text/event-stream")
        server.headers.forEach { (k, v) -> getBuilder.header(k, v) }
        val resp = STREAM.newCall(getBuilder.build()).await()
        if (!resp.isSuccessful) { resp.close(); throw IOException("HTTP ${resp.code} opening SSE") }
        val source = (resp.body ?: run { resp.close(); throw IOException("empty SSE stream") }).source()

        val endpointCh = CompletableDeferred<String>()
        val pending = ConcurrentHashMap<String, CompletableDeferred<JsonObject>>()
        val reader = launch(Dispatchers.IO) {
            try {
                var event = "message"
                val data = StringBuilder()
                while (isActive) {
                    val line = source.readUtf8Line() ?: break
                    if (line.isEmpty()) {
                        val payload = data.toString(); val ev = event
                        data.clear(); event = "message"
                        if (payload.isEmpty()) continue
                        if (ev == "endpoint") {
                            if (!endpointCh.isCompleted) endpointCh.complete(payload)
                        } else {
                            val obj = runCatching { json.parseToJsonElement(payload).jsonObject }.getOrNull() ?: continue
                            obj["id"]?.jsonPrimitive?.contentOrNull?.let { pending.remove(it)?.complete(obj) }
                        }
                    } else when {
                        line.startsWith("event:") -> event = line.removePrefix("event:").trim()
                        line.startsWith("data:") -> appendData(data, line)
                    }
                }
            } catch (_: Exception) {
                // The stream closed (or was closed by us); waiters get the error in finally.
            } finally {
                pending.values.forEach { if (!it.isCompleted) it.completeExceptionally(IOException("SSE stream closed")) }
            }
        }

        try {
            val endpoint = withTimeout(20_000) { endpointCh.await() }
            val postUrl = server.url.toHttpUrl().resolve(endpoint)
                ?: throw IOException("could not parse MCP endpoint: $endpoint")

            suspend fun post(msg: JsonObject, expectResponse: Boolean): JsonObject? {
                val id = msg["id"]?.jsonPrimitive?.contentOrNull
                val deferred = if (expectResponse && id != null) {
                    CompletableDeferred<JsonObject>().also { pending[id] = it }
                } else {
                    null
                }
                val builder = Request.Builder().url(postUrl)
                    .post(json.encodeToString(JsonObject.serializer(), msg).toRequestBody(JSON_MEDIA))
                server.headers.forEach { (k, v) -> builder.header(k, v) }
                HTTP.newCall(builder.build()).await().use { r ->
                    if (!r.isSuccessful) {
                        id?.let { pending.remove(it) }
                        throw IOException("HTTP ${r.code}: ${r.body?.string()?.take(300).orEmpty()}")
                    }
                }
                return deferred?.let { withTimeout(60_000) { it.await() } }
            }

            val initResp = post(rpc("initialize", initParams()), true)!!
            initResp["error"]?.let { throw IOException(rpcError(it)) }
            runCatching { post(notify("notifications/initialized"), false) }

            val send: suspend (JsonObject) -> JsonObject = { req ->
                val r = post(req, true)!!
                r["error"]?.let { throw IOException(rpcError(it)) }
                r["result"]?.jsonObject ?: JsonObject(emptyMap())
            }
            block(send)
        } finally {
            reader.cancel()
            resp.close()
        }
    }

    /** Reads the short SSE stream of a Streamable HTTP reply up to the message with the wanted id. */
    private fun readSseForId(body: ResponseBody, wantId: String?): JsonObject {
        val source = body.source()
        val data = StringBuilder()
        while (true) {
            val line = source.readUtf8Line() ?: break
            if (line.isEmpty()) {
                if (data.isNotEmpty()) {
                    val obj = runCatching { json.parseToJsonElement(data.toString()).jsonObject }.getOrNull()
                    data.clear()
                    if (obj != null) {
                        val id = obj["id"]?.jsonPrimitive?.contentOrNull
                        if (wantId == null || id == wantId) return obj
                    }
                }
            } else if (line.startsWith("data:")) {
                appendData(data, line)
            }
        }
        throw IOException("SSE response closed without a result")
    }

    // ------------------------------------------------------------- helpers

    /** SSE: an event's data lines are joined with newlines (a JSON reply may span several lines). */
    private fun appendData(data: StringBuilder, line: String) {
        if (data.isNotEmpty()) data.append('\n')
        data.append(line.removePrefix("data:").removePrefix(" "))
    }

    private fun rpc(method: String, params: JsonObject): JsonObject = buildJsonObject {
        put("jsonrpc", "2.0"); put("id", idSeq.getAndIncrement()); put("method", method); put("params", params)
    }

    private fun notify(method: String): JsonObject = buildJsonObject {
        put("jsonrpc", "2.0"); put("method", method); putJsonObject("params") {}
    }

    private fun initParams(): JsonObject = buildJsonObject {
        put("protocolVersion", "2025-03-26")
        putJsonObject("capabilities") {}
        putJsonObject("clientInfo") { put("name", "Altair"); put("version", com.localaiagent.app.BuildConfig.VERSION_NAME) }
    }

    private fun rpcError(el: JsonElement): String {
        val o = el.jsonObject
        val msg = o["message"]?.jsonPrimitive?.contentOrNull ?: "MCP error"
        val code = o["code"]?.jsonPrimitive?.contentOrNull
        return if (code != null) "$msg (code $code)" else msg
    }

    /** Joins the text of content[] from a tools/call result. */
    private fun renderContent(result: JsonObject): String {
        val sb = StringBuilder()
        result["content"]?.jsonArray?.forEach { el ->
            val o = el.jsonObject
            when (o["type"]?.jsonPrimitive?.contentOrNull) {
                "text" -> sb.append(o["text"]?.jsonPrimitive?.contentOrNull.orEmpty())
                "image" -> sb.append("[image ${o["mimeType"]?.jsonPrimitive?.contentOrNull.orEmpty()}]")
                "resource" -> sb.append(
                    o["resource"]?.jsonObject?.get("text")?.jsonPrimitive?.contentOrNull ?: "[resource]",
                )
                else -> {}
            }
            sb.append("\n")
        }
        return sb.toString().trim().ifBlank { result.toString().take(500) }
    }

    companion object {
        private val JSON_MEDIA = "application/json".toMediaType()

        /** One connection pool for all servers; a client per call used to leak a thread pool each. */
        private val HTTP: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(90, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()

        /** The legacy SSE stream stays open for the whole session, so it has no read timeout. */
        private val STREAM: OkHttpClient = HTTP.newBuilder().readTimeout(0, TimeUnit.MILLISECONDS).build()

        fun emptyObjectSchema(): JsonObject = buildJsonObject {
            put("type", "object"); putJsonObject("properties") {}
        }

        /** Executes the call off-thread and cancels it when the coroutine is cancelled (timeout, stop). */
        private suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
            cont.invokeOnCancellation { runCatching { cancel() } }
            enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (cont.isActive) cont.resumeWithException(e)
                }
                override fun onResponse(call: Call, response: Response) {
                    if (cont.isActive) cont.resume(response) else response.close()
                }
            })
        }
    }
}
