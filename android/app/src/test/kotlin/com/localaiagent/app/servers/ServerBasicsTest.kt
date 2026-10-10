package com.localaiagent.app.servers

import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

/** The phone-as-a-body basics against the PC's contract (PHONE_SERVER_SPEC §1–3, §6, §9). */
class ServerBasicsTest {

    private val fp = "ab".repeat(32)

    // ---------------------------------------------------------------- §9 test vectors (match Python)

    @Test
    fun bodyIdOfAFixedPublicKey() {
        assertEquals("riFsLvUkejeCwTXv", BodyCrypto.bodyId(ByteArray(32) { (it + 1).toByte() }))
    }

    @Test
    fun keyIdAndSignatureOfAFixedSeed() {
        val seed = ByteArray(32) { it.toByte() }
        val pub = BodyCrypto.publicKey(seed)
        assertEquals("A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=", Base64.getEncoder().encodeToString(pub))
        assertEquals("Vkdap1RjR0wChd9d", BodyCrypto.bodyId(pub))
        val message = BodyCrypto.authMessage("NONCE123", "riFsLvUkejeCwTXv")
        assertEquals("altair-body-auth:v1:NONCE123:riFsLvUkejeCwTXv", message)
        assertEquals(
            "HT1CqEH/n4RH36kQmXLrIuoO4vqO2IOd8EIAaJc4SaTf8VoisqjMln6NdR+LfizRB2WFEfHKWMjSW48yw+cHBg==",
            BodyCrypto.sign(seed, message),
        )
    }

    @Test
    fun theCardCarriesTheIdOfItsOwnKey() {
        val seed = ByteArray(32) { it.toByte() }
        val card = BodyCrypto.card(seed, "Pixel 8")
        assertEquals("Vkdap1RjR0wChd9d", card["id"]!!.jsonPrimitive.content)
        assertEquals("phone", card["kind"]!!.jsonPrimitive.content)
        assertEquals("A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=", card["public_key"]!!.jsonPrimitive.content)
        // A fresh key gives a different id.
        val other = BodyCrypto.card(BodyCrypto.newSeed(), "x")
        assertTrue(other["id"]!!.jsonPrimitive.content != "Vkdap1RjR0wChd9d")
    }

    // ---------------------------------------------------------------- the link

    @Test
    fun aFullLinkParses() {
        val link = parseServerLink(
            "altair://body?n=test-vps&s=srv1&b=Ab_cD-12efGH34ij&c=K7M2PQ9X" +
                "&u=http%3A%2F%2F192.168.1.5%3A8765&t=tok%2B%2F%3D&d=https%3A%2F%2F203.0.113.7%3A8443&fp=$fp",
        )!!
        assertEquals("test-vps", link.name)
        assertEquals("srv1", link.serverId)
        assertEquals("Ab_cD-12efGH34ij", link.bodyId)
        assertEquals("K7M2PQ9X", link.code)
        assertEquals("http://192.168.1.5:8765", link.relayUrl)
        assertEquals("tok+/=", link.relayToken)
        assertEquals("https://203.0.113.7:8443", link.directUrl)
        assertEquals(fp, link.fingerprint)
        assertTrue(link.hasRelay && link.hasDirect)
    }

    @Test
    fun oneRouteIsEnough() {
        assertTrue(parseServerLink("altair://body?n=a&s=s&b=b&c=K7M2PQ9X&u=http%3A%2F%2Fpc%3A1&t=t")!!.hasRelay)
        assertTrue(parseServerLink("altair://body?s=s&b=b&c=K7M2PQ9X&d=https%3A%2F%2Fh%3A1&fp=$fp")!!.hasDirect)
    }

    @Test
    fun brokenOrForeignLinksAreRefused() {
        assertNull(parseServerLink("altair://pair?u=x&t=y"))
        assertNull(parseServerLink("https://body?s=s&b=b&c=K7M2PQ9X&u=x&t=y"))
        assertNull(parseServerLink("altair://body?s=s&b=b&c=K7M2PQ9X")) // no route
        assertNull(parseServerLink("altair://body?s=s&b=b&c=k7m2pq9I&u=x&t=y")) // I is not in the alphabet
        assertNull(parseServerLink("altair://body?s=s&b=b&c=K7M2PQ9X&d=https%3A%2F%2Fh&fp=123")) // short pin
        assertNull(parseServerLink("altair://body?s=s&b=b&c=K7M2PQ9X&d=http%3A%2F%2Fh&fp=$fp")) // not TLS
        assertNull(parseServerLink("altair://body?b=b&c=K7M2PQ9X&u=x&t=y")) // no server id
        assertNull(parseServerLink(""))
    }

