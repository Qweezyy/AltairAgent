package com.localaiagent.llm

import com.localaiagent.core.LlmConfig
import com.localaiagent.core.Message
import com.localaiagent.core.Role
import com.localaiagent.llm.FakeSse.Companion.answer
import com.localaiagent.llm.FakeSse.Companion.text
import com.localaiagent.llm.FakeSse.Reply
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/** The reliability layer against a real local SSE server, as the PC's tests/test_reliability.py. */
class ReliabilityTest {

    private val main = FakeSse()
    private val spare = FakeSse()
    private val ask = listOf(Message(Role.USER, "Hi"))

    @Before
    fun setUp() { ProviderHealth.reset(); ConcurrencyLimiter.resetAll() }

    @After
    fun tearDown() { main.close(); spare.close(); ProviderHealth.reset(); ConcurrencyLimiter.resetAll() }

    private fun cfg(
        sse: FakeSse = main,
        model: String = "m",
        effort: String = Effort.ADAPTIVE,
        retries: Int = 3,
        firstByteMs: Long = 90_000,
        slowMs: Long = 40_000,
        fallbacks: List<LlmConfig> = emptyList(),
        totalMs: Long = 120_000,
    ) = LlmConfig(
        baseUrl = sse.baseUrl, model = model, apiKey = "k", maxRetries = retries, reasoningEffort = effort,
        firstByteTimeoutMs = firstByteMs, slowFirstByteMs = slowMs, fallbacks = fallbacks,
        retryDelayScale = 0.01, timeoutMs = totalMs,
    )

    private suspend fun call(config: LlmConfig, shown: StringBuilder = StringBuilder(), escalate: Boolean = false) =
        OpenAiCompatClient(config).let { c ->
            try { c.complete(ask, onText = { shown.append(it) }, escalate = escalate) } finally { c.close() }
        }

    // ---------------------------------------------------------------- failures disguised as answers

    @Test
    fun gatewayErrorTextIsRetriedAndNeverShown() = runBlocking {
        main.serve(
            answer("The request could not be completed. Please retry later, or reduce the request parameters/content."),
            answer("A real answer."),
        )
        val shown = StringBuilder()
        val turn = call(cfg(), shown)
        assertEquals("A real answer.", turn.content)
        assertEquals("A real answer.", shown.toString())
        assertEquals(2, main.requests.size)
    }

    @Test
    fun emptyStreamIsAFailureNotAnAnswer() = runBlocking {
        main.serve(Reply(listOf(text("", "stop"))), answer("Now with text."))
        assertEquals("Now with text.", call(cfg()).content)
        assertEquals(2, main.requests.size)
    }

    @Test
    fun allEmptyAnswersEndInAnHonestError() = runBlocking {
        main.serve(Reply(listOf(text("", "stop"))), Reply(listOf(text("", "stop"))))
        try { call(cfg(retries = 2)); fail("an empty answer must not pass") } catch (e: RuntimeException) {
            assertTrue(e.message, e.message!!.contains("empty answer"))
        }
    }

    @Test
    fun unprocessedRequestIsRetried() = runBlocking {
        // ~10K tokens of text, but the provider says it read 100.
        val big = listOf(Message(Role.USER, "x".repeat(45_000)))
        main.serve(
            Reply(listOf(text("Short."), """{"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":100}}""")),
            Reply(listOf(text("Read it."), """{"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10000}}""")),
        )
        val c = OpenAiCompatClient(cfg())
        val turn = c.complete(big)
        c.close()
        assertEquals("Read it.", turn.content)
    }

    @Test
    fun classifierRules() {
        assertEquals(FakeAnswer.EMPTY, FakeAnswer.classify("  ", false, 0, 10))
        assertNull(FakeAnswer.classify("", true, 0, 10))
        assertEquals(FakeAnswer.GATEWAY, FakeAnswer.classify("Upstream request failed", false, 0, 10))
        // A long answer that mentions the phrase is a real answer.
        assertNull(FakeAnswer.classify("no healthy upstream ".repeat(30), false, 0, 10))
        assertNull(FakeAnswer.classify("ok", false, 100, 4000))
        assertEquals(FakeAnswer.UNPROCESSED, FakeAnswer.classify("ok", false, 100, 45_000))
    }

