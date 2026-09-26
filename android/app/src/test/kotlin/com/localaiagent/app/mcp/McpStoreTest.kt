package com.localaiagent.app.mcp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class McpStoreTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun store() = McpStore(tmp.root, vault = false)

    @Test
    fun parsesClaudeDesktopCursorAndVsCodeShapes() {
        val desktop = """
            {"mcpServers": {
              "deepwiki": {"url": "https://mcp.deepwiki.com/mcp"},
              "notion": {"url": "https://mcp.notion.com/sse", "type": "sse", "token": "abc"},
              "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]},
              "off": {"serverUrl": "https://x.example/mcp", "disabled": true,
                      "headers": {"Authorization": "Bearer keep"}, "token": "ignored"}
            }}
        """.trimIndent()
        val p = McpConfig.parsePasted(desktop)
        assertEquals(listOf("deepwiki", "notion", "off"), p.servers.map { it.name })
        assertEquals("sse", p.servers[1].transport)
        assertEquals("Bearer abc", p.servers[1].headers["Authorization"])
        assertEquals("Bearer keep", p.servers[2].headers["Authorization"])
        assertFalse(p.servers[2].enabled)
        assertEquals(mapOf("files" to "local"), p.skipped)

        val vscode = McpConfig.parsePasted("""{"servers": {"My Server": {"type": "http", "url": "https://a.b/mcp"}}}""")
        assertEquals("My-Server", vscode.servers.single().name)

        val bare = McpConfig.parsePasted("""{"x": {"url": "ftp://nope"}}""")
        assertTrue(bare.servers.isEmpty())
        assertEquals("bad url", bare.skipped["x"])
    }

    @Test(expected = IllegalArgumentException::class)
    fun garbageIsRejected() {
        McpConfig.parsePasted("not json")
    }

    @Test
    fun masksSecretHeadersOnly() {
        assertEquals("Bearer …yz", McpConfig.maskHeader("Authorization", "Bearer abcdefghxyz"))
        assertEquals("••••", McpConfig.maskHeader("X-Api-Key", "short"))
        assertEquals("application/json", McpConfig.maskHeader("Accept", "application/json"))
    }

    @Test
    fun putRenamesInPlaceAndKeepsOrder() {
        val s = store()
        s.put(McpServer("a", "https://a"))
        s.put(McpServer("b", "https://b"))
        s.put(McpServer("a2", "https://a2"), previousName = "a")
        assertEquals(listOf("a2", "b"), s.load().map { it.name })
    }

    @Test
    fun syncKeepsPhoneServersAndUserChoices() {
        val s = store()
        s.put(McpServer("mine", "https://phone"))
        s.replaceFromPc(
            listOf(
                McpServer("mine", "https://pc-version"),
                McpServer("wiki", "https://wiki"),
                McpServer("gone", "https://gone"),
                McpServer("quiet", "https://quiet"),
            ),
        )
        assertEquals("https://phone", s.get("mine")!!.url)
        assertTrue(s.get("wiki")!!.fromPc)

        s.remove("gone")
        s.setEnabled("quiet", false)
        s.replaceFromPc(
            listOf(McpServer("wiki", "https://wiki2"), McpServer("gone", "https://gone"), McpServer("quiet", "https://q")),
        )
        assertNull("a PC server deleted on the phone must not come back", s.get("gone"))
        assertFalse("switched off on the phone stays off", s.get("quiet")!!.enabled)
        assertEquals("https://wiki2", s.get("wiki")!!.url)

        s.replaceFromPc(emptyList())
        assertEquals("PC servers removed on the PC disappear", listOf("mine"), s.load().map { it.name })

        // Adding it by hand lifts the dismissal.
        s.put(McpServer("gone", "https://mine-now"))
        assertEquals("https://mine-now", s.get("gone")!!.url)
    }

    @Test
    fun readsTheOldBareArrayFormat() {
        File(tmp.root, "mcp_servers.json").writeText(
            """[{"name":"old","url":"https://old","transport":"sse","enabled":false,"fromPc":true,"headers":{}}]""",
        )
        val old = store().get("old")!!
        assertEquals("sse", old.transport)
        assertFalse(old.enabled)
        assertTrue(old.fromPc)
    }
}
