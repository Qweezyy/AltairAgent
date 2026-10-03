package com.localaiagent.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The context window a user types when adding a model. */
class ContextWindowTest {

    @Test
    fun suffixesAndPlainNumbers() {
        assertEquals(128_000, parseContextWindow("128K"))
        assertEquals(128_000, parseContextWindow("128k"))
        assertEquals(1_000_000, parseContextWindow("1M"))
        assertEquals(1_500_000, parseContextWindow("1.5M"))
        assertEquals(1_500_000, parseContextWindow("1,5M"))
        assertEquals(200_000, parseContextWindow("200 000"))
        assertEquals(200_000, parseContextWindow(" 200000 "))
    }

    @Test
    fun nonsenseIsRefused() {
        assertNull(parseContextWindow(""))
        assertNull(parseContextWindow("big"))
        assertNull(parseContextWindow("12"))
        assertNull(parseContextWindow("999M"))
    }

    @Test
    fun formatsAsTheUserReadsIt() {
        assertEquals("128K", formatContextWindow(128_000))
        assertEquals("1M", formatContextWindow(1_000_000))
        assertEquals("1.5M", formatContextWindow(1_500_000))
    }
}
