package com.localaiagent.app.mcp

import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * A real MCP server on a local socket, speaking both transports, so the client is tested against the
 * wire protocol rather than mocks. (The JDK HttpServer is not on the Android unit-test classpath.)
 */
private class FakeMcpServer(
    private val sseReplies: Boolean = false,
    private val requiredAuth: String? = null,
) : AutoCloseable {
    private val socket = ServerSocket(0)
    val base = "http://127.0.0.1:${socket.localPort}"
    val seenSessionIds = mutableListOf<String?>()
    private val legacyStreams = java.util.concurrent.ConcurrentHashMap<String, LinkedBlockingQueue<String>>()
    private val sidSeq = java.util.concurrent.atomic.AtomicInteger()
    @Volatile private var open = true

    init {
        thread(isDaemon = true) {
            while (open) {
                val s = runCatching { socket.accept() }.getOrNull() ?: break
                thread(isDaemon = true) { runCatching { handle(s) } }
            }
        }
    }

    private class Req(val method: String, val path: String, val headers: Map<String, String>, val body: String)

    private fun read(s: Socket): Req {
        val r = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
        val (method, path) = r.readLine().split(" ").let { it[0] to it[1] }
        val headers = mutableMapOf<String, String>()
        while (true) {
            val l = r.readLine() ?: break
            if (l.isEmpty()) break
            headers[l.substringBefore(':').trim().lowercase()] = l.substringAfter(':').trim()
        }
        val len = headers["content-length"]?.toInt() ?: 0
        val buf = CharArray(len)
        var got = 0
        while (got < len) { val n = r.read(buf, got, len - got); if (n < 0) break; got += n }
        return Req(method, path, headers, String(buf, 0, got))
    }

    private fun respond(out: OutputStream, code: Int, body: String, type: String = "application/json", extra: String = "") {
        val bytes = body.toByteArray()
        out.write(
            ("HTTP/1.1 $code X\r\nContent-Type: $type\r\nContent-Length: ${bytes.size}\r\n$extra" +
                "Connection: close\r\n\r\n").toByteArray(),
        )
        out.write(bytes); out.flush()
    }

    /** Multi-line payloads go out as several data: lines, as the SSE spec requires. */
    private fun sseEvent(payload: String) = "event: message\n" + payload.lines().joinToString("") { "data: $it\n" } + "\n"

    private fun result(id: String, result: String) = """{"jsonrpc":"2.0","id":$id,"result":$result}"""

    private fun answer(msg: JsonObject): String? {
        val id = msg["id"]?.toString() ?: return null
        return when (msg["method"]?.jsonPrimitive?.contentOrNull) {
            "initialize" -> result(id, """{"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"fake"}}""")
            "tools/list" -> result(
                id,
                """{"tools":[{"name":"echo","description":"Echo text","inputSchema":{"type":"object",
                  "properties":{"text":{"type":"string"}},"required":["text"]}},{"name":"boom","description":"fails"}]}""",
            )
            "tools/call" -> {
                val p = msg["params"]!!.jsonObject
                if (p["name"]!!.jsonPrimitive.content == "boom") {
                    result(id, """{"isError":true,"content":[{"type":"text","text":"it broke"}]}""")
                } else {
                    val text = p["arguments"]!!.jsonObject["text"]!!.jsonPrimitive.content
                    result(id, """{"content":[{"type":"text","text":"echo: $text"}]}""")
                }
            }
            else -> """{"jsonrpc":"2.0","id":$id,"error":{"code":-32601,"message":"no such method"}}"""
        }
    }

    private fun handle(s: Socket) = s.use {
        val req = read(s)
        val out = s.getOutputStream()
        if (requiredAuth != null && req.headers["authorization"] != requiredAuth) {
            respond(out, 401, """{"error":"unauthorized"}"""); return
        }
        when {
            req.path == "/hang" -> Thread.sleep(60_000)
            req.method == "GET" && req.path == "/sse" -> {
                out.write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n".toByteArray())
                val sid = sidSeq.incrementAndGet().toString()
                val queue = LinkedBlockingQueue<String>().also { legacyStreams[sid] = it }
                out.write("event: endpoint\ndata: /messages?sid=$sid\n\n".toByteArray()); out.flush()
                while (open) {
                    val ev = queue.poll(200, TimeUnit.MILLISECONDS) ?: continue
                    out.write(sseEvent(ev).toByteArray()); out.flush()
                }
            }
            req.path.startsWith("/messages") -> {
                val sid = req.path.substringAfter("sid=")
                answer(Json.parseToJsonElement(req.body).jsonObject)?.let { legacyStreams[sid]?.put(it) }
                respond(out, 202, "")
            }
            req.path == "/mcp" -> {
                val msg = Json.parseToJsonElement(req.body).jsonObject
                val isInit = msg["method"]?.jsonPrimitive?.contentOrNull == "initialize"
                if (!isInit) synchronized(seenSessionIds) { seenSessionIds += req.headers["mcp-session-id"] }
                val reply = answer(msg)
                when {
                    reply == null -> respond(out, 202, "")
                    sseReplies -> respond(out, 200, sseEvent(reply), "text/event-stream")
                    else -> respond(out, 200, reply, extra = if (isInit) "Mcp-Session-Id: sess-42\r\n" else "")
                }
            }
            else -> respond(out, 404, "{}")
        }
    }

    override fun close() { open = false; socket.close() }
}

