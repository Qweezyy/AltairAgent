package com.localaiagent.llm

import com.localaiagent.core.LlmConfig
import com.localaiagent.core.Message
import com.localaiagent.core.Role
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedInputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.Collections
import kotlin.concurrent.thread

/**
 * A real OpenAI-style SSE server on a local socket that cuts the stream in the middle of an answer,
 * the way a flaky mobile connection does. The client has to continue the same answer, not restart it.
 */
class StreamContinuationTest {

    /** One scripted reply per connection: SSE data lines, and whether the stream ends properly. */
    private class Reply(val chunks: List<String>, val complete: Boolean)

    private val server = ServerSocket(0)
    private val requests: MutableList<String> = Collections.synchronizedList(mutableListOf())

    @After
    fun tearDown() {
        server.close()
    }

    private fun serve(vararg replies: Reply) {
        thread(isDaemon = true) {
            for (reply in replies) {
                val socket = runCatching { server.accept() }.getOrNull() ?: return@thread
                socket.use { answer(it, reply) }
            }
        }
    }

    private fun answer(socket: Socket, reply: Reply) {
        val input = BufferedInputStream(socket.getInputStream())
        var length = 0
        while (true) {
            val line = readLine(input)
            if (line.isEmpty()) break
            if (line.lowercase().startsWith("content-length:")) length = line.substringAfter(':').trim().toInt()
        }
        val body = ByteArray(length)
        var read = 0
        while (read < length) {
            val n = input.read(body, read, length - read)
            if (n < 0) break
            read += n
        }
        requests += String(body, Charsets.UTF_8)
        val out = socket.getOutputStream()
        out.write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n".toByteArray())
        for (chunk in reply.chunks) out.write("data: $chunk\n\n".toByteArray())
        if (reply.complete) out.write("data: [DONE]\n\n".toByteArray())
        out.flush()
        // Closing here without [DONE] is the broken connection.
    }

    private fun readLine(input: BufferedInputStream): String {
        val sb = StringBuilder()
        while (true) {
            val c = input.read()
            if (c < 0 || c == '\n'.code) break
            if (c != '\r'.code) sb.append(c.toChar())
        }
        return sb.toString()
    }

    private fun text(s: String, finish: String? = null): String {
        val fin = if (finish == null) "null" else "\"$finish\""
        return """{"choices":[{"delta":{"content":"$s"},"finish_reason":$fin}]}"""
    }

    private fun client() = OpenAiCompatClient(
        LlmConfig(baseUrl = "http://127.0.0.1:${server.localPort}/v1", model = "m", apiKey = "k", maxRetries = 3),
    )

    @Test
    fun aBrokenAnswerIsContinuedNotRestarted() = runBlocking {
        serve(
            Reply(listOf(text("Hello, "), text("wor")), complete = false),
            Reply(listOf(text("ld!"), text("", finish = "stop")), complete = true),
        )
        val streamed = StringBuilder()
        var retries = 0
        var discarded = 0
        val llm = client()
        val turn = llm.complete(
            messages = listOf(Message(Role.USER, "Say hello")),
            onText = { streamed.append(it) },
            onRetry = { _, _, _, _ -> retries++ },
            onDiscard = { discarded += it },
        )
        llm.close()

        assertEquals("Hello, world!", turn.content)
        // The user saw every piece once, in order: nothing was repeated or taken back.
        assertEquals("Hello, world!", streamed.toString())
        assertEquals(1, retries)
        assertEquals(0, discarded)
        assertEquals(2, requests.size)
        // The second request hands the model its own partial answer and asks it to go on.
        val second = requests[1]
        assertTrue(second, second.contains("\"Hello, wor\""))
        assertTrue(second, second.contains("Continue it exactly from where it stopped"))
        assertFalse(requests[0].contains("Continue it exactly"))
    }

    @Test
    fun aBrokenToolCallStartsTheAnswerOver() = runBlocking {
        val partialCall = """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",""" +
            """"function":{"name":"read_file","arguments":"{\"pa"}}]},"finish_reason":null}]}"""
        serve(
            Reply(listOf(text("Let me check"), partialCall), complete = false),
            Reply(listOf(text("Done.", finish = "stop")), complete = true),
        )
        var discarded = 0
        val llm = client()
        val turn = llm.complete(
            messages = listOf(Message(Role.USER, "Check the file")),
            onDiscard = { discarded += it },
        )
        llm.close()

        assertEquals("Done.", turn.content)
        assertTrue(turn.toolCalls.isEmpty())
        assertEquals("Let me check".length, discarded)
        assertFalse(requests[1].contains("Continue it exactly"))
    }

    @Test
    fun aStreamThatEndsProperlyIsNotRetried() = runBlocking {
        serve(Reply(listOf(text("All good.", finish = "stop")), complete = true))
        var retries = 0
        val llm = client()
        val turn = llm.complete(
            messages = listOf(Message(Role.USER, "Hi")),
            onRetry = { _, _, _, _ -> retries++ },
        )
        llm.close()
        assertEquals("All good.", turn.content)
        assertEquals(0, retries)
        assertEquals(1, requests.size)
    }
}
