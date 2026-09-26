package com.localaiagent.core

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NetPolicyTest {
    @Test
    fun httpsAlwaysAllowed() {
        assertTrue(NetPolicy.credentialsAllowed("https://api.openai.com/v1"))
        assertTrue(NetPolicy.credentialsAllowed("wss://example.com/ws"))
    }

    @Test
    fun plainHttpOnlyToPrivateHosts() {
        listOf(
            "http://192.168.1.20:8000", "http://10.0.0.5/ws", "http://172.20.3.4:8000",
            "http://127.0.0.1:11434/v1", "http://localhost:8000", "http://100.102.19.29:8000",
            "http://my-pc.local:8000", "ws://[::1]:8000/ws", "http://[fd12::1]:8000",
        ).forEach { assertTrue(it, NetPolicy.credentialsAllowed(it)) }
    }

    @Test
    fun plainHttpToPublicHostsRefused() {
        listOf(
            "http://api.openai.com/v1", "http://8.8.8.8", "http://172.32.0.1", "http://100.128.0.1",
            "http://192.169.1.1", "ws://evil.example/ws",
        ).forEach { assertFalse(it, NetPolicy.credentialsAllowed(it)) }
    }

    @Test
    fun garbageRefused() {
        assertFalse(NetPolicy.credentialsAllowed(""))
        assertFalse(NetPolicy.credentialsAllowed("ftp://192.168.1.1"))
        assertFalse(NetPolicy.credentialsAllowed("not a url"))
        assertFalse(NetPolicy.isPrivateHost("300.1.1.1"))
    }
}
