package com.localaiagent.llm

import java.io.BufferedInputStream
import java.io.Closeable
import java.net.ServerSocket
import java.net.Socket
import java.util.Collections
import kotlin.concurrent.thread

/**
 * A real OpenAI-style SSE server on a local socket, scripted reply by reply: chunks, an HTTP error,
 * a late first byte, slow chunks, or a stream that just breaks off.
 */
class FakeSse : Closeable {

    data class Reply(
        val chunks: List<String> = emptyList(),
        /** Ends with [DONE]; false = the connection drops after the chunks. */
        val complete: Boolean = true,
        val status: Int = 200,
        val errorBody: String = "",
        val firstByteDelayMs: Long = 0,
        val chunkDelayMs: Long = 0,
    )

    private val server = ServerSocket(0)
    val requests: MutableList<String> = Collections.synchronizedList(mutableListOf())
    val baseUrl get() = "http://127.0.0.1:${server.localPort}/v1"

    fun serve(vararg replies: Reply) {
        thread(isDaemon = true) {
            for (reply in replies) {
                val socket = runCatching { server.accept() }.getOrNull() ?: return@thread
                // Each connection on its own thread, so a slow reply does not hold up the next one.
                thread(isDaemon = true) { runCatching { socket.use { answer(it, reply) } } }
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
        if (reply.firstByteDelayMs > 0) Thread.sleep(reply.firstByteDelayMs)
        val out = socket.getOutputStream()
        if (reply.status != 200) {
            val b = reply.errorBody.toByteArray()
            out.write("HTTP/1.1 ${reply.status} Error\r\nContent-Length: ${b.size}\r\nConnection: close\r\n\r\n".toByteArray())
            out.write(b); out.flush(); return
        }
        out.write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n".toByteArray())
        for (chunk in reply.chunks) {
            out.write("data: $chunk\n\n".toByteArray()); out.flush()
            if (reply.chunkDelayMs > 0) Thread.sleep(reply.chunkDelayMs)
        }
        if (reply.complete) out.write("data: [DONE]\n\n".toByteArray())
        out.flush()
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

    override fun close() = server.close()

    companion object {
        fun text(s: String, finish: String? = null): String {
            val fin = if (finish == null) "null" else "\"$finish\""
            val esc = s.replace("\\", "\\\\").replace("\"", "\\\"")
            return """{"choices":[{"delta":{"content":"$esc"},"finish_reason":$fin}]}"""
        }

        /** A complete reply with [s] as the whole answer. */
        fun answer(s: String) = Reply(listOf(text(s), text("", "stop")))
    }
}
