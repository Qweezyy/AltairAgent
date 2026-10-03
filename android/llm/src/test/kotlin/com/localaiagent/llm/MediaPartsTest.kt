package com.localaiagent.llm

import com.localaiagent.core.LlmConfig
import com.localaiagent.core.Message
import com.localaiagent.core.Part
import com.localaiagent.core.Role
import com.localaiagent.llm.FakeSse.Companion.answer
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.util.Base64

/** Video, audio and documents reach the model as OpenAI "file" parts, the form GateYourWay passes on. */
class MediaPartsTest {

    private val sse = FakeSse()

    @Before
    fun setUp() { ProviderHealth.reset(); ConcurrencyLimiter.resetAll() }

    @After
    fun tearDown() { sse.close(); ProviderHealth.reset() }

    private fun probeVideo(): String {
        val bytes = javaClass.getResource("/probe.mp4")!!.readBytes()
        return "data:video/mp4;base64," + Base64.getEncoder().encodeToString(bytes)
    }

    @Test
    fun aFilePartGoesAsFileWithNameAndData() = runBlocking {
        sse.serve(answer("seen"))
        val video = probeVideo()
        val msg = Message(
            Role.USER, "What is on screen?",
            parts = listOf(Part.Image("data:image/png;base64,AAAA"), Part.File(video, "clip.mp4")),
        )
        val c = OpenAiCompatClient(LlmConfig(baseUrl = sse.baseUrl, model = "m", apiKey = "k", retryDelayScale = 0.01))
        c.complete(listOf(msg))
        c.close()
        val content = Json.parseToJsonElement(sse.requests.single()).jsonObject["messages"]!!
            .let { (it as JsonArray)[0].jsonObject["content"] as JsonArray }
        assertEquals(listOf("text", "image_url", "file"), content.map { it.jsonObject["type"]!!.jsonPrimitive.content })
        val file = content[2].jsonObject["file"]!!.jsonObject
        assertEquals("clip.mp4", file["filename"]!!.jsonPrimitive.content)
        assertEquals(video, file["file_data"]!!.jsonPrimitive.content)
    }

    /**
     * The real thing, run only with a key: GYW_KEY=… (GateYourWay) and optionally GYW_MODEL. Our client
     * sends a 5 s clip with on-screen text and a spoken word; the model must report both.
     */
    @Test
    fun liveGeminiWatchesAndHearsAVideo() = runBlocking {
        val key = System.getenv("GYW_KEY").orEmpty()
        assumeTrue("set GYW_KEY to run against GateYourWay", key.isNotBlank())
        val c = OpenAiCompatClient(
            LlmConfig(
                baseUrl = "https://api.gateyourway.com/v1", model = System.getenv("GYW_MODEL") ?: "gemini-3.8-flash",
                apiKey = key, reasoningEffort = Effort.PROVIDER,
            ),
        )
        val turn = c.complete(
            listOf(
                Message(
                    Role.USER, "Watch and listen to the attached video. What text is on screen and what code word is spoken?",
                    parts = listOf(Part.File(probeVideo(), "probe.mp4")),
                ),
            ),
        )
        c.close()
        val text = turn.content.lowercase()
        assertTrue(turn.content, "elephant" in text && "58" in text)
        assertTrue(turn.content, "pineapple" in text)
    }
}
