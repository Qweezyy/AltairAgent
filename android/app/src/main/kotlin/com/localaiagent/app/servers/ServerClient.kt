package com.localaiagent.app.servers

import com.localaiagent.core.NetPolicy
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/** A server the phone is paired with (or is pairing with): what the QR gave, minus the one-time code. */
@Serializable
data class ServerEntry(
    /** The server's id on the PC (the relay path); also our key for it. */
    val id: String,
    val name: String,
    /** The server's body id: the `verifier` its challenges must return. */
    val bodyId: String,
    val relayUrl: String = "",
    val relayToken: String = "",
    val directUrl: String = "",
    val fingerprint: String = "",
    /** The server trusts this phone's key (pairing succeeded). */
    val paired: Boolean = false,
    /** The Journal record to ask news after (`/api/notices?since=`); null before the first look. */
    val noticeNext: Long? = null,
) {
    val hasRelay: Boolean get() = relayUrl.isNotBlank() && relayToken.isNotBlank()
    val hasDirect: Boolean get() = directUrl.isNotBlank() && fingerprint.isNotBlank()

    companion object {
        fun of(link: ServerLink) = ServerEntry(
            id = link.serverId, name = link.name.ifBlank { link.serverId }, bodyId = link.bodyId,
            relayUrl = link.relayUrl, relayToken = link.relayToken,
            directUrl = link.directUrl, fingerprint = link.fingerprint,
        )
    }
}

/** How the phone reaches a server (PHONE_SERVER_SPEC §7). */
enum class ServerRoute { DIRECT, RELAY }

/** Why a server call failed, in the terms the UI acts on. */
sealed class ServerError(message: String) : IOException(message) {
    /** The server does not trust this phone (revoked, or never paired): pair again with a new QR. */
    class NotTrusted(message: String) : ServerError(message)
    /** The one-time code was wrong, used or expired, or tries are locked: a new QR is needed. */
    class CodeRefused(message: String) : ServerError(message)
    /** Neither route answers right now. */
    class Unreachable(message: String) : ServerError(message)
    /** The PC no longer knows this server (it was removed there). */
    class Gone(message: String) : ServerError(message)
    /** Any other answer: the status and the server's text. */
    class Http(val code: Int, message: String) : ServerError(message)
}

/** The phone's own key and name, the same toward every server. */
class PhoneIdentity(val seed: ByteArray, val deviceName: String) {
    val bodyId: String get() = BodyCrypto.bodyId(BodyCrypto.publicKey(seed))
    val card: JsonObject get() = BodyCrypto.card(seed, deviceName)
}

/**
 * Talks to one server over the route that works: its own TLS door (pinned certificate, signed
 * sign-in) or the PC relay (`/b/<id>`, the PC bridge token). The PC token never goes to the door;
 * the session token lives only in memory.
 */
