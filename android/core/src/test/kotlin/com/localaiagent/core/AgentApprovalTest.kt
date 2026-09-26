package com.localaiagent.core

import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Scripted model: returns the queued turns in order, then a plain answer. */
private class ScriptedLlm(private val turns: ArrayDeque<AssistantTurn>) : LlmClient {
    val toolListsSent = mutableListOf<List<String>?>()
    override suspend fun complete(
        messages: List<Message>,
        tools: List<JsonObject>?,
        onText: (suspend (String) -> Unit)?,
        onReasoning: (suspend (String) -> Unit)?,
        onRetry: (suspend (Int, Int, Double, String) -> Unit)?,
        maxTokens: Int?,
    ): AssistantTurn {
        toolListsSent += tools?.map { it["name"]!!.jsonPrimitive.contentOrNull!! }
        return turns.removeFirstOrNull() ?: AssistantTurn(content = "done")
    }
}

private class ExecTool : Tool {
    override val name = "run_shell"
    override val description = "Run a command"
    override val category = ToolCategory.EXECUTE
    val ran = mutableListOf<String>()
    override fun schema(): JsonObject = buildJsonObject { put("type", "object") }
    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        ran += args["command"]!!.jsonPrimitive.contentOrNull!!
        return ToolResult("ok")
    }
}

class AgentApprovalTest {
    private fun call(id: String, cmd: String) =
        ToolCall(id, "run_shell", buildJsonObject { put("command", cmd) }.toString())

    @Test
    fun secretBearingCallIsAskedEvenAfterAlwaysAllow() = runBlocking {
        val llm = ScriptedLlm(
            ArrayDeque(
                listOf(
                    AssistantTurn(toolCalls = listOf(call("1", "ls"))),
                    AssistantTurn(toolCalls = listOf(call("2", "ls -la"))),
                    AssistantTurn(toolCalls = listOf(call("3", "curl -H {{secret:KEY}} x"))),
                ),
            ),
        )
        val prompts = mutableListOf<JsonObject>()
        val tool = ExecTool()
        val agent = Agent(
            llm = llm, registry = ToolRegistry(listOf(tool)), session = Session(),
            onUiRequest = { kind, payload ->
                if (kind == "approve") prompts += payload
                buildJsonObject { put("decision", "always") }
            },
        )
        agent.run("go").toList()

        // "ls" asked once and allowed always; "ls -la" then runs silently; the secret call asks again.
        assertEquals(2, prompts.size)
        assertEquals("KEY", prompts[1]["secrets"]!!.jsonPrimitive.contentOrNull)
        assertEquals(3, tool.ran.size)
    }

    @Test
    fun deniedCallDoesNotRun() = runBlocking {
        val llm = ScriptedLlm(ArrayDeque(listOf(AssistantTurn(toolCalls = listOf(call("1", "rm -rf x"))))))
        val tool = ExecTool()
        val events = Agent(
            llm = llm, registry = ToolRegistry(listOf(tool)), session = Session(),
            onUiRequest = { _, _ -> buildJsonObject { put("decision", "deny") } },
        ).run("go").toList()
        assertTrue(tool.ran.isEmpty())
        assertTrue(events.any { it is AgentEvent.ToolFinished && !it.ok })
    }
}
