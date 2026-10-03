package com.localaiagent.core

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The signs that make the next step think harder (adaptive reasoning effort). */
class LooksFailedTest {

    @Test
    fun aFailedToolIsAFailure() = assertTrue(looksFailed(ok = false, content = "fine"))

    @Test
    fun failingOutputIsAFailure() {
        listOf(
            "===== 2 failed, 13 passed in 0.4s =====",
            "test_parse FAILED",
            "Traceback (most recent call last):\n  File \"a.py\"",
            "E   AssertionError: 3 != 4",
            "  SyntaxError: invalid syntax",
            "Process finished, exit code 1",
            "ValueError: bad input",
        ).forEach { assertTrue(it, looksFailed(ok = true, content = it)) }
    }

    @Test
    fun cleanOutputIsNot() {
        listOf(
            "15 passed in 0.3s",
            "0 failed",
            "exit code 0",
            "The file was saved.",
            "error handling is described in chapter 3",
        ).forEach { assertFalse(it, looksFailed(ok = true, content = it)) }
    }
}
