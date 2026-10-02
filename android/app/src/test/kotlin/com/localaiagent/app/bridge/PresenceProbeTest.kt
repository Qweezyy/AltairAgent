package com.localaiagent.app.bridge

import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PresenceProbeTest {
    @Test
    fun successIsOnline() = runBlocking {
        // The regression: success is null, and must not be read as a timeout.
        assertTrue(PresenceProbe.isOnline(1_000) { null })
    }

    @Test
    fun errorIsOffline() = runBlocking {
        assertFalse(PresenceProbe.isOnline(1_000) { "failed to connect" })
    }

    @Test
    fun timeoutIsOffline() = runBlocking {
        assertFalse(PresenceProbe.isOnline(50) { delay(5_000); null })
    }

    @Test
    fun exceptionIsOffline() = runBlocking {
        assertFalse(PresenceProbe.isOnline(1_000) { throw java.io.IOException("refused") })
    }
}
