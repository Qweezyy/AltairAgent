package com.localaiagent.app.mcp

import com.localaiagent.app.security.KeyVault
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.File

/**
 * One MCP server (an external source of tools). The phone talks to [url] DIRECTLY over HTTP/SSE,
 * without the PC, so it works in both builds. [transport]: "http" (Streamable HTTP, the current
 * standard) or "sse" (the older HTTP+SSE, e.g. mcp-proxy). [headers] carry auth, usually
 * `Authorization: Bearer …`. [fromPc] — the server came from a PC sync; the next sync may update or
 * remove it, while servers added on the phone are never touched by a sync.
 */
data class McpServer(
    val name: String,
    val url: String,
    val transport: String = "http",
    val headers: Map<String, String> = emptyMap(),
    val enabled: Boolean = true,
    val fromPc: Boolean = false,
)

/** Pure config helpers, shared by the store, the UI and the tests. */
object McpConfig {
    private val NAME_RE = Regex("^[A-Za-z0-9_.-]{1,48}$")
    private val SECRET_HINT = Regex("(key|token|secret|pass|auth|cookie|bearer)", RegexOption.IGNORE_CASE)

    /** Same rule as the PC (pc/core/mcp/manager.py), so a server keeps its name across devices. */
    fun isValidName(name: String): Boolean = NAME_RE.matches(name)

    /** A valid name from free text ("My Notion" → "My-Notion"); "" if nothing usable is left. */
    fun safeName(raw: String): String = raw.trim().replace(Regex("[^A-Za-z0-9_.-]+"), "-").trim('-', '.').take(48)

    fun isHttpUrl(url: String): Boolean = url.startsWith("http://") || url.startsWith("https://")

    /** Header value for display: secrets masked, so a screenshot of the settings leaks nothing. */
    fun maskHeader(key: String, value: String): String =
        if (!SECRET_HINT.containsMatchIn(key)) value
        else if (value.length > 12) value.take(7) + "…" + value.takeLast(2) else "••••"

    /** Result of parsing pasted JSON: servers the phone can use, plus names it had to skip and why. */
    data class Parsed(val servers: List<McpServer>, val skipped: Map<String, String>)

    /**
     * Servers from JSON pasted as is from Claude Desktop, Cursor or VS Code:
     * `{"mcpServers": {...}}`, `{"servers": {...}}` or `{name: {...}}`. Local (stdio) servers need a
     * process on a computer, so they are skipped with a reason — the PC can run them.
     */
    fun parsePasted(text: String): Parsed {
        val root = runCatching { Json.parseToJsonElement(text.trim()) }.getOrNull() as? JsonObject
            ?: throw IllegalArgumentException("not a JSON object")
        val servers = (root["mcpServers"] as? JsonObject) ?: (root["servers"] as? JsonObject) ?: root
        require(servers.isNotEmpty()) { "no servers found in the JSON" }
        val out = mutableListOf<McpServer>()
        val skipped = linkedMapOf<String, String>()
        for ((rawName, el) in servers) {
            val cfg = el as? JsonObject
            if (cfg == null) { skipped[rawName] = "not an object"; continue }
            val name = safeName(rawName)
            if (!isValidName(name)) { skipped[rawName] = "invalid name"; continue }
            val url = (cfg.str("url") ?: cfg.str("serverUrl")).orEmpty().trim()
            if (url.isEmpty()) {
                skipped[rawName] = if (cfg.str("command") != null) "local" else "no url"
                continue
            }
            if (!isHttpUrl(url)) { skipped[rawName] = "bad url"; continue }
            val kind = cfg.str("type").orEmpty().lowercase()
            val transport = cfg.str("transport")?.lowercase() ?: if (kind == "sse") "sse" else "http"
            val headers = linkedMapOf<String, String>()
            (cfg["headers"] as? JsonObject)?.forEach { (k, v) -> (v as? JsonPrimitive)?.contentOrNull?.let { headers[k] = it } }
            val token = cfg.str("token") ?: cfg.str("authToken") ?: cfg.str("bearer")
            if (!token.isNullOrBlank() && headers.keys.none { it.equals("Authorization", true) }) {
                headers["Authorization"] = "Bearer $token"
            }
            val disabled = (cfg["disabled"] as? JsonPrimitive)?.booleanOrNull == true
            out += McpServer(name, url, if (transport == "sse") "sse" else "http", headers, enabled = !disabled)
        }
        return Parsed(out, skipped)
    }

    private fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
}

/**
 * MCP server configs in the app's private folder (mcp_servers.json, encrypted with [KeyVault]).
 *
 * Format: `{"servers": [...], "dismissedPc": [...]}`. `dismissedPc` remembers PC servers the user
 * deleted on the phone, so the next sync does not bring them back. Older builds wrote a bare array.
 */
class McpStore(private val filesDir: File, private val vault: Boolean = true) {
    private val file = File(filesDir, "mcp_servers.json")
    private val json = Json { ignoreUnknownKeys = true }

