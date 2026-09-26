package com.localaiagent.core.tools

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class SecretRedactionTest {
    @Test
    fun masksEchoedSecret() {
        val out = redactSecrets("token=sk-live-12345 ok", mapOf("OPENAI" to "sk-live-12345"))
        assertEquals("token={{secret:OPENAI}} ok", out)
    }

    @Test
    fun masksEveryOccurrence() {
        val out = redactSecrets("a abcd b abcd", mapOf("K" to "abcd"))
        assertEquals("a {{secret:K}} b {{secret:K}}", out)
    }

    @Test
    fun longerSecretMaskedWholeBeforeShorterOne() {
        // "abcd" is a substring of "abcdef": the longer value must win, otherwise the output
        // would leak "ef" next to a mask.
        val out = redactSecrets("x=abcdef", linkedMapOf("SHORT" to "abcd", "LONG" to "abcdef"))
        assertEquals("x={{secret:LONG}}", out)
    }

    @Test
    fun veryShortValuesAreLeftAlone() {
        // Masking "1" would shred ordinary output like line numbers.
        val out = redactSecrets("line 1 of 10", mapOf("PIN" to "1"))
        assertEquals("line 1 of 10", out)
    }

    @Test
    fun noSecretValueSurvivesInEnvDump() {
        val env = "HOME=/data\nAPI_KEY=ghp_AbCdEf123456\nPATH=/bin"
        val out = redactSecrets(env, mapOf("GH" to "ghp_AbCdEf123456"))
        assertFalse(out.contains("ghp_AbCdEf123456"))
    }
}
