package com.localaiagent.app.bridge

/**
 * Turns bridge address mistakes and connection errors into something the user can act on. Pure logic,
 * so it is unit-tested; the view model maps [Problem] to localized strings.
 */
object BridgeDiagnostics {
    enum class Problem { BAD_IP, UNREACHABLE, REFUSED, UNKNOWN_HOST }

    private val IPV4ISH = Regex("^\\d{1,3}(\\.\\d{1,3}){3}$")

    /** Host part of a bridge URL ("http://1.2.3.4:8137/x" → "1.2.3.4"); "" when there is none. */
    fun host(url: String): String {
        val noScheme = url.trim().substringAfter("://", url.trim())
        val authority = noScheme.substringBefore('/').substringAfter('@')
        return if (authority.startsWith("[")) authority.substringBefore(']') + "]" else authority.substringBefore(':')
    }

    /**
     * An IPv4-looking host with a number above 255 ("192.268.8.40") is a typo, not an address; without
     * this check the phone only times out after 10 s and the user never learns why.
     */
    fun invalidIp(url: String): String? {
        val h = host(url)
        if (!IPV4ISH.matches(h)) return null
        return h.takeIf { it.split('.').any { part -> part.toInt() > 255 } }
    }

    /** What a raw connection error most likely means. */
    fun classify(error: String): Problem? {
        val e = error.lowercase()
        return when {
            "refused" in e || "econnrefused" in e -> Problem.REFUSED
            "unable to resolve host" in e || "unknownhost" in e || "no address associated" in e -> Problem.UNKNOWN_HOST
            "timeout" in e || "timed out" in e || "failed to connect" in e || "etimedout" in e ||
                "ehostunreach" in e || "enetunreach" in e || "no route to host" in e -> Problem.UNREACHABLE
            else -> null
        }
    }
}