    private data class State(val servers: List<McpServer>, val dismissedPc: Set<String>)

    fun load(): MutableList<McpServer> = read().servers.toMutableList()

    private fun read(): State {
        if (!file.isFile) return State(emptyList(), emptySet())
        return runCatching {
            val text = file.readText().let { if (vault) KeyVault.decrypt(it) else it }
            val root = json.parseToJsonElement(text)
            val arr = (root as? JsonArray) ?: (root.jsonObject["servers"] as? JsonArray) ?: JsonArray(emptyList())
            val dismissed = ((root as? JsonObject)?.get("dismissedPc") as? JsonArray)
                ?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.lowercase() }?.toSet().orEmpty()
            State(arr.mapNotNull { parseServer(it as? JsonObject ?: return@mapNotNull null) }, dismissed)
        }.getOrElse { State(emptyList(), emptySet()) }
    }

    private fun parseServer(o: JsonObject): McpServer? {
        val name = o["name"]?.jsonPrimitive?.contentOrNull ?: return null
        val url = o["url"]?.jsonPrimitive?.contentOrNull ?: return null
        val headers = (o["headers"] as? JsonObject)?.mapNotNull { (k, v) ->
            (v as? JsonPrimitive)?.contentOrNull?.let { k to it }
        }?.toMap() ?: emptyMap()
        return McpServer(
            name = name,
            url = url,
            transport = o["transport"]?.jsonPrimitive?.contentOrNull ?: "http",
            headers = headers,
            enabled = o["enabled"]?.jsonPrimitive?.booleanOrNull ?: true,
            fromPc = o["fromPc"]?.jsonPrimitive?.booleanOrNull ?: false,
        )
    }

    /** Re-encrypts a file written in plaintext by an older build. Idempotent; call once at start. */
    fun migrateToEncrypted() {
        if (!vault || !file.isFile) return
        val raw = runCatching { file.readText() }.getOrNull() ?: return
        if (raw.isNotBlank() && !KeyVault.isEncrypted(raw)) write(read())
    }

    private fun write(state: State) {
        filesDir.mkdirs()
        val root = buildJsonObject {
            putJsonArray("servers") {
                state.servers.forEach { s ->
                    addJsonObject {
                        put("name", s.name); put("url", s.url); put("transport", s.transport)
                        put("enabled", s.enabled); put("fromPc", s.fromPc)
                        putJsonObject("headers") { s.headers.forEach { (k, v) -> put(k, v) } }
                    }
                }
            }
            putJsonArray("dismissedPc") { state.dismissedPc.sorted().forEach { add(JsonPrimitive(it)) } }
        }.toString()
        val tmp = File(filesDir, "mcp_servers.json.tmp")
        tmp.writeText(if (vault) KeyVault.encrypt(root) else root)
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
    }

    /**
     * Adds or updates a server added on the phone. [previousName] renames an existing entry. A server
     * edited on the phone becomes the user's own: a sync no longer overwrites it.
     */
    fun put(server: McpServer, previousName: String? = null) {
        val st = read()
        val key = previousName ?: server.name
        val list = st.servers
            .filterNot { it.name.equals(server.name, true) && !it.name.equals(key, true) }
            .toMutableList()
        val entry = server.copy(fromPc = false)
        val i = list.indexOfFirst { it.name.equals(key, true) }
        if (i >= 0) list[i] = entry else list += entry
        write(State(list, st.dismissedPc - server.name.lowercase()))
    }

    fun get(name: String): McpServer? = load().firstOrNull { it.name.equals(name, true) }

    /** Removes a server. A PC server is also remembered as dismissed so the next sync skips it. */
    fun remove(name: String) {
        val st = read()
        val gone = st.servers.firstOrNull { it.name.equals(name, true) } ?: return
        val dismissed = if (gone.fromPc) st.dismissedPc + gone.name.lowercase() else st.dismissedPc
        write(State(st.servers - gone, dismissed))
    }

    fun setEnabled(name: String, enabled: Boolean) {
        val st = read()
        write(st.copy(servers = st.servers.map { if (it.name.equals(name, true)) it.copy(enabled = enabled) else it }))
    }

    /**
     * Sync from the PC: replaces the previous PC servers with [incoming] and keeps the phone's own ones.
     * A name taken by a phone server is skipped (the phone setting wins), a server the user deleted
     * here stays deleted, and a server the user switched off here stays off.
     */
    fun replaceFromPc(incoming: List<McpServer>) {
        val st = read()
        val manual = st.servers.filterNot { it.fromPc }
        val manualNames = manual.map { it.name.lowercase() }.toSet()
        val wasEnabled = st.servers.filter { it.fromPc }.associate { it.name.lowercase() to it.enabled }
        val fresh = incoming
            .filter { it.name.lowercase() !in manualNames && it.name.lowercase() !in st.dismissedPc }
            .map { it.copy(fromPc = true, enabled = wasEnabled[it.name.lowercase()] ?: it.enabled) }
        write(State(manual + fresh, st.dismissedPc))
    }
}
