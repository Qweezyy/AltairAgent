package com.localaiagent.app.bridge

import com.localaiagent.app.bridge.BridgeDiagnostics.Problem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BridgeDiagnosticsTest {
    @Test
    fun extractsHostFromUrls() {
        assertEquals("192.168.8.40", BridgeDiagnostics.host("http://192.168.8.40:8137"))
        assertEquals("pc.local", BridgeDiagnostics.host("https://user@pc.local/api"))
        assertEquals("10.0.0.2", BridgeDiagnostics.host("10.0.0.2:8137"))
        assertEquals("[fd00::1]", BridgeDiagnostics.host("http://[fd00::1]:8137"))
    }

    @Test
    fun flagsTypoInIpButNotValidAddresses() {
        assertEquals("192.268.8.40", BridgeDiagnostics.invalidIp("http://192.268.8.40:8137"))
        assertNull(BridgeDiagnostics.invalidIp("http://192.168.8.40:8137"))
        assertNull(BridgeDiagnostics.invalidIp("http://100.102.19.29:8137"))
        assertNull(BridgeDiagnostics.invalidIp("http://my-pc.tailnet.ts.net:8137"))
    }

    @Test
    fun classifiesRealAndroidErrors() {
        assertEquals(
            Problem.UNREACHABLE,
            BridgeDiagnostics.classify(
                "failed to connect to /192.168.8.40 (port 8137) from /10.2.101.91 (port 36090) after 10000ms",
            ),
        )
        assertEquals(Problem.REFUSED, BridgeDiagnostics.classify("failed to connect to /192.168.8.40: ECONNREFUSED (Connection refused)"))
        assertEquals(Problem.UNKNOWN_HOST, BridgeDiagnostics.classify("Unable to resolve host \"pc.local\": No address associated with hostname"))
        assertNull(BridgeDiagnostics.classify("HTTP 401"))
    }
}
