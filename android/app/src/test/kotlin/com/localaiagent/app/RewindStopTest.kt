package com.localaiagent.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Rewinding, stopping an answer and answering a message anew. */
class RewindStopTest {

    private fun user(text: String, reaction: String? = null) = ChatMessage(true, text, reaction = reaction)
    private fun ai(text: String) = ChatMessage(false, text)

    private val chat = listOf(user("hi", "😄"), ai("hello"), user("how are you?", "🙂"), ai("fine"))

    @Test
    fun rewindToOwnMessageTakesItBackWithItsReaction() {
        val kept = keptOnRevert(chat, 2)
        assertEquals(listOf("hi", "hello"), kept.map { it.text })
        // The reaction on the taken-back message left with it; earlier ones stay.
        assertEquals("😄", kept[0].reaction)
    }

    @Test
    fun rewindToAnAnswerKeepsTheAnswer() {
        assertEquals(listOf("hi", "hello"), keptOnRevert(chat, 1).map { it.text })
        assertEquals(chat, keptOnRevert(chat, 3))
    }

    @Test
    fun stoppingDropsThePartialAnswerAndItsReaction() {
        val running = chat + user("tell me more", "🤔") + ai("Once upon a ti") + ai("")
        val kept = keptAfterStop(running)!!
        assertEquals(chat.map { it.text } + "tell me more", kept.map { it.text })
        assertNull(kept.last().reaction)
        // Earlier messages keep their reactions.
        assertEquals("🙂", kept[2].reaction)
    }

    @Test
    fun stoppingWithNoUserMessageChangesNothing() {
        assertNull(keptAfterStop(listOf(ai("welcome"))))
    }

    @Test
    fun regenerateIsOfferedOnlyOnAnUnansweredOwnMessage() {
        val stopped = keptAfterStop(chat + user("again") + ai("par"))!!
        assertTrue(canRegenerateUserMessage(stopped, stopped.lastIndex))
        assertFalse(canRegenerateUserMessage(chat, 2))
        assertFalse(canRegenerateUserMessage(chat, 3))
        assertFalse(canRegenerateUserMessage(chat, 99))
    }
}
