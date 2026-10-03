package com.localaiagent.app.data

import com.localaiagent.app.ChatMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** Message times survive a save, and chats from older builds still get a last-activity time. */
class ChatStoreTimeTest {

    private val root: File = Files.createTempDirectory("chats").toFile()

    @Test
    fun messageTimesAreSavedAndLoaded() {
        val msgs = listOf(ChatMessage(true, "hi", time = 1_000L), ChatMessage(false, "hello", time = 2_000L))
        ChatStore.save(File(root, "a"), "a", 500L, msgs)
        val loaded = ChatStore.loadAll(root).single()
        assertEquals(listOf(1_000L, 2_000L), loaded.messages.map { it.time })
        // With real times, the file date is not needed.
        assertEquals(0L, loaded.savedAt)
    }

    @Test
    fun chatsWithoutTimesFallBackToTheFileDate() {
        val dir = File(root, "old").apply { mkdirs() }
        File(dir, "session.json").writeText("""{"id":"old","created":1,"messages":[{"u":true,"t":"legacy"}]}""")
        val loaded = ChatStore.loadAll(root).single()
        assertEquals(0L, loaded.messages.single().time)
        assertTrue(loaded.savedAt > 0)
    }
}