class ServerClient(
    val entry: ServerEntry,
    private val identity: PhoneIdentity,
    relayHttp: OkHttpClient? = null,
    directBuilder: () -> OkHttpClient.Builder = { OkHttpClient.Builder() },
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val relay: OkHttpClient = relayHttp ?: OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()
    private val direct: OkHttpClient? = if (entry.hasDirect) {
        CertPin.client(entry.fingerprint, directBuilder().connectTimeout(8, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS))
    } else null

    /** The route last found working; re-probed when a call fails. */
    @Volatile var route: ServerRoute? = null
        private set

    // ------------------------------------------------------------------ routes

    /**
     * Finds the route to use now: the paired door first, then the PC (§7). Null: offline. Throws
     * [ServerError.Gone] when the PC says it has no such server and there is no door to try.
     */
    suspend fun probe(): ServerRoute? {
        val doorUp = direct != null && healthy(ServerRoute.DIRECT) == 200
        val relayStatus = if (entry.hasRelay && !(entry.paired && doorUp)) healthy(ServerRoute.RELAY) else -1
        val found = when {
            entry.paired && doorUp -> ServerRoute.DIRECT
            relayStatus == 200 -> ServerRoute.RELAY
            // Not paired over the door yet: it may still be the only way (pairing goes there).
            doorUp -> ServerRoute.DIRECT
            relayStatus == 404 -> throw ServerError.Gone("the PC no longer has this server")
            else -> null
        }
        route = found
        return found
    }

    /** The health answer's status over a route; -1 when it does not answer. */
    private suspend fun healthy(r: ServerRoute): Int = try {
        execute(r, request(r, "/api/health").get().build(), quick = true).first
    } catch (e: IOException) {
        -1
    }

    private fun base(r: ServerRoute): String = when (r) {
        ServerRoute.DIRECT -> entry.directUrl.trimEnd('/')
        ServerRoute.RELAY -> entry.relayUrl.trimEnd('/') + "/b/" + entry.id
    }

    private fun request(r: ServerRoute, path: String, query: Map<String, String> = emptyMap()): Request.Builder {
        val url = (base(r) + path).toHttpUrl().newBuilder().apply { query.forEach { (k, v) -> addQueryParameter(k, v) } }.build()
        val b = Request.Builder().url(url)
        if (r == ServerRoute.RELAY) {
            // The PC token travels only to the PC, and never in clear over a public network.
            if (!NetPolicy.credentialsAllowed(entry.relayUrl)) {
                throw ServerError.Unreachable("the PC address is public and not encrypted; the token stays on the phone")
            }
            b.header("Authorization", "Bearer ${entry.relayToken}")
        }
        return b
    }

    private suspend fun execute(r: ServerRoute, req: Request, quick: Boolean = false): Pair<Int, String> =
        withContext(Dispatchers.IO) {
            val base = if (r == ServerRoute.DIRECT) direct ?: throw ServerError.Unreachable("no door") else relay
            val client = if (quick) base.newBuilder().callTimeout(5, TimeUnit.SECONDS).build() else base
            client.newCall(req).execute().use { it.code to (it.body?.string().orEmpty()) }
        }

    // ------------------------------------------------------------------ pairing and signing in

    /**
     * Pairs this phone with the server by the QR's one-time code, by whichever route answers first.
     * Returns the server's card; its id must be the body id from the QR.
     */
    suspend fun pair(code: String): JsonObject {
        val payload = buildJsonObject { put("code", code); put("card", identity.card) }.toString()
        var lastError: Throwable = ServerError.Unreachable("no route to the server")
        for (r in listOfNotNull(ServerRoute.DIRECT.takeIf { direct != null }, ServerRoute.RELAY.takeIf { entry.hasRelay })) {
            val (status, text) = try {
                execute(r, request(r, "/api/bodies/pair").post(payload.toRequestBody(JSON)).build())
            } catch (e: IOException) {
                lastError = e; continue
            }
            when (status) {
                200 -> {
                    val body = json.parseToJsonElement(text).jsonObject["body"]?.jsonObject
                        ?: throw ServerError.Http(status, "no server card in the answer")
                    val id = body["id"]?.jsonPrimitive?.contentOrNull
                    if (id != entry.bodyId) throw ServerError.NotTrusted("the server is not the one in the QR")
                    route = r
                    return body
                }
                401 -> throw ServerError.CodeRefused(detail(text))
                503 -> { lastError = ServerError.Unreachable(detail(text)); continue }
                404 -> if (r == ServerRoute.RELAY) throw ServerError.Gone(detail(text)) else { lastError = ServerError.Http(404, detail(text)); continue }
                else -> throw ServerError.Http(status, detail(text))
            }
        }
        throw lastError
    }

    /** Signs in over the door (§3.3): challenge, check the verifier, sign, log in. */
    private suspend fun signIn(): String {
        val ask = buildJsonObject { put("id", identity.bodyId) }.toString()
        val (cs, ct) = execute(ServerRoute.DIRECT, request(ServerRoute.DIRECT, "/api/bodies/challenge").post(ask.toRequestBody(JSON)).build())
        if (cs == 401 || cs == 403) throw ServerError.NotTrusted(detail(ct))
        if (cs != 200) throw ServerError.Http(cs, detail(ct))
        val challenge = json.parseToJsonElement(ct).jsonObject
        val nonce = challenge["nonce"]?.jsonPrimitive?.contentOrNull ?: throw ServerError.Http(cs, "no nonce")
        val verifier = challenge["verifier"]?.jsonPrimitive?.contentOrNull
        // Only the server this phone paired with may get a signature.
        if (verifier != entry.bodyId) throw ServerError.NotTrusted("the server is not the one paired")
        val login = buildJsonObject {
            put("id", identity.bodyId)
            put("nonce", nonce)
            put("signature", BodyCrypto.sign(identity.seed, BodyCrypto.authMessage(nonce, verifier)))
        }.toString()
        val (ls, lt) = execute(ServerRoute.DIRECT, request(ServerRoute.DIRECT, "/api/bodies/login").post(login.toRequestBody(JSON)).build())
        if (ls == 401) throw ServerError.NotTrusted(detail(lt))
        if (ls != 200) throw ServerError.Http(ls, detail(lt))
        val token = json.parseToJsonElement(lt).jsonObject["token"]?.jsonPrimitive?.contentOrNull
            ?: throw ServerError.Http(ls, "no token")
        tokens[entry.id] = token
        return token
    }

    private suspend fun token(): String = tokens[entry.id] ?: signIn()

    // ------------------------------------------------------------------ calls

    suspend fun get(path: String, query: Map<String, String> = emptyMap()): JsonObject = call(path, query, null)

    suspend fun post(path: String, body: JsonObject = JsonObject(emptyMap())): JsonObject = call(path, emptyMap(), body)

    private suspend fun call(path: String, query: Map<String, String>, body: JsonObject?): JsonObject {
        val r = route ?: probe() ?: throw ServerError.Unreachable("the server does not answer by either route")
        return try {
            when (r) {
                ServerRoute.RELAY -> {
                    val (s, t) = execute(r, build(r, path, query, body, null))
                    when (s) {
                        in 200..299 -> parse(t)
                        // The PC has no live tunnel to it now: the door may still answer.
                        503 -> { route = null; retryDirect(path, query, body) ?: throw ServerError.Unreachable(detail(t)) }
                        404 -> throw ServerError.Gone(detail(t))
                        else -> throw ServerError.Http(s, detail(t))
                    }
                }
                ServerRoute.DIRECT -> directCall(path, query, body)
            }
        } catch (e: ServerError) {
            throw e
        } catch (e: IOException) {
            // The network moved under us: find the route again next time.
            route = null
            throw ServerError.Unreachable(e.message ?: "no connection")
        }
    }

    private suspend fun retryDirect(path: String, query: Map<String, String>, body: JsonObject?): JsonObject? {
        if (!entry.paired || direct == null || healthy(ServerRoute.DIRECT) != 200) return null
        route = ServerRoute.DIRECT
        return directCall(path, query, body)
    }

    private suspend fun directCall(path: String, query: Map<String, String>, body: JsonObject?): JsonObject {
        val (s, t) = execute(ServerRoute.DIRECT, build(ServerRoute.DIRECT, path, query, body, token()))
        if (s != 401) return if (s in 200..299) parse(t) else throw ServerError.Http(s, detail(t))
        // A token from before a server restart: sign in once more; still refused means not trusted.
        tokens.remove(entry.id)
        val (s2, t2) = execute(ServerRoute.DIRECT, build(ServerRoute.DIRECT, path, query, body, signIn()))
        if (s2 == 401) { tokens.remove(entry.id); throw ServerError.NotTrusted(detail(t2)) }
        return if (s2 in 200..299) parse(t2) else throw ServerError.Http(s2, detail(t2))
    }

    private fun build(r: ServerRoute, path: String, query: Map<String, String>, body: JsonObject?, bearer: String?): Request {
        val b = request(r, path, query)
        bearer?.let { b.header("Authorization", "Bearer $it") }
        if (body != null) b.post(body.toString().toRequestBody(JSON)) else b.get()
        return b.build()
    }

    /**
     * Opens the server's chat socket (`/ws`, the same protocol as the PC's) over the current route.
     * The direct route signs in first; its token goes in the query as WebSockets need.
     */
    suspend fun openSocket(listener: WebSocketListener): WebSocket {
        val r = route ?: probe() ?: throw ServerError.Unreachable("the server does not answer by either route")
        val client = (if (r == ServerRoute.DIRECT) direct!! else relay).newBuilder()
            .readTimeout(0, TimeUnit.MILLISECONDS).pingInterval(20, TimeUnit.SECONDS).build()
        val url = base(r).replaceFirst("https://", "wss://").replaceFirst("http://", "ws://") + "/ws"
        val token = if (r == ServerRoute.DIRECT) token() else entry.relayToken
        if (r == ServerRoute.RELAY && !NetPolicy.credentialsAllowed(entry.relayUrl)) {
            throw ServerError.Unreachable("the PC address is public and not encrypted; the token stays on the phone")
        }
        val req = Request.Builder().url("$url?token=${java.net.URLEncoder.encode(token, "UTF-8")}").build()
        return client.newWebSocket(req, listener)
    }

    /** After the socket was refused with 401 over the door: drop the token so the next open signs in. */
    fun forgetToken() { tokens.remove(entry.id) }

    private fun parse(text: String): JsonObject =
        runCatching { json.parseToJsonElement(text).jsonObject }.getOrElse { JsonObject(emptyMap()) }

    private fun detail(text: String): String =
        runCatching { json.parseToJsonElement(text).jsonObject["detail"]?.jsonPrimitive?.contentOrNull }.getOrNull()
            ?: text.take(200)

    companion object {
        private val JSON = "application/json".toMediaType()

        /** Session tokens per server: memory only, gone with the process (the server's live 12 h). */
        private val tokens = ConcurrentHashMap<String, String>()

        fun forgetAllTokens() = tokens.clear()
    }
}