class McpClientTest {
    private val servers = mutableListOf<FakeMcpServer>()
    private fun server(sse: Boolean = false, auth: String? = null) = FakeMcpServer(sse, auth).also { servers += it }

    @After fun tearDown() = servers.forEach { it.close() }

    private val echoArgs = buildJsonObject { put("text", "hi") }

    @Test
    fun streamableHttpListsAndCallsToolsWithinOneSession() = runBlocking {
        val srv = server()
        val client = McpClient(McpServer("fake", "${srv.base}/mcp"))
        val tools = client.listTools()
        assertEquals(listOf("echo", "boom"), tools.map { it.name })
        assertEquals("object", tools[1].inputSchema["type"]!!.jsonPrimitive.content)
        assertEquals(true to "echo: hi", client.callTool("echo", echoArgs))
        assertEquals(false to "it broke", client.callTool("boom", JsonObject(emptyMap())))
        assertTrue("session id is echoed back", srv.seenSessionIds.all { it == "sess-42" })
    }

    @Test
    fun sseEncodedRepliesAreRead() = runBlocking {
        val srv = server(sse = true)
        val client = McpClient(McpServer("fake", "${srv.base}/mcp"))
        assertEquals("multi-line data lines are joined", 2, client.listTools().size)
        assertEquals(true to "echo: hi", client.callTool("echo", echoArgs))
    }

    @Test
    fun legacySseTransportWorks() = runBlocking {
        val srv = server()
        val client = McpClient(McpServer("old", "${srv.base}/sse", transport = "sse"))
        assertEquals(2, client.listTools().size)
        assertEquals(true to "echo: hi", client.callTool("echo", echoArgs))
    }

    @Test
    fun bearerTokenIsSentAndMissingOneFailsClearly() = runBlocking {
        val srv = server(auth = "Bearer t0k")
        val ok = McpServer("a", "${srv.base}/mcp", headers = mapOf("Authorization" to "Bearer t0k"))
        assertEquals(2, McpClient(ok).listTools().size)
        try {
            McpClient(McpServer("a", "${srv.base}/mcp")).listTools()
            fail("expected 401")
        } catch (e: java.io.IOException) {
            assertTrue(e.message!!.contains("401"))
        }
    }

    @Test
    fun tokenIsNeverSentOverPlainHttpToAPublicHost() = runBlocking {
        val srv = McpServer("pub", "http://example.com/mcp", headers = mapOf("Authorization" to "Bearer x"))
        try {
            McpClient(srv).listTools()
            fail("expected a refusal")
        } catch (e: java.io.IOException) {
            assertTrue(e.message!!.contains("unencrypted"))
        }
    }

    @Test
    fun hangingServerIsCancelledPromptly() = runBlocking {
        val srv = server()
        val started = System.nanoTime()
        try {
            withTimeout(1_000) { McpClient(McpServer("slow", "${srv.base}/hang")).listTools() }
            fail("expected a timeout")
        } catch (_: TimeoutCancellationException) {
        }
        val ms = (System.nanoTime() - started) / 1_000_000
        assertTrue("cancellation must not wait for the socket (took $ms ms)", ms < 5_000)
    }

    @Test
    fun registryReportsEachServerAndPrefixesToolNames() = runBlocking {
        val good = server()
        val store = McpStore(kotlin.io.path.createTempDirectory().toFile(), vault = false)
        store.put(McpServer("good", "${good.base}/mcp"))
        store.put(McpServer("dead", "http://127.0.0.1:1/mcp"))
        store.put(McpServer("off", "${good.base}/mcp", enabled = false))
        val reg = McpRegistry(store)
        reg.refresh()
        assertEquals(McpStatus.Ok(2), reg.status["good"])
        assertTrue(reg.status["dead"] is McpStatus.Failed)
        assertFalse("disabled servers are not polled", "off" in reg.status)
        assertEquals(listOf("good_echo", "good_boom"), reg.snapshot().map { it.name })
    }
}
