package com.localaiagent.app.servers

import android.content.Context
import android.os.Build
import com.localaiagent.app.security.KeyVault
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.util.Base64

/**
 * The servers this phone is a body of, and the phone's own key. Both are secrets (relay tokens, the
 * private key): stored KeyVault-encrypted. The one-time pairing code is never stored.
 */
class ServerStore(
    context: Context,
    private val seal: (String) -> String = KeyVault::encrypt,
    private val open: (String) -> String = KeyVault::decrypt,
) {
    private val prefs = context.getSharedPreferences("servers", Context.MODE_PRIVATE)

    @Synchronized
    fun entries(): List<ServerEntry> = ServerCodec.decode(open(prefs.getString(KEY_ENTRIES, "").orEmpty()))

    fun entry(id: String): ServerEntry? = entries().firstOrNull { it.id == id }

    /** Adds or replaces a server (by its id on the PC: a new QR for the same server updates it). */
    @Synchronized
    fun put(entry: ServerEntry) {
        val list = entries().filterNot { it.id == entry.id } + entry
        prefs.edit().putString(KEY_ENTRIES, seal(ServerCodec.encode(list))).apply()
    }

    @Synchronized
    fun update(id: String, change: (ServerEntry) -> ServerEntry) {
        val list = entries().map { if (it.id == id) change(it) else it }
        prefs.edit().putString(KEY_ENTRIES, seal(ServerCodec.encode(list))).apply()
    }

    @Synchronized
    fun remove(id: String) {
        prefs.edit().putString(KEY_ENTRIES, seal(ServerCodec.encode(entries().filterNot { it.id == id }))).apply()
    }

    /** The phone's key, made on first use; the same toward every server. */
    @Synchronized
    fun identity(): PhoneIdentity {
        val stored = open(prefs.getString(KEY_SEED, "").orEmpty())
        val seed = runCatching { Base64.getDecoder().decode(stored) }.getOrNull()?.takeIf { it.size == 32 }
            ?: BodyCrypto.newSeed().also {
                prefs.edit().putString(KEY_SEED, seal(Base64.getEncoder().encodeToString(it))).apply()
            }
        return PhoneIdentity(seed, deviceName())
    }

    /** Notices already shown (not secret): kept so a restart does not notify again. */
    fun seenNotices(): List<String> = ServerCodec.decodeKeys(prefs.getString(KEY_SEEN, "").orEmpty())

    fun saveSeenNotices(keys: List<String>) {
        prefs.edit().putString(KEY_SEEN, ServerCodec.encodeKeys(keys)).apply()
    }

    private fun deviceName(): String =
        listOf(Build.MANUFACTURER.orEmpty(), Build.MODEL.orEmpty())
            .filter { it.isNotBlank() }
            .let { parts -> if (parts.size == 2 && parts[1].startsWith(parts[0], ignoreCase = true)) listOf(parts[1]) else parts }
            .joinToString(" ").ifBlank { "Phone" }

    private companion object {
        const val KEY_ENTRIES = "entries"
        const val KEY_SEED = "phone_seed"
        const val KEY_SEEN = "notices_seen"
    }
}

/** The stored form of the server list: plain JSON before sealing, tolerant of older/newer fields. */
object ServerCodec {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val listSer = ListSerializer(ServerEntry.serializer())
    private val keysSer = ListSerializer(String.serializer())

    fun encode(list: List<ServerEntry>): String = json.encodeToString(listSer, list)

    fun decode(text: String): List<ServerEntry> =
        if (text.isBlank()) emptyList() else runCatching { json.decodeFromString(listSer, text) }.getOrDefault(emptyList())

    fun encodeKeys(keys: List<String>): String = json.encodeToString(keysSer, keys)

    fun decodeKeys(text: String): List<String> =
        if (text.isBlank()) emptyList() else runCatching { json.decodeFromString(keysSer, text) }.getOrDefault(emptyList())
}