    // ---------------------------------------------------------------- timeouts

    @Test
    fun lateFirstByteDropsTheAttempt() = runBlocking {
        main.serve(answer("too late").copy(firstByteDelayMs = 1500), answer("in time"))
        assertEquals("in time", call(cfg(firstByteMs = 500)).content)
    }

    @Test
    fun aLongHealthyStreamIsNeverCut() = runBlocking {
        // Streams for ~3 s against an old-style 1 s whole-request limit: only silence may end it.
        val chunks = (1..12).map { text("part$it ") } + text("", "stop")
        main.serve(Reply(chunks, chunkDelayMs = 250))
        val turn = call(cfg(totalMs = 1000, firstByteMs = 1000))
        assertTrue(turn.content.startsWith("part1 ") && turn.content.contains("part12"))
        assertEquals(1, main.requests.size)
    }

    /** A config where 25K prompt tokens (100K characters) add 1 s to the first-byte wait. */
    private fun scaled(firstByteMs: Long, silenceMs: Long = 60_000) = LlmConfig(
        baseUrl = main.baseUrl, model = "m", apiKey = "k", maxRetries = 3, retryDelayScale = 0.01,
        firstByteTimeoutMs = firstByteMs, silenceTimeoutMs = silenceMs,
        firstBytePer100kTokensMs = 4_000, uploadBytesPerSec = 0,
    )

    @Test
    fun aBigPromptIsGivenTimeToBeRead() = runBlocking {
        // 100K characters ≈ 25K tokens → 0.5 s + 1 s allowed; the model answers after 1.2 s.
        main.serve(answer("read it all").copy(firstByteDelayMs = 1200))
        val big = listOf(Message(Role.USER, "x".repeat(100_000)))
        val c = OpenAiCompatClient(scaled(firstByteMs = 500))
        assertEquals("read it all", c.complete(big).content)
        c.close()
        assertEquals("one request, nothing billed twice", 1, main.requests.size)
    }

    @Test
    fun aSmallPromptStillHasTheShortWait() = runBlocking {
        main.serve(answer("too late").copy(firstByteDelayMs = 1200), answer("in time"))
        val c = OpenAiCompatClient(scaled(firstByteMs = 500))
        assertEquals("in time", c.complete(ask).content)
        c.close()
        assertEquals(2, main.requests.size)
    }

    @Test
    fun silentThinkingAfterTheHeadersIsNotCut() = runBlocking {
        // Headers at once, then 1 s of silence (the model thinks), then the answer: silence before the
        // first data counts against the first-byte budget, not the 0.3 s silence limit.
        main.serve(answer("thought it over").copy(dataDelayMs = 1000))
        val c = OpenAiCompatClient(scaled(firstByteMs = 3000, silenceMs = 300))
        assertEquals("thought it over", c.complete(ask).content)
        c.close()
        assertEquals(1, main.requests.size)
    }

    @Test
    fun aStreamThatStopsMidwayIsCutBySilence() = runBlocking {
        main.serve(
            Reply(listOf(text("Hel"), text("lo")), complete = true, chunkDelayMs = 900),
            answer("Hello again"),
        )
        val c = OpenAiCompatClient(scaled(firstByteMs = 3000, silenceMs = 300))
        assertEquals("Hello again", c.complete(ask).content)
        c.close()
        assertEquals(2, main.requests.size)
    }

    @Test
    fun aRefusalIsAnHonestErrorNotFiveBilledRetries() = runBlocking {
        main.serve(Reply(listOf("""{"choices":[{"delta":{},"finish_reason":"content_filter"}]}""")), answer("never asked"))
        try { call(cfg(retries = 5)); fail("a refusal must surface") } catch (e: RuntimeException) {
            assertTrue(e.message, e.message!!.contains("declined"))
        }
        assertEquals(1, main.requests.size)
        assertFalse(ProviderHealth.isSick(ProviderHealth.key(main.baseUrl, "m")))
    }

