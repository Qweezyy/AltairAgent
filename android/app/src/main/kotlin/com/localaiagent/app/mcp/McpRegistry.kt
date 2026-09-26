package com.localaiagent.app.mcp

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import com.localaiagent.core.Verdict
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonObject

/**
 * A remote MCP tool wrapped as our [Tool]. EXECUTE, and it always asks: an external server performs
 * the action, the same as run_shell/run_python. The name carries the server as a prefix so it cannot
 * collide with built-in tools.
 */
class McpTool(
    private val server: McpServer,
    private val spec: McpToolSpec,
    override val name: String,
) : Tool {
    override val description: String =
        spec.description.ifBlank { "MCP server tool" } + " [MCP: ${server.name}]"
    override val category = ToolCategory.EXECUTE
    override fun schema(): JsonObject = spec.inputSchema

    override fun autoVerdict(args: JsonObject, ctx: ToolContext): Verdict = Verdict.ASK

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val (ok, text) = try {
            withTimeout(McpRegistry.CALL_TIMEOUT_MS) { McpClient(server).callTool(spec.name, args) }
        } catch (e: TimeoutCancellationException) {
            return ToolResult.fail("MCP server '${server.name}' did not answer in ${McpRegistry.CALL_TIMEOUT_MS / 1000} s.")
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            return ToolResult.fail("MCP server '${server.name}': ${e.message ?: "connection failed"}")
        }
        return if (ok) ToolResult(text) else ToolResult.fail(text)
    }
}

/** Connection state of one server, localized by the UI. */
sealed interface McpStatus {
    data class Ok(val tools: Int) : McpStatus
    data class Failed(val reason: String) : McpStatus
}

/**
 * MCP tools of the enabled servers, cached and handed to the agent as ordinary [Tool]s. Refreshing
 * goes over the network in the background, so the agent always takes the last good [snapshot].
 */
class McpRegistry(private val store: McpStore) {

    @Volatile private var cache: List<McpTool> = emptyList()
    @Volatile var status: Map<String, McpStatus> = emptyMap()
        private set

    fun snapshot(): List<Tool> = cache

    /** Polls every enabled server in parallel; one slow or dead server cannot hold up the rest. */
    suspend fun refresh() {
        val servers = store.load().filter { it.enabled }
        val results = coroutineScope {
            servers.map { s -> async { s to probe(s) } }.awaitAll()
        }
        val tools = mutableListOf<McpTool>()
        val stat = linkedMapOf<String, McpStatus>()
        val usedNames = mutableSetOf<String>()
        for ((s, r) in results) {
            r.onSuccess { specs ->
                specs.forEach { spec -> tools += McpTool(s, spec, uniqueName(toolName(s, spec.name), usedNames)) }
                stat[s.name] = McpStatus.Ok(specs.size)
            }.onFailure { stat[s.name] = McpStatus.Failed(reason(it)) }
        }
        cache = tools
        status = stat
    }

    /** Checks one (maybe unsaved) server for the UI without touching the cache. */
    suspend fun test(server: McpServer): McpStatus =
        probe(server).fold({ McpStatus.Ok(it.size) }, { McpStatus.Failed(reason(it)) })

    private suspend fun probe(server: McpServer): Result<List<McpToolSpec>> = try {
        Result.success(withTimeout(LIST_TIMEOUT_MS) { McpClient(server).listTools() })
    } catch (e: TimeoutCancellationException) {
        Result.failure(java.io.IOException("no answer in ${LIST_TIMEOUT_MS / 1000} s"))
    } catch (e: kotlinx.coroutines.CancellationException) {
        throw e
    } catch (e: Exception) {
        Result.failure(e)
    }

    private fun reason(e: Throwable): String = (e.message ?: e.javaClass.simpleName).take(160)

    companion object {
        const val LIST_TIMEOUT_MS = 25_000L
        const val CALL_TIMEOUT_MS = 120_000L

        fun toolName(server: McpServer, tool: String): String =
            (sanitize(server.name) + "_" + sanitize(tool)).take(60)

        private fun uniqueName(base: String, used: MutableSet<String>): String {
            var name = base; var i = 2
            while (name in used) { name = (base.take(57) + "_" + i); i++ }
            used += name
            return name
        }

        private fun sanitize(x: String): String =
            x.trim().lowercase().replace(Regex("[^a-z0-9_]+"), "_").trim('_').ifBlank { "srv" }
    }
}
