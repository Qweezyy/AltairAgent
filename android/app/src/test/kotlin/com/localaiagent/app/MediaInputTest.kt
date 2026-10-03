package com.localaiagent.app

import org.junit.Assert.assertEquals
import org.junit.Test

/** Which attachments go to the model itself and which only by path. */
class MediaInputTest {

    private fun item(name: String, kind: String) = LibraryItem("/chat/attachments/$name", name, kind)
    private val photo = item("cat.jpg", "image")
    private val video = item("clip.mp4", "video")
    private val audio = item("voice.m4a", "audio")
    private val pdf = item("paper.pdf", "file")
    private val csv = item("data.csv", "file")
    private val all = listOf(photo, video, audio, pdf, csv)

    private fun plan(caps: Set<String>, size: Long = 1_000) = MediaInput.plan(all, caps) { size }

    @Test
    fun aModelWithVideoAudioAndFilesTakesThemIn() {
        val p = plan(setOf("image", "video", "audio", "file"))
        assertEquals(listOf(photo, video, audio, pdf), p.inline)
        // A table is read by the tools better than by the model's eyes.
        assertEquals(listOf(csv), p.byPath)
    }

    @Test
    fun aTextAndPhotoModelGetsPathsForTheRest() {
        val p = plan(setOf("image"))
        assertEquals(listOf(photo), p.inline)
        assertEquals(listOf(video, audio, pdf, csv), p.byPath)
    }

    @Test
    fun overTheBudgetTheRestGoesByPathWithTheReason() {
        val half = MediaInput.INLINE_BUDGET_BYTES / 2 + 1
        val p = MediaInput.plan(listOf(photo, video, audio), setOf("video", "audio")) { half }
        assertEquals(listOf(photo, video), p.inline)
        assertEquals(listOf(audio), p.tooLarge)
    }

    @Test
    fun mimeTypes() {
        assertEquals("video/mp4", MediaInput.mimeOf("a.MP4"))
        assertEquals("audio/mp3", MediaInput.mimeOf("a.mp3"))
        assertEquals("audio/aac", MediaInput.mimeOf("a.m4a"))
        assertEquals("application/pdf", MediaInput.mimeOf("a.pdf"))
    }
}
