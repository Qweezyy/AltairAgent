package com.localaiagent.app.servers

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** A server chat built from the PC's `/ws` events, as the server sends them. */
class ServerChatReducerTest {

    private fun ev(s: String): JsonObject = Json.parseToJsonElement(s).jsonObject

    private fun run(start: ServerChatState, vararg events: String) =
        events.fold(start) { st, e -> ServerChatReducer.apply(st, ev(e)) }

    @Test
    fun aReopenedChatShowsItsStoredCourse() {
        val s = run(
            ServerChatState(chatId = "c1"),
            """{"type":"ready","session_id":"fresh"}""",
            """{"type":"session.loaded","running":false,"session":{"id":"c1","title":"Deploy","timeline":[
                {"kind":"user","text":"deploy it"},
                {"kind":"text","text":"Checking the repo."},
                {"kind":"step","name":"run_shell","args":{"cmd":"git pull"},"ok":true,"output":"Already up to date.","duration_ms":120},
                {"kind":"answer","text":"Deployed.","full":"Checking the repo. Deployed."},
                {"kind":"wake","text":"quiet","quiet":true},
                {"kind":"error","text":"later failure"}
            ]}}""",
        )
        assertEquals("c1", s.chatId) // the ready of a fresh socket does not replace the chat asked for
        assertEquals("Deploy", s.title)
        assertTrue(s.connected)
        assertEquals(
            listOf(
                ServerChatItem.User("deploy it"),
                ServerChatItem.Assistant("Checking the repo.", final = false),
                ServerChatItem.Tool("", "run_shell", "cmd=git pull", true, "Already up to date.", 120),
                ServerChatItem.Assistant("Deployed.", final = true),
                ServerChatItem.Note("later failure", error = true),
            ),
            s.items,
        )
    }

    @Test
    fun aRunStreamsToolsAndTheAnswer() {
        var s = ServerChatReducer.sent(ServerChatState(chatId = "c1", connected = true), "list files")
        assertTrue(s.running)
        s = run(
            s,
            """{"type":"run.started","run_id":"r1"}""",
            """{"type":"text.delta","text":"Let me "}""",
            """{"type":"text.delta","text":"look."}""",
            """{"type":"tool.started","call_id":"t1","name":"list_dir","args":{"path":"."}}""",
            """{"type":"tool.finished","call_id":"t1","name":"list_dir","ok":true,"output":"a.txt","duration_ms":5}""",
            """{"type":"text.delta","text":"One file: a.txt"}""",
        )
        assertEquals("One file: a.txt", s.streaming)
        s = run(s, """{"type":"run.finished","run_id":"r1","text":"Let me look.One file: a.txt","cost_usd":0.002}""")
        assertFalse(s.running)
        assertEquals("", s.streaming)
        assertEquals(
            listOf(
                ServerChatItem.User("list files"),
                ServerChatItem.Assistant("Let me look.", final = false),
                ServerChatItem.Tool("t1", "list_dir", "path=.", true, "a.txt", 5),
                ServerChatItem.Assistant("One file: a.txt", final = true),
            ),
            s.items,
        )
        assertEquals(0.002, s.costUsd, 1e-9)
    }

    @Test
    fun anApprovalAndAQuestionAreAskedAndCleared() {
        var s = run(
            ServerChatState(chatId = "c1"),
            """{"type":"state","state":"waiting_approval"}""",
            """{"type":"approval.requested","request_id":"a1","name":"run_shell","reason":"deletes files","args":{"cmd":"rm -rf build"}}""",
        )
        assertTrue(s.running)
        assertEquals(ServerApproval("a1", "run_shell", "deletes files", "cmd=rm -rf build"), s.approval)
        s = run(s, """{"type":"approval.resolved","request_id":"other","approved":true}""")
        assertEquals("a1", s.approval?.requestId)
        s = run(s, """{"type":"approval.resolved","request_id":"a1","approved":true}""")
        assertNull(s.approval)

        s = run(s, """{"type":"question.asked","request_id":"q1","questions":[
            {"question":"Which branch?","kind":"single","options":[{"label":"main","recommended":true},{"label":"dev","description":"risky"}]},
            {"question":"Extras?","kind":"multiple","options":[{"label":"tests"},{"label":"lint"}]}]}""")
        val q = s.question!!
        assertEquals("q1", q.requestId)
        assertFalse(q.items[0].multiple)
        assertTrue(q.items[1].multiple)
        assertTrue(q.items[0].options[0].recommended)
        assertEquals("risky", q.items[0].options[1].description)
        s = run(s, """{"type":"run.failed","run_id":"r","message":"model is down"}""")
        assertNull(s.question)
        assertFalse(s.running)
        assertEquals(ServerChatItem.Note("model is down", error = true), s.items.last())
    }

    @Test
    fun joiningMidRunKeepsWhatWasStreamedAndUnknownFinishes() {
        val s = run(
            ServerChatState(chatId = "c1"),
            """{"type":"session.loaded","running":true,"session":{"id":"c1","title":"t","timeline":[]}}""",
            """{"type":"state","state":"running"}""",
            """{"type":"text.delta","text":"halfway"}""",
            """{"type":"tool.finished","call_id":"zz","name":"grep","ok":false,"output":"boom","duration_ms":1}""",
        )
        assertTrue(s.running)
        assertEquals(ServerChatItem.Tool("zz", "grep", "", false, "boom"), s.items.last())
        assertEquals("halfway", s.streaming)
    }

    @Test
    fun otherChatsTitlesAndUnknownEventsChangeNothing() {
        val start = ServerChatState(chatId = "c1", title = "mine")
        assertEquals(start, run(start, """{"type":"session.title","session_id":"c2","title":"theirs"}"""))
        assertEquals(start, run(start, """{"type":"chat.activity","session_id":"c2"}"""))
        assertEquals(start, run(start, """{"type":"log","level":"info","text":"folder set"}"""))
        assertEquals("renamed", run(start, """{"type":"session.title","session_id":"c1","title":"renamed"}""").title)
        assertTrue(run(start, """{"type":"session.missing","session_id":"c1"}""").missing)
    }

    @Test
    fun modelRetriesAreShownUntilTheRunMovesOn() {
        var s = run(
            ServerChatState(chatId = "c1", running = true),
            """{"type":"reconnecting","attempt":2,"max_attempts":5,"delay_s":2.9,"reason":"503"}""",
        )
        assertEquals(2 to 5, s.retry)
        s = run(s, """{"type":"reconnecting","attempt":3,"max_attempts":5}""")
        assertEquals(3 to 5, s.retry)
        assertNull(run(s, """{"type":"text.delta","text":"ok"}""").retry)
        assertNull(run(s, """{"type":"run.failed","run_id":"r","message":"down"}""").retry)
    }

    @Test
    fun longArgumentsAreShortened() {
        val line = ServerChatReducer.argsLine(ev("""{"content":"${"x".repeat(300)}","path":"a\nb"}"""))
        assertTrue(line.length <= 240)
        assertTrue(line.startsWith("content=" + "x".repeat(80) + "…"))
        assertTrue(line.contains("path=a b"))
    }
}