    @Test
    fun firstByteBudgetGrowsWithThePrompt() {
        // A small chat: about the base wait.
        assertEquals(90_000L + 24_000 + 1_000, firstByteBudgetMs(90_000, 120_000, 100_000, 20_000, 100_000))
        // 228K tokens with a 16 MB body (a video): over 8 minutes, under the 15-minute cap.
        val big = firstByteBudgetMs(90_000, 120_000, 100_000, 228_000, 16_000_000)
        assertTrue("$big", big in 480_000L..900_000L)
        assertEquals(15 * 60_000L, firstByteBudgetMs(90_000, 120_000, 100_000, 5_000_000, 100_000_000))
    }

    // ---------------------------------------------------------------- reasoning effort

    @Test
    fun effortDialects() {
        val or = effortField("https://openrouter.ai/api/v1", "z-ai/glm-5.3-flash", "high")!!
        assertEquals("reasoning", or.name)
        assertEquals("high", (or.value as JsonObject)["effort"]!!.jsonPrimitive.content)
        assertEquals("low", (effortField("https://openrouter.ai/api/v1", "x", "minimal")!!.value as JsonObject)["effort"]!!.jsonPrimitive.content)
        val gyw = effortField("https://api.gateyourway.com/v1", "glm-5.3-flash", "low")!!
        assertEquals("thinking", gyw.name)
        assertEquals("disabled", (gyw.value as JsonObject)["type"]!!.jsonPrimitive.content)
        assertEquals("enabled", (effortField("https://open.bigmodel.cn/api", "x", "high")!!.value as JsonObject)["type"]!!.jsonPrimitive.content)
        assertEquals("thinking", effortField("http://localhost:1234/v1", "glm-4", "low")!!.name)
        val oai = effortField("https://api.openai.com/v1", "gpt-5", "medium")!!
        assertEquals("reasoning_effort", oai.name)
        assertEquals(JsonPrimitive("medium"), oai.value)
        assertNull(effortField("https://api.openai.com/v1", "gpt-5", null))
        // Gemini ignores the Z.ai field even behind GateYourWay (measured): it gets reasoning_effort.
        val gem = effortField("https://api.gateyourway.com/v1", "gemini-3.8-flash", "low")!!
        assertEquals("reasoning_effort", gem.name)
        assertEquals(JsonPrimitive("low"), gem.value)
        assertEquals("reasoning", effortField("https://openrouter.ai/api/v1", "google/gemini-3.8-flash", "low")!!.name)
    }

    @Test
    fun adaptiveEffortIsLowAndHighAfterAFailedStep() {
        assertEquals("low", Effort.resolve(Effort.ADAPTIVE, escalate = false))
        assertEquals("high", Effort.resolve(Effort.ADAPTIVE, escalate = true))
        assertEquals("medium", Effort.resolve(Effort.MEDIUM, escalate = true))
        assertNull(Effort.resolve(Effort.PROVIDER, escalate = true))
    }

    @Test
    fun effortGoesIntoTheRequestBody() = runBlocking {
        main.serve(answer("a"), answer("b"))
        call(cfg())
        call(cfg(), escalate = true)
        assertTrue(main.requests[0], main.requests[0].contains("\"reasoning_effort\":\"low\""))
        assertTrue(main.requests[1], main.requests[1].contains("\"reasoning_effort\":\"high\""))
    }

    @Test
    fun providerDefaultSendsNoEffort() = runBlocking {
        main.serve(answer("a"))
        call(cfg(effort = Effort.PROVIDER))
        assertFalse(main.requests[0].contains("reasoning_effort"))
    }

    @Test
    fun aRefusedEffortFieldIsDroppedForGood() = runBlocking {
        main.serve(
            Reply(status = 400, errorBody = """{"error":"Unrecognized request argument: reasoning_effort"}"""),
            answer("without it"),
            answer("still without it"),
        )
        assertEquals("without it", call(cfg(retries = 1)).content)
        assertEquals("still without it", call(cfg(retries = 1)).content)
        assertTrue(main.requests[0].contains("reasoning_effort"))
        assertFalse(main.requests[1].contains("reasoning_effort"))
        assertFalse(main.requests[2].contains("reasoning_effort"))
    }

