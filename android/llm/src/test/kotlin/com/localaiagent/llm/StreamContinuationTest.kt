package com.localaiagent.llm

import com.localaiagent.core.LlmConfig
import com.localaiagent.core.Message
import com.localaiagent.core.Role
import com.localaiagent.llm.FakeSse.Companion.text
import com.localaiagent.llm.FakeSse.Reply
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * A real OpenAI-style SSE server on a local socket that cuts the stream in the middle of an answer,
 * the way a flaky mobile connection does. The client has to continue the same answer, not restart it.
 */
class StreamContinuationTest {

    private val sse = FakeSse()
    private val requests get() = sse.requests

    // Longer than the held-back opening, so it reaches the screen before the break.
    private val opening = "Hello! " + "This is a long opening of the answer. ".repeat(6)

    @Before
    fun setUp() { ProviderHealth.reset(); ConcurrencyLimiter.resetAll() }

    @After
    fun tearDown() { sse.close(); ProviderHealth.reset() }

    private fun client() = OpenAiCompatClient(
        LlmConfig(baseUrl = sse.baseUrl, model = "m", apiKey = "k", maxRetries = 3, retryDelayScale = 0.01),
    )

    @Test
    fun aBrokenAnswerIsContinuedNotRestarted() = runBlocking {
        sse.serve(
            Reply(listOf(text(opening), text("wor")), complete = false),
            Reply(listOf(text("ld!"), text("", finish = "stop"))),
        )
        val streamed = StringBuilder()
        var retries = 0
        var discarded = 0
        val llm = client()
        val turn = llm.complete(
            messages = listOf(Message(Role.USER, "Say hello")),
            onText = { streamed.append(it) },
            onRetry = { _, _, _, _ -> retries++ },
            onDiscard = { discarded += it },
        )
        llm.close()

        // Past the held-back opening, "wor" was on screen, and the answer goes on from it.
        assertEquals(opening + "world!", turn.content)
        // The user saw every piece once, in order: nothing was repeated or taken back.
        assertEquals(opening + "world!", streamed.toString())
        assertEquals(1, retries)
        assertEquals(0, discarded)
        assertEquals(2, requests.size)
        // The second request hands the model its own partial answer and asks it to go on.
        val second = requests[1]
        assertTrue(second, second.contains("This is a long opening of the answer."))
        assertTrue(second, second.contains("Continue it exactly from where it stopped"))
        assertFalse(requests[0].contains("Continue it exactly"))
    }

    @Test
    fun aBrokenShortOpeningIsSimplyAskedAgain() = runBlocking {
        // Nothing reached the screen yet (the opening is held back): a plain retry, no continuation.
        sse.serve(
            Reply(listOf(text("Hello, wor")), complete = false),
            Reply(listOf(text("Hello, world!"), text("", finish = "stop"))),
        )
        val streamed = StringBuilder()
        val llm = client()
        val turn = llm.complete(listOf(Message(Role.USER, "Say hello")), onText = { streamed.append(it) })
        llm.close()
        assertEquals("Hello, world!", turn.content)
        assertEquals("Hello, world!", streamed.toString())
        assertFalse(requests[1].contains("Continue it exactly"))
    }

    @Test
    fun aBrokenToolCallStartsTheAnswerOver() = runBlocking {
        val partialCall = """{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",""" +
            """"function":{"name":"read_file","arguments":"{\"pa"}}]},"finish_reason":null}]}"""
        sse.serve(
            Reply(listOf(text(opening), partialCall), complete = false),
            Reply(listOf(text("Done.", finish = "stop"))),
        )
        var discarded = 0
        val llm = client()
        val turn = llm.complete(
            messages = listOf(Message(Role.USER, "Check the file")),
            onDiscard = { discarded += it },
        )
        llm.close()

        assertEquals("Done.", turn.content)
        assertTrue(turn.toolCalls.isEmpty())
        assertEquals(opening.length, discarded)
        assertFalse(requests[1].contains("Continue it exactly"))
    }

    @Test
    fun jsonNullFieldsInChunksAreIgnoredNotFatal() = runBlocking {
        // As real providers send them: usage/reasoning/tool_calls/function as null, a null delta,
        // and the reasoning text under reasoning_content while "reasoning" is null.
        sse.serve(
            Reply(
                listOf(
                    """{"choices":[{"delta":{"content":"Hel","reasoning":null,"reasoning_content":"think",""" +
                        """"tool_calls":null},"finish_reason":null}],"usage":null}""",
                    """{"choices":[{"delta":{"content":"lo","tool_calls":[{"index":0,"function":null}]},""" +
                        """"finish_reason":null}],"usage":null}""",
                    """{"choices":[{"delta":null,"finish_reason":"stop"}],"usage":{"prompt_tokens":3,""" +
                        """"completion_tokens":2,"total_tokens":5,"prompt_tokens_details":{"cached_tokens":2}}}""",
                    """{"choices":null,"usage":null}""",
                ),
            ),
        )
        var retries = 0
        val reasoning = StringBuilder()
        val llm = client()
        val turn = llm.complete(
            messages = listOf(Message(Role.USER, "Hi")),
            onReasoning = { reasoning.append(it) },
            onRetry = { _, _, _, _ -> retries++ },
        )
        llm.close()
        assertEquals("Hello", turn.content)
        assertEquals("think", reasoning.toString())
        assertEquals(5, turn.usage.totalTokens)
        assertEquals(2, turn.usage.cachedTokens)
        assertTrue(turn.toolCalls.isEmpty())
        assertEquals(0, retries)
    }

    @Test
    fun aStreamThatEndsProperlyIsNotRetried() = runBlocking {
        sse.serve(Reply(listOf(text("All good.", finish = "stop"))))
        var retries = 0
        val llm = client()
        val turn = llm.complete(
            messages = listOf(Message(Role.USER, "Hi")),
            onRetry = { _, _, _, _ -> retries++ },
        )
        llm.close()
        assertEquals("All good.", turn.content)
        assertEquals(0, retries)
        assertEquals(1, requests.size)
    }
}