    // ---------------------------------------------------------------- the pin

    @Test
    fun thePinComparesTheWholeFingerprint() {
        val der = "a certificate".toByteArray()
        val good = CertPin.sha256Hex(der)
        assertTrue(CertPin.matches(der, good))
        assertTrue(CertPin.matches(der, good.uppercase()))
        assertFalse(CertPin.matches(der, fp))
        assertFalse(CertPin.matches(der, good.dropLast(2)))
        assertFalse(CertPin.matches(der, ""))
    }

    @Test(expected = java.security.cert.CertificateException::class)
    fun aCertificateOfAnotherServerIsRefused() {
        val tm = CertPin.trustManager(fp)
        val cert = java.security.cert.CertificateFactory.getInstance("X.509")
            .generateCertificate(java.io.ByteArrayInputStream(Base64.getDecoder().decode(SELF_SIGNED))) as java.security.cert.X509Certificate
        tm.checkServerTrusted(arrayOf(cert), "RSA")
    }

    @Test
    fun theRightCertificateIsTrusted() {
        val cert = java.security.cert.CertificateFactory.getInstance("X.509")
            .generateCertificate(java.io.ByteArrayInputStream(Base64.getDecoder().decode(SELF_SIGNED))) as java.security.cert.X509Certificate
        CertPin.trustManager(CertPin.sha256Hex(cert.encoded)).checkServerTrusted(arrayOf(cert), "RSA")
    }

    // ---------------------------------------------------------------- notices

    private fun notice(body: String, at: Double = 1791404705.9) = buildJsonObject {
        put("type", "notice"); put("kind", "run.finished"); put("level", "info")
        put("title", "test-vps: done"); put("text", "ok"); put("chat", "c1"); put("body_id", body); put("at", at)
    }

    @Test
    fun theSameEventThroughThePcAndTheServerNotifiesOnce() {
        val dedupe = NoticeDedupe()
        val viaServer = ServerNotice.parse(notice("self"), selfServerId = "srv1")!!
        val viaPc = ServerNotice.parse(notice("srv1"), selfServerId = null)!!
        assertEquals(viaServer.key, viaPc.key)
        assertTrue(dedupe.firstTime(viaServer))
        assertFalse(dedupe.firstTime(viaPc))
        assertTrue(dedupe.firstTime(ServerNotice.parse(notice("srv1", at = 1791404800.0), null)!!))
    }

    @Test
    fun aSelfNoticeWithoutAKnownServerIsDropped() {
        assertNull(ServerNotice.parse(notice("self"), selfServerId = null))
    }

    @Test
    fun theDedupeMemoryIsBounded() {
        val dedupe = NoticeDedupe(capacity = 3)
        (1..5).forEach { assertTrue(dedupe.firstTime(ServerNotice.parse(notice("s", at = it.toDouble()), null)!!)) }
        assertEquals(3, dedupe.snapshot().size)
        // The oldest fell out and counts as new again; a recent one is still a repeat.
        assertTrue(dedupe.firstTime(ServerNotice.parse(notice("s", at = 1.0), null)!!))
        assertFalse(dedupe.firstTime(ServerNotice.parse(notice("s", at = 5.0), null)!!))
    }

    private companion object {
        // A throwaway self-signed certificate (CN=altair-test), DER, base64.
        const val SELF_SIGNED =
            "MIIBgTCCASegAwIBAgIUC0IMQ0UG8xGnHBK/ApY3V045j1gwCgYIKoZIzj0EAwIwFjEUMBIGA1UEAwwLYWx0YWlyLX" +
                "Rlc3QwHhcNMjYxMDEwMDkzNzU1WhcNMzYxMDA3MDkzNzU1WjAWMRQwEgYDVQQDDAthbHRhaXItdGVzdDBZMBMGByqG" +
                "SM49AgEGCCqGSM49AwEHA0IABHeR/KlSSRkiIYpu8iPybV2r0F2A8PKW1qoX0dAUpy6PgiZBW7H/1CR8wWwkYxjfSW" +
                "vDQlEjqFJnmmqrTclNDEKjUzBRMB0GA1UdDgQWBBR7BYr3jqe/3Zw+B51yqGq8MZuwxTAfBgNVHSMEGDAWgBR7BYr3" +
                "jqe/3Zw+B51yqGq8MZuwxTAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMCA0gAMEUCIQCSdw6vuzbHgbtMxDAqN+" +
                "AkzbmHmXyIuLM55E6OI+iWsQIgBIqJpPbWnEV5hBpvxqksjVMn4V2YCT/D+ajZArvbLJY="
    }
}
