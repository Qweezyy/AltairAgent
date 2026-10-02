package com.localaiagent.app

import org.junit.Assert.assertEquals
import org.junit.Test

class FollowupsTest {
    @Test
    fun standardLineAtTheEnd() {
        val (clean, opts) = Followups.extract("Answer text.\n\n?>> Yes, go on || Explain || Another option")
        assertEquals("Answer text.", clean)
        assertEquals(listOf("Yes, go on", "Explain", "Another option"), opts)
    }

    @Test
    fun looseFormatsModelsActuallySend() {
        assertEquals(listOf("Да", "Нет"), Followups.extract("x\n!>> Да || Нет").second)
        assertEquals(listOf("A", "B"), Followups.extract("x\n? >> A | B").second)
        assertEquals(listOf("A", "B"), Followups.extract("x\n`?>> A || B`").second)
        assertEquals(listOf("A", "B"), Followups.extract("x\n**?>> A || B**").second)
        assertEquals(listOf("A", "B"), Followups.extract("x\n- ?>> «A» || \"B\"").second)
    }

    @Test
    fun textAfterTheLineIsKeptInTheAnswer() {
        val (clean, opts) = Followups.extract("Intro\n?>> One || Two\nP.S. note")
        assertEquals("Intro\nP.S. note", clean)
        assertEquals(listOf("One", "Two"), opts)
    }

    @Test
    fun markerInsideCodeIsCode() {
        val text = "Use it like this:\n```\n?>> a || b\n```"
        assertEquals(text to emptyList<String>(), Followups.extract(text))
    }

    @Test
    fun noLineNoOptions() {
        assertEquals("Plain" to emptyList<String>(), Followups.extract("Plain"))
        assertEquals(emptyList<String>(), Followups.extract("Is it 3 >> 2? Yes").second)
    }

    @Test
    fun atMostThreeShortDistinctOptions() {
        val long = "x".repeat(100)
        val opts = Followups.extract("t\n?>> a || a || b || $long || c || d").second
        assertEquals(listOf("a", "b", "c"), opts)
    }

    @Test
    fun halfWrittenLineIsHiddenWhileStreaming() {
        assertEquals("Answer", Followups.hideWhileStreaming("Answer\n?>"))
        assertEquals("Answer", Followups.hideWhileStreaming("Answer\n?>> Yes || Ex"))
        assertEquals("Answer\nmore", Followups.hideWhileStreaming("Answer\nmore"))
        assertEquals("Is it?", Followups.hideWhileStreaming("Is it?"))
    }
}
