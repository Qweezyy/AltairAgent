package com.localaiagent.core.tools

import com.localaiagent.core.Message
import com.localaiagent.core.Role
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCall
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolRegistry
import com.localaiagent.core.ToolResult
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeTool(override val name: String, override val description: String) : Tool {
    override val category = ToolCategory.READ
    override fun schema(): JsonObject = buildJsonObject { put("type", "object") }
    override suspend fun run(args: JsonObject, ctx: ToolContext) = ToolResult("ok")
}

private class FakeCtx(override val registry: ToolRegistry) : ToolContext {
    override val workspaceDir = "."
    override val scratch = mutableMapOf<String, Any?>()
    override suspend fun approve(name: String, reason: String, args: JsonObject) = true
    override suspend fun emit(event: com.localaiagent.core.AgentEvent) {}
}

class DeferredToolsTest {
    private fun registry(extra: Int = 25): ToolRegistry {
        val tools = mutableListOf<Tool>(ToolSearchTool(), FakeTool("web_search", "Search the web"))
        tools += FakeTool("set_reminder", "Set a timer or reminder for a given time")
        tools += FakeTool("watch_condition", "Notify when a battery or network condition holds")
        repeat(extra) { tools += FakeTool("filler_$it", "Unrelated helper number $it") }
        return ToolRegistry(tools)
    }

    @Test
    fun smallRegistryIsSentWhole() {
        val small = ToolRegistry(listOf(ToolSearchTool(), FakeTool("a", "x")))
        assertNull(DeferredTools.activeNames(small, emptyList(), emptyMap()))
        assertTrue(DeferredTools.deferredNames(small).isEmpty())
    }

    @Test
    fun onlyCoreToolsGoUpFront() {
        val active = DeferredTools.activeNames(registry(), emptyList(), emptyMap())!!
        assertTrue("tool_search" in active)
        assertTrue("web_search" in active)
        assertFalse("set_reminder" in active)
        assertTrue("set_reminder" in DeferredTools.deferredNames(registry()))
    }

    @Test
    fun toolCalledEarlierInTheChatStaysActive() {
        val history = listOf(Message(Role.ASSISTANT, "", toolCalls = listOf(ToolCall("1", "set_reminder"))))
        val active = DeferredTools.activeNames(registry(), history, emptyMap())!!
        assertTrue("set_reminder" in active)
    }

    @Test
    fun rankingFindsToolByKeywordAndPrefix() {
        val found = DeferredTools.rank(registry().all(), "remind", 3)
        assertEquals("set_reminder", found.first().name)
    }

    @Test
    fun toolSearchLoadsMatchesIntoTheRun() = runBlocking {
        val reg = registry()
        val ctx = FakeCtx(reg)
        val res = ToolSearchTool().run(buildJsonObject { put("query", "battery condition") }, ctx)
        assertTrue(res.ok)
        val active = DeferredTools.activeNames(reg, emptyList(), ctx.scratch)!!
        assertTrue("watch_condition" in active)
    }

    @Test
    fun selectLoadsExactNamesAndReportsUnknown() = runBlocking {
        val reg = registry()
        val ctx = FakeCtx(reg)
        val res = ToolSearchTool().run(buildJsonObject { put("query", "select:set_reminder,nope") }, ctx)
        assertTrue(res.ok)
        assertTrue(res.content.contains("Not found: nope"))
        assertTrue("set_reminder" in DeferredTools.activeNames(reg, emptyList(), ctx.scratch)!!)
    }

    @Test
    fun noMatchIsAnError() = runBlocking {
        val res = ToolSearchTool().run(buildJsonObject { put("query", "zzzqqq") }, FakeCtx(registry()))
        assertFalse(res.ok)
    }
}
