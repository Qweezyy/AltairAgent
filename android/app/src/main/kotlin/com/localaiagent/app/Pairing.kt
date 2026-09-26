package com.localaiagent.app

import android.net.Uri

/** Данные связывания телефона с ПК-мостом. */
data class BridgePair(val url: String, val token: String, val workspace: String, val lang: String = "")

/**
 * Разобрать ссылку связывания `altair://pair?u=<url>&t=<token>&w=<workspace>` (значения URL-encoded).
 * Возвращает null, если это не ссылка связывания. Форвард-совместимо с будущим QR-пэйрингом аккаунтов.
 */
fun parsePairLink(raw: String?): BridgePair? {
    val s = raw?.trim().orEmpty()
    if (s.isEmpty()) return null
    return try {
        val uri = Uri.parse(s)
        if (!"altair".equals(uri.scheme, ignoreCase = true)) return null
        if (!"pair".equals(uri.host, ignoreCase = true)) return null
        val url = uri.getQueryParameter("u")?.trim().orEmpty()
        val token = uri.getQueryParameter("t")?.trim().orEmpty()
        val ws = uri.getQueryParameter("w")?.trim().orEmpty()
        val lang = (uri.getQueryParameter("lang") ?: uri.getQueryParameter("l"))?.trim()?.lowercase().orEmpty()
        if (url.isEmpty() && token.isEmpty()) null else BridgePair(url, token, ws, lang)
    } catch (e: Exception) {
        null
    }
}
