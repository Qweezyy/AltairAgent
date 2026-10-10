package com.localaiagent.app.servers

import okhttp3.OkHttpClient
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * A server's door has a self-signed certificate: it is trusted only when its SHA-256 equals the
 * fingerprint from the QR (PHONE_SERVER_SPEC §3.1). The pin is the server's identity; there is no
 * "trust all" fallback, ever.
 */
object CertPin {
    fun sha256Hex(der: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

    /** Whether a certificate is the paired one: the whole hex compared in constant time. */
    fun matches(der: ByteArray, fingerprint: String): Boolean {
        val want = fingerprint.trim().lowercase().replace(":", "")
        if (want.length != 64) return false
        return MessageDigest.isEqual(sha256Hex(der).toByteArray(), want.toByteArray())
    }

    fun trustManager(fingerprint: String): X509TrustManager = object : X509TrustManager {
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            val leaf = chain.firstOrNull() ?: throw CertificateException("no certificate")
            if (!matches(leaf.encoded, fingerprint)) throw CertificateException("not the paired server")
        }

        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
            throw CertificateException("client certificates are not used")

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    /** A client that talks only to the server whose certificate matches [fingerprint]. */
    fun client(fingerprint: String, base: OkHttpClient.Builder = OkHttpClient.Builder()): OkHttpClient {
        val tm = trustManager(fingerprint)
        val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }
        return base
            .sslSocketFactory(ssl.socketFactory, tm)
            // The host can be an IP or a Tailscale name; the pin above is the identity check.
            .hostnameVerifier { _, _ -> true }
            .build()
    }
}
