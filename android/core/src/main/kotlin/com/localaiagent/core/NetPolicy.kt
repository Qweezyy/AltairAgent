package com.localaiagent.core

import java.net.URI

/**
 * Where credentials may travel without TLS.
 *
 * The manifest has to allow cleartext traffic globally (the PC bridge usually runs on plain HTTP in
 * the LAN or over Tailscale, and Android cannot scope cleartext to private IP ranges declaratively).
 * So the rule is enforced in code instead: an API key, bridge token or MCP bearer token is only
 * sent over plain http:// to a private/local address. Public hosts need https.
 */
object NetPolicy {
    /** True if a request carrying credentials to [url] is acceptable. */
    fun credentialsAllowed(url: String): Boolean {
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return false
        return when (uri.scheme?.lowercase()) {
            "https", "wss" -> true
            "http", "ws" -> isPrivateHost(uri.host ?: return false)
            else -> false
        }
    }

    /** Loopback, RFC 1918, link-local, CGNAT (Tailscale 100.64/10), IPv6 ULA/link-local, mDNS. */
    fun isPrivateHost(rawHost: String): Boolean {
        val host = rawHost.trim().trim('[', ']').lowercase()
        if (host == "localhost" || host.endsWith(".local") || host.endsWith(".localhost")) return true
        if (host.contains(':')) {
            return host == "::1" || host.startsWith("fc") || host.startsWith("fd") || host.startsWith("fe80")
        }
        val o = host.split('.')
        if (o.size != 4) return false
        val n = o.map { it.toIntOrNull() ?: return false }
        if (n.any { it !in 0..255 }) return false
        return when {
            n[0] == 10 || n[0] == 127 -> true
            n[0] == 172 && n[1] in 16..31 -> true
            n[0] == 192 && n[1] == 168 -> true
            n[0] == 169 && n[1] == 254 -> true
            n[0] == 100 && n[1] in 64..127 -> true
            else -> false
        }
    }
}
