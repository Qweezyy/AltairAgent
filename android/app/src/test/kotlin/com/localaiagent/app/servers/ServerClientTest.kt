package com.localaiagent.app.servers

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.Base64
import java.util.Collections
import java.util.UUID

/**
 * The phone's client against a fake server that follows PHONE_SERVER_SPEC §3–5: a TLS door with its
 * own certificate (pinned) and signed sign-in, and the PC relay under `/b/<id>` with the PC token.
 */
class ServerClientTest {

    private lateinit var door: FakeServer
    private lateinit var relay: MockWebServer
    private lateinit var doorFp: String
    private val identity = PhoneIdentity(ByteArray(32) { it.toByte() }, "Pixel test")

    /** What reached the relay: path and the Authorization it carried. */
    private val relaySeen = Collections.synchronizedList(mutableListOf<Pair<String, String>>())
    @Volatile private var relayDown = false

    @Before
    fun setUp() {
        ServerClient.forgetAllTokens()
        door = FakeServer()
        // A throwaway self-signed certificate, like a server's door makes for itself.
        val cert = HeldCertificate.Builder().commonName("altair-door-test").build()
        doorFp = CertPin.sha256Hex(cert.certificate.encoded)
        val tls = HandshakeCertificates.Builder().heldCertificate(cert).build()
        door.https = MockWebServer().apply {
            useHttps(tls.sslSocketFactory(), false)
            dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest) = door.handle(request, "")
            }
            start()
        }
        // The PC: checks its own token, strips it, forwards to the same server logic.
        relay = MockWebServer().apply {
            dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val auth = request.getHeader("Authorization").orEmpty()
                    val path = request.requestUrl!!.encodedPath
                    relaySeen += path to auth
                    val prefix = "/b/srv1/"
                    return when {
                        auth != "Bearer pc-token" -> respond(401, """{"detail":"bad PC token"}""")
                        !path.startsWith(prefix) -> respond(404, """{"detail":"no such server"}""")
                        relayDown -> respond(503, """{"detail":"server tunnel is down"}""")
                        else -> door.handle(request, prefix.trimEnd('/'), viaRelay = true)
                    }
                }
            }
            start()
        }
    }

    @After
    fun tearDown() {
        runCatching { door.https.shutdown() }
        relay.shutdown()
    }

    private fun entry(
        direct: Boolean = true, viaRelay: Boolean = true, paired: Boolean = false, fp: String = doorFp,
        bodyId: String = FakeServer.SERVER_ID,
    ) = ServerEntry(
        id = "srv1", name = "test-vps", bodyId = bodyId,
        relayUrl = if (viaRelay) "http://127.0.0.1:${relay.port}" else "",
        relayToken = if (viaRelay) "pc-token" else "",
        directUrl = if (direct) "https://127.0.0.1:${door.https.port}" else "",
        fingerprint = if (direct) fp else "",
        paired = paired,
    )

    // ---------------------------------------------------------------- pairing

    @Test
    fun pairsOverTheDoorOnceAndSignsInWithItsKey() = runBlocking {
        val client = ServerClient(entry(), identity)
        val card = client.pair("K7M2PQ9X")
        assertEquals(FakeServer.SERVER_ID, card["id"]!!.jsonPrimitive.content)
        assertEquals(ServerRoute.DIRECT, client.route)
        assertTrue("the server stored the phone's key", door.trusted.containsKey(identity.bodyId))

        // The code works once.
        try { ServerClient(entry(), identity).pair("K7M2PQ9X"); fail("a used code must be refused") }
        catch (_: ServerError.CodeRefused) {}

        val paired = ServerClient(entry(paired = true), identity)
        val sessions = paired.get("/api/sessions")
        assertEquals("c1", sessions["sessions"]!!.jsonArray[0].jsonObject["id"]!!.jsonPrimitive.content)
        assertEquals(1, door.logins)
        // The PC token never goes through the door.
        assertFalse(door.authSeen.any { it.contains("pc-token") })
    }

    @Test
    fun pairsThroughThePcWhenThereIsNoDoor() = runBlocking {
        val client = ServerClient(entry(direct = false), identity)
        client.pair("K7M2PQ9X")
        assertEquals(ServerRoute.RELAY, client.route)
        assertTrue(relaySeen.any { it.first == "/b/srv1/api/bodies/pair" && it.second == "Bearer pc-token" })

        val status = ServerClient(entry(direct = false, paired = true), identity).get("/api/body/status")
        assertEquals("linux", status["system"]!!.jsonPrimitive.content)
        // Through the PC the server needs no sign-in of its own.
        assertEquals(0, door.logins)
    }

    @Test
    fun aServerOtherThanTheOneInTheQrIsRefused() = runBlocking {
        try {
            ServerClient(entry(bodyId = "someoneElse12345"), identity).pair("K7M2PQ9X")
            fail("the card of another server must be refused")
        } catch (_: ServerError.NotTrusted) {}
    }

    @Test
    fun aWrongCodeAsksForANewQr() = runBlocking {
        try { ServerClient(entry(), identity).pair("AAAAAAAA"); fail() } catch (_: ServerError.CodeRefused) {}
        assertTrue(door.trusted.isEmpty())
    }

    // ---------------------------------------------------------------- the pin

    @Test
    fun aDoorWithAnotherCertificateIsNeverTalkedTo() = runBlocking {
        val client = ServerClient(entry(viaRelay = false, fp = "ab".repeat(32)), identity)
        try { client.pair("K7M2PQ9X"); fail("a wrong certificate must be refused") }
        catch (e: Exception) { assertTrue(e is java.io.IOException) }
        assertTrue("nothing reached the server", door.requests.isEmpty())
        assertTrue(door.trusted.isEmpty())
    }

    // ---------------------------------------------------------------- signing in

    @Test
    fun aRestartedServerIsSignedInToAgainOnce() = runBlocking {
        door.trust(identity)
        val client = ServerClient(entry(paired = true), identity)
        client.get("/api/sessions")
        door.tokens.clear() // the server restarted: its tokens are gone
        client.get("/api/sessions")
        assertEquals(2, door.logins)
        // And the token is reused while it works.
        client.get("/api/sessions")
        assertEquals(2, door.logins)
    }

    @Test
    fun aRevokedPhoneIsToldToPairAgain() = runBlocking {
        door.trust(identity)
        val client = ServerClient(entry(paired = true, viaRelay = false), identity)
        client.get("/api/sessions")
        door.trusted.clear(); door.tokens.clear()
        try { client.get("/api/sessions"); fail() } catch (_: ServerError.NotTrusted) {}
    }

    @Test
    fun noSignatureForAServerThatIsNotThePairedOne() = runBlocking {
        door.trust(identity)
        door.verifier = "anotherServer123"
        try { ServerClient(entry(paired = true, viaRelay = false), identity).get("/api/sessions"); fail() }
        catch (_: ServerError.NotTrusted) {}
        assertEquals("no login was attempted", 0, door.loginAttempts)
    }

    // ---------------------------------------------------------------- routes

    @Test
    fun thePairedDoorIsPreferredAndThePcIsTheFallback() = runBlocking {
        door.trust(identity)
        assertEquals(ServerRoute.DIRECT, ServerClient(entry(paired = true), identity).probe())
        // Not paired over the door yet: the PC goes first.
        assertEquals(ServerRoute.RELAY, ServerClient(entry(paired = false), identity).probe())
        // The door is down: the PC.
        door.https.shutdown()
        assertEquals(ServerRoute.RELAY, ServerClient(entry(paired = true), identity).probe())
    }

    @Test
    fun aDownTunnelSwitchesToTheDoor() = runBlocking {
        door.trust(identity)
        val client = ServerClient(entry(paired = true, direct = true), identity)
        // Start on the PC as if the door had not answered at first.
        door.healthDown = true
        assertEquals(ServerRoute.RELAY, client.probe())
        door.healthDown = false
        relayDown = true
        val sessions = client.get("/api/sessions")
        assertTrue(sessions.containsKey("sessions"))
        assertEquals(ServerRoute.DIRECT, client.route)
    }

    @Test
    fun aServerRemovedOnThePcIsGone() = runBlocking {
        val gone = entry(direct = false).copy(id = "srv-removed", paired = true)
        try { ServerClient(gone, identity).get("/api/sessions"); fail() } catch (_: ServerError.Gone) {}
    }

    // ---------------------------------------------------------------- storage

    @Test
    fun theStoredListRoundTripsAndNeverHoldsTheCode() {
        val link = parseServerLink(
            "altair://body?n=test-vps&s=srv1&b=${FakeServer.SERVER_ID}&c=K7M2PQ9X&u=http%3A%2F%2F10.0.0.2%3A8765&t=tok",
        )!!
        val stored = ServerCodec.encode(listOf(ServerEntry.of(link).copy(paired = true, noticeNext = 42)))
        assertFalse(stored.contains("K7M2PQ9X"))
        val back = ServerCodec.decode(stored).single()
        assertEquals("srv1", back.id)
        assertEquals("tok", back.relayToken)
        assertEquals(42L, back.noticeNext)
        assertTrue(back.paired)
        assertTrue(ServerCodec.decode("not json").isEmpty())
        assertEquals(listOf("a", "b"), ServerCodec.decodeKeys(ServerCodec.encodeKeys(listOf("a", "b"))))
    }

    // ---------------------------------------------------------------- the fake server

    private class FakeServer {
        lateinit var https: MockWebServer
        val trusted = java.util.concurrent.ConcurrentHashMap<String, ByteArray>()
        val tokens = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
        private val nonces = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
        val requests = Collections.synchronizedList(mutableListOf<String>())
        val authSeen = Collections.synchronizedList(mutableListOf<String>())
        @Volatile var code: String? = "K7M2PQ9X"
        @Volatile var verifier = SERVER_ID
        @Volatile var logins = 0
        @Volatile var loginAttempts = 0
        @Volatile var healthDown = false
        private val json = Json { ignoreUnknownKeys = true }

        fun trust(identity: PhoneIdentity) { trusted[identity.bodyId] = BodyCrypto.publicKey(identity.seed) }

        fun handle(req: RecordedRequest, prefix: String, viaRelay: Boolean = false): MockResponse {
            val path = req.requestUrl!!.encodedPath.removePrefix(prefix)
            if (!viaRelay) {
                requests += path
                authSeen += req.getHeader("Authorization").orEmpty()
            }
            val body = req.body.readUtf8()
            val input = if (body.isBlank()) JsonObject(emptyMap()) else json.parseToJsonElement(body).jsonObject
            fun s(k: String) = input[k]?.jsonPrimitive?.contentOrNull.orEmpty()
            return when (path) {
                "/api/health" -> if (healthDown && !viaRelay) respond(500, "{}") else respond(200, """{"ok":true}""")
                "/api/bodies/pair" -> {
                    if (code == null || s("code") != code) return respond(401, """{"detail":"code"}""")
                    code = null
                    val card = input["card"]!!.jsonObject
                    val pub = Base64.getDecoder().decode(card["public_key"]!!.jsonPrimitive.content)
                    if (BodyCrypto.bodyId(pub) != card["id"]!!.jsonPrimitive.content) return respond(400, "{}")
                    trusted[card["id"]!!.jsonPrimitive.content] = pub
                    respond(200, buildJsonObject {
                        put("ok", true)
                        put("body", buildJsonObject { put("id", SERVER_ID); put("name", "test-vps"); put("kind", "server") })
                    }.toString())
                }
                "/api/bodies/challenge" -> {
                    if (!trusted.containsKey(s("id"))) return respond(401, """{"detail":"unknown body"}""")
                    val nonce = UUID.randomUUID().toString().also { nonces += it }
                    respond(200, buildJsonObject { put("nonce", nonce); put("verifier", verifier) }.toString())
                }
                "/api/bodies/login" -> {
                    loginAttempts++
                    val pub = trusted[s("id")] ?: return respond(401, "{}")
                    if (!nonces.remove(s("nonce"))) return respond(401, "{}")
                    val message = BodyCrypto.authMessage(s("nonce"), SERVER_ID).toByteArray()
                    val ok = Ed25519Signer().run {
                        init(false, Ed25519PublicKeyParameters(pub, 0))
                        update(message, 0, message.size)
                        verifySignature(Base64.getDecoder().decode(s("signature")))
                    }
                    if (!ok) return respond(401, "{}")
                    logins++
                    val token = UUID.randomUUID().toString().also { tokens += it }
                    respond(200, """{"token":"$token","expires_in":43200}""")
                }
                else -> {
                    // Through the PC the server trusts the tunnel; at the door a token is required.
                    if (!viaRelay) {
                        val t = req.getHeader("Authorization").orEmpty().removePrefix("Bearer ")
                        if (t !in tokens) return respond(401, """{"detail":"sign in"}""")
                    }
                    when (path) {
                        "/api/sessions" -> respond(200, buildJsonObject {
                            put("sessions", buildJsonArray { add(buildJsonObject { put("id", "c1"); put("title", "t") }) })
                        }.toString())
                        "/api/body/status" -> respond(200, """{"system":"linux","cpus":2}""")
                        else -> respond(404, """{"detail":"no route"}""")
                    }
                }
            }
        }

        companion object {
            const val SERVER_ID = "srvBodyId1234567"
        }
    }

    private companion object {
        fun respond(code: Int, text: String): MockResponse =
            MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(text)
    }
}
