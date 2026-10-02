package com.localaiagent.app.bridge

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.withTimeout

/**
 * One presence check of the PC. The bridge test reports success as `null` (no error) — which is
 * exactly what `withTimeoutOrNull` returns on a timeout, so the two were confused and the chip could
 * never turn "online". Here the outcome is a plain Boolean.
 */
object PresenceProbe {
    /** True when [test] answers within [timeoutMs] without an error (it returns null on success). */
    suspend fun isOnline(timeoutMs: Long, test: suspend () -> String?): Boolean = try {
        withTimeout(timeoutMs) { test() } == null
    } catch (e: TimeoutCancellationException) {
        false
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        false
    }
}
