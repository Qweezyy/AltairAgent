package com.localaiagent.app.servers

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.util.Locale

/** A server's news item (PHONE_SERVER_SPEC §6). */
data class ServerNotice(
    /** The server it is about: our entry id (the server's id on the PC). */
    val serverId: String,
    val kind: String,
    val level: String,
    val title: String,
    val text: String,
    val chat: String,
    val at: Double,
) {
    /** The same event can come through the PC and from the server itself: this key is one per event. */
    val key: String get() = listOf(serverId, kind, chat, String.format(Locale.ROOT, "%.3f", at)).joinToString("|")

    companion object {
        /**
         * Reads a notice. [selfServerId] is the server a direct connection belongs to (its notices say
         * `body_id: "self"`); through the PC, `body_id` is the server's id there.
         */
        fun parse(o: JsonObject, selfServerId: String?): ServerNotice? {
            fun s(k: String) = o[k]?.jsonPrimitive?.contentOrNull.orEmpty()
            val body = s("body_id")
            val server = if (body.isBlank() || body == "self") selfServerId ?: return null else body
            val kind = s("kind").ifBlank { return null }
            return ServerNotice(
                serverId = server, kind = kind, level = s("level").ifBlank { "info" },
                title = s("title"), text = s("text"), chat = s("chat"),
                at = o["at"]?.jsonPrimitive?.doubleOrNull ?: 0.0,
            )
        }
    }
}

/**
 * Remembers the notices already shown, so one event that arrives twice (through the PC and the
 * server, or live and from the background poll) makes one notification. Bounded, oldest out first.
 */
class NoticeDedupe(private val capacity: Int = 500, initial: Collection<String> = emptyList()) {
    private val seen = LinkedHashSet<String>(initial.toList().takeLast(capacity))

    /** True the first time a notice is seen; false for a repeat. */
    @Synchronized
    fun firstTime(notice: ServerNotice): Boolean {
        if (!seen.add(notice.key)) return false
        while (seen.size > capacity) seen.remove(seen.first())
        return true
    }

    @Synchronized
    fun snapshot(): List<String> = seen.toList()
}
