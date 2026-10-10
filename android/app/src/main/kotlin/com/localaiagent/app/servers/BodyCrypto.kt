package com.localaiagent.app.servers

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * The phone's identity toward servers (PHONE_SERVER_SPEC §2–3): an Ed25519 key, the body id derived
 * from its public key, the card the server stores, and the signed sign-in message. Pure functions,
 * checked against the vectors the PC's tests pin.
 */
object BodyCrypto {
    /** A new 32-byte private key seed. */
    fun newSeed(): ByteArray = ByteArray(32).also { SecureRandom().nextBytes(it) }

    /** The raw 32-byte public key of a seed. */
    fun publicKey(seed: ByteArray): ByteArray = Ed25519PrivateKeyParameters(seed, 0).generatePublicKey().encoded

    /** Body id: the first 16 characters of base64url(SHA-256(raw public key)). */
    fun bodyId(publicKey: ByteArray): String =
        Base64.getUrlEncoder().encodeToString(MessageDigest.getInstance("SHA-256").digest(publicKey)).take(16)

    /** What the phone signs to sign in: the nonce and the server's body id it was paired with. */
    fun authMessage(nonce: String, verifier: String): String = "altair-body-auth:v1:$nonce:$verifier"

    /** Ed25519 signature of a UTF-8 message, standard base64. */
    fun sign(seed: ByteArray, message: String): String {
        val signer = Ed25519Signer()
        signer.init(true, Ed25519PrivateKeyParameters(seed, 0))
        val bytes = message.toByteArray(Charsets.UTF_8)
        signer.update(bytes, 0, bytes.size)
        return Base64.getEncoder().encodeToString(signer.generateSignature())
    }

    /** The card the server stores for this phone. */
    fun card(seed: ByteArray, deviceName: String): JsonObject {
        val pub = publicKey(seed)
        return buildJsonObject {
            put("id", bodyId(pub))
            put("name", deviceName.ifBlank { "Phone" })
            put("kind", "phone")
            put("public_key", Base64.getEncoder().encodeToString(pub))
        }
    }
}
