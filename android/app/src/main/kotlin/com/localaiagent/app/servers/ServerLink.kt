package com.localaiagent.app.servers

import java.net.URI
import java.net.URLDecoder

/**
 * The pairing link from the PC's QR (PHONE_SERVER_SPEC §1):
 * `altair://body?n=<name>&s=<server id>&b=<server body id>&c=<code>[&u=&t=][&d=&fp=]`.
 * At least one route must be there: the relay through the PC (`u`, `t`) or the server's own door
 * (`d`, `fp`).
 */
data class ServerLink(
    /** The server's name, for the UI. */
    val name: String,
    /** The server's id on the PC: the relay path is `/b/<id>/…`. */
    val serverId: String,
    /** The server's body id: its challenges must return it as `verifier`. */
    val bodyId: String,
    /** One-time pairing code (single use, 10 minutes). Never stored after pairing. */
    val code: String,
    val relayUrl: String = "",
    val relayToken: String = "",
    val directUrl: String = "",
    /** SHA-256 of the door's certificate (DER), lowercase hex. */
    val fingerprint: String = "",
) {
    val hasRelay: Boolean get() = relayUrl.isNotBlank() && relayToken.isNotBlank()
    val hasDirect: Boolean get() = directUrl.isNotBlank() && fingerprint.isNotBlank()
}

private val CODE_RE = Regex("^[A-HJ-NP-Z2-9]{8}$")
private val FP_RE = Regex("^[0-9a-f]{64}$")

/** Parses a server pairing link; null if it is not one or is incomplete. */
fun parseServerLink(raw: String?): ServerLink? {
    val s = raw?.trim().orEmpty()
    if (s.isEmpty()) return null
    return try {
        val uri = URI(s)
        if (!"altair".equals(uri.scheme, ignoreCase = true) || !"body".equals(uri.host, ignoreCase = true)) return null
        val q = (uri.rawQuery ?: "").split('&').filter { it.isNotEmpty() }.associate { part ->
            val k = part.substringBefore('=')
            val v = part.substringAfter('=', "")
            URLDecoder.decode(k, "UTF-8") to URLDecoder.decode(v, "UTF-8").trim()
        }
        val link = ServerLink(
            name = q["n"].orEmpty(),
            serverId = q["s"].orEmpty(),
            bodyId = q["b"].orEmpty(),
            code = q["c"].orEmpty().uppercase(),
            relayUrl = q["u"].orEmpty().trimEnd('/'),
            relayToken = q["t"].orEmpty(),
            directUrl = q["d"].orEmpty().trimEnd('/'),
            fingerprint = q["fp"].orEmpty().lowercase().replace(":", ""),
        )
        when {
            link.serverId.isBlank() || link.bodyId.isBlank() -> null
            !CODE_RE.matches(link.code) -> null
            link.directUrl.isNotBlank() && !FP_RE.matches(link.fingerprint) -> null
            link.directUrl.isNotBlank() && !link.directUrl.startsWith("https://") -> null
            !link.hasRelay && !link.hasDirect -> null
            else -> link
        }
    } catch (e: IllegalArgumentException) {
        null
    } catch (e: java.net.URISyntaxException) {
        null
    }
}