    // ---------------------------------------------------------------- runaway generation

    @Test
    fun runawayTwiceIsAnHonestErrorAndTheSecondTryThinksLess() = runBlocking {
        val endless = Reply(listOf(text("la ".repeat(60_000))), complete = false)
        main.serve(endless, endless, answer("never asked"))
        try { call(cfg(effort = Effort.HIGH)); fail("a runaway model must end in an error") } catch (e: RuntimeException) {
            assertTrue(e.message, e.message!!.contains("without end twice"))
        }
        assertEquals(2, main.requests.size)
        assertTrue(main.requests[0].contains("\"reasoning_effort\":\"high\""))
        assertTrue(main.requests[1].contains("\"reasoning_effort\":\"low\""))
    }

    // ---------------------------------------------------------------- fallback and breaker

    @Test
    fun aFailingMainModelHandsTheStepToTheFallback() = runBlocking {
        main.serve(Reply(status = 503, errorBody = "down"), Reply(status = 503, errorBody = "down"))
        spare.serve(answer("from the spare"), answer("spare again"))
        val config = cfg(retries = 5, fallbacks = listOf(cfg(sse = spare, model = "s")))
        assertEquals("from the spare", call(config).content)
        // Two attempts on the main, not five, while a fallback is behind it.
        assertEquals(2, main.requests.size)
        // Now the main is sick: the next step goes straight to the spare.
        assertEquals("spare again", call(config).content)
        assertEquals(2, main.requests.size)
    }

    @Test
    fun aSlowMainModelTwiceInARowIsSkippedThenTriedAgainLater() = runBlocking {
        var clock = 1_000_000L
        ProviderHealth.now = { clock }
        main.serve(
            answer("slow 1").copy(firstByteDelayMs = 400),
            answer("slow 2").copy(firstByteDelayMs = 400),
            answer("main is back"),
        )
        spare.serve(answer("spare"))
        val config = cfg(slowMs = 150, fallbacks = listOf(cfg(sse = spare, model = "s")))
        assertEquals("slow 1", call(config).content)
        assertEquals("slow 2", call(config).content)
        assertEquals("spare", call(config).content)
        clock += ProviderHealth.SICK_FOR_MS + 1
        assertEquals("main is back", call(config).content)
    }

    // ---------------------------------------------------------------- concurrency

    @Test
    fun concurrency429LowersTheLimitAndItGrowsBack() = runBlocking {
        val limiter = ConcurrencyLimiter()
        repeat(3) { limiter.acquire() }
        limiter.onConcurrencyLimited()
        assertEquals(2, limiter.limit)
        repeat(3) { limiter.release() }
        repeat(ConcurrencyLimiter.GROW_AFTER) { limiter.onSuccess() }
        assertEquals(3, limiter.limit)
    }

    @Test
    fun theLimiterReallyHoldsTheQueue() = runBlocking {
        val limiter = ConcurrencyLimiter()
        repeat(2) { limiter.acquire() }
        limiter.onConcurrencyLimited() // limit 1, two still in flight
        var entered = false
        val waiter = async { limiter.acquire(); entered = true }
        delay(100)
        limiter.release()
        delay(100)
        assertFalse("one still in flight, the limit is one", entered)
        limiter.release()
        withTimeout(2000) { waiter.await() }
        assertTrue(entered)
    }

    @Test
    fun a429AboutConcurrencyIsRecognised() = runBlocking {
        assertTrue(isConcurrencyLimit(429, """{"error":"Too many concurrent requests"}"""))
        assertFalse(isConcurrencyLimit(429, """{"error":"Rate limit exceeded: 60 per minute"}"""))
        main.serve(Reply(status = 429, errorBody = "Too many concurrent requests for this key"), answer("ok"))
        assertEquals("ok", call(cfg()).content)
        assertEquals(1, ConcurrencyLimiter.forUrl(main.baseUrl).limit)
    }
}
