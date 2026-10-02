package com.localaiagent.app.data

import com.localaiagent.app.ChatMessage
import com.localaiagent.app.LibraryItem
import com.localaiagent.app.userAttachments
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class ChatStoreAttachmentsTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test
    fun manyAttachmentsOfMixedKindsSurviveSaveAndLoad() {
        val atts = (1..15).map { i ->
            val kind = listOf("image", "video", "audio", "file")[i % 4]
            LibraryItem("/data/chats/c1/attachments/f$i", "f$i", kind)
        }
        val dir = File(tmp.root, "chats/c1")
        ChatStore.save(dir, "c1", 1L, listOf(ChatMessage(true, "look", attachments = atts), ChatMessage(false, "ok")))
        val back = ChatStore.loadAll(File(tmp.root, "chats")).single().messages
        assertEquals(atts, back[0].attachments)
        assertEquals(atts, back[0].userAttachments)
        assertEquals("look", back[0].text)
    }

    @Test
    fun chatsFromOlderBuildsKeepTheirSingleAttachment() {
        val photo = ChatMessage(true, "", imageUrl = "/x/a.jpg")
        assertEquals(listOf(LibraryItem("/x/a.jpg", "a.jpg", "image")), photo.userAttachments)
        val file = ChatMessage(true, "", attachPath = "/x/report.pdf", attachName = "report.pdf", attachKind = "file")
        assertEquals(listOf(LibraryItem("/x/report.pdf", "report.pdf", "file")), file.userAttachments)
    }
}
