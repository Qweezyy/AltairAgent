package com.localaiagent.app.servers

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive

/** One line of a server chat as the phone shows it. */
sealed class ServerChatItem {
    data class User(val text: String) : ServerChatItem()
    /** The agent's words: an intermediate part before a tool, or the final answer. */
    data class Assistant(val text: String, val final: Boolean) : ServerChatItem()
    data class Tool(
        val callId: String, val name: String, val args: String,
        val ok: Boolean? = null, val output: String = "", val durationMs: Long = 0,
    ) : ServerChatItem()
    data class Note(val text: String, val error: Boolean = false) : ServerChatItem()
}

data class ServerApproval(val requestId: String, val name: String, val reason: String, val args: String)

data class ServerQuestion(val requestId: String, val items: List<Item>) {
    data class Item(val question: String, val multiple: Boolean, val options: List<Option>)
    data class Option(val label: String, val description: String, val recommended: Boolean)
}

/** A server chat: what the `/ws` events (the PC's protocol) have built so far. */
data class ServerChatState(
    val chatId: String? = null,
    val title: String = "",
    val items: List<ServerChatItem> = emptyList(),
    /** The answer streaming in right now. */
    val streaming: String = "",
    val running: Boolean = false,
    val state: String = "idle",
    val approval: ServerApproval? = null,
    val question: ServerQuestion? = null,
    val connected: Boolean = false,
    val missing: Boolean = false,
    val costUsd: Double = 0.0,
    /** The model did not answer and the server tries again: (attempt, of how many). */
    val retry: Pair<Int, Int>? = null,
)

/**
 * Applies the server's `/ws` events to a chat. Pure: the socket feeds it, tests drive it directly.
 * Events of other chats (the socket also carries chat-list news) leave the state as it is.
 */
object ServerChatReducer {
    private val busyStates = setOf("running", "waiting_approval", "compacting", "thinking")

    fun apply(s: ServerChatState, e: JsonObject): ServerChatState {
        fun str(k: String) = e[k]?.jsonPrimitive?.contentOrNull.orEmpty()
        return when (str("type")) {
            "ready" -> s.copy(connected = true, chatId = s.chatId ?: str("session_id").ifBlank { null })
            "session.loaded" -> {
                val session = e["session"] as? JsonObject ?: return s
                s.copy(
                    chatId = session["id"]?.jsonPrimitive?.contentOrNull ?: s.chatId,
                    title = session["title"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                    items = fromTimeline(session["timeline"] as? JsonArray),
                    streaming = "",
                    running = e["running"]?.jsonPrimitive?.booleanOrNull ?: false,
                    missing = false,
                )
            }
            "session.missing" -> s.copy(missing = true)
            "session.title" -> if (str("session_id") == s.chatId) s.copy(title = str("title")) else s
            "run.started" -> s.copy(running = true, state = "running")
            "text.delta" -> s.copy(streaming = s.streaming + str("text"), retry = null)
            "reconnecting" -> s.copy(retry = (int(e, "attempt") ?: 0) to (int(e, "max_attempts") ?: 0))
            "tool.started" -> flush(s).let {
                it.copy(retry = null, items = it.items + ServerChatItem.Tool(str("call_id"), str("name"), argsLine(e["args"])))
            }
            "tool.finished" -> {
                val id = str("call_id")
                var hit = false
                val items = s.items.map {
                    if (it is ServerChatItem.Tool && it.callId == id && it.ok == null) {
                        hit = true
                        it.copy(ok = e["ok"]?.jsonPrimitive?.booleanOrNull ?: false, output = str("output").take(4000),
                            durationMs = e["duration_ms"]?.jsonPrimitive?.contentOrNull?.toLongOrNull() ?: 0)
                    } else it
                }
                // A finish without its start (we joined mid-run): show it anyway.
                s.copy(items = if (hit) items else items + ServerChatItem.Tool(id, str("name"), "",
                    e["ok"]?.jsonPrimitive?.booleanOrNull ?: false, str("output").take(4000)))
            }
            "run.finished" -> {
                val text = s.streaming.ifBlank { str("text") }.trim()
                val items = if (text.isNotEmpty()) s.items + ServerChatItem.Assistant(text, final = true) else s.items
                s.copy(items = items, streaming = "", running = false, state = "idle", approval = null, question = null, retry = null,
                    costUsd = s.costUsd + (e["cost_usd"]?.jsonPrimitive?.doubleOrNull ?: 0.0))
            }
            "run.failed" -> flush(s).let {
                it.copy(items = it.items + ServerChatItem.Note(str("message"), error = true),
                    running = false, state = "idle", approval = null, question = null, retry = null)
            }
            "run.cancelled" -> flush(s).copy(running = false, state = "idle", approval = null, question = null, retry = null)
            "state" -> str("state").let { st -> s.copy(state = st, running = st in busyStates) }
            "approval.requested" -> s.copy(
                approval = ServerApproval(str("request_id"), str("name"), str("reason"), argsLine(e["args"])),
            )
            "approval.resolved" -> if (s.approval?.requestId == str("request_id")) s.copy(approval = null) else s
            "question.asked" -> s.copy(question = parseQuestion(str("request_id"), e["questions"] as? JsonArray))
            "log" -> when (str("level")) {
                "error", "warning" -> s.copy(items = s.items + ServerChatItem.Note(str("text"), error = str("level") == "error"))
                else -> s
            }
            "show_image", "show_file" -> s.copy(items = s.items + ServerChatItem.Note("📎 " + str("name").ifBlank { str("path") }))
            else -> s
        }
    }

    private fun int(e: JsonObject, k: String): Int? = e[k]?.jsonPrimitive?.contentOrNull?.toDoubleOrNull()?.toInt()

    /** What the user sent, shown before the server answers. */
    fun sent(s: ServerChatState, text: String): ServerChatState =
        s.copy(items = s.items + ServerChatItem.User(text), running = true, state = "running")

    private fun flush(s: ServerChatState): ServerChatState {
        val text = s.streaming.trim()
        return if (text.isEmpty()) s.copy(streaming = "")
        else s.copy(items = s.items + ServerChatItem.Assistant(text, final = false), streaming = "")
    }

    /** The chat's stored course (`session.timeline`): what a reopened chat looks like on the PC too. */
    fun fromTimeline(timeline: JsonArray?): List<ServerChatItem> = timeline.orEmpty().mapNotNull { el ->
        val o = el as? JsonObject ?: return@mapNotNull null
        fun str(k: String) = o[k]?.jsonPrimitive?.contentOrNull.orEmpty()
        when (str("kind")) {
            "user" -> ServerChatItem.User(str("text"))
            "text" -> ServerChatItem.Assistant(str("text"), final = false)
            "answer" -> str("text").takeIf { it.isNotBlank() }?.let { ServerChatItem.Assistant(it, final = true) }
            "step" -> ServerChatItem.Tool("", str("name"), argsLine(o["args"]),
                o["ok"]?.jsonPrimitive?.booleanOrNull ?: false, str("output"),
                o["duration_ms"]?.jsonPrimitive?.contentOrNull?.toLongOrNull() ?: 0)
            "error" -> ServerChatItem.Note(str("text"), error = true)
            "wake" -> if (o["quiet"]?.jsonPrimitive?.booleanOrNull == true) null else ServerChatItem.Note("⏰ " + str("text"))
            "image", "media", "file" -> ServerChatItem.Note("📎 " + str("name").ifBlank { str("path") })
            else -> null
        }
    }

    private fun parseQuestion(id: String, arr: JsonArray?): ServerQuestion? {
        val items = arr.orEmpty().mapNotNull { el ->
            val q = el as? JsonObject ?: return@mapNotNull null
            ServerQuestion.Item(
                question = q["question"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                multiple = q["kind"]?.jsonPrimitive?.contentOrNull == "multiple",
                options = (q["options"] as? JsonArray).orEmpty().mapNotNull { op ->
                    val o = op as? JsonObject ?: return@mapNotNull null
                    ServerQuestion.Option(
                        o["label"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null,
                        o["description"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                        o["recommended"]?.jsonPrimitive?.booleanOrNull ?: false,
                    )
                },
            )
        }
        return if (id.isBlank() || items.isEmpty()) null else ServerQuestion(id, items)
    }

    /** A tool's arguments in one short line: the values that say what it does. */
    fun argsLine(args: JsonElement?): String {
        val o = args as? JsonObject ?: return ""
        return o.entries.joinToString("  ") { (k, v) ->
            val value = when (v) {
                is JsonPrimitive -> v.contentOrNull.orEmpty()
                else -> v.toString()
            }.replace('\n', ' ')
            "$k=${if (value.length > 80) value.take(80) + "…" else value}"
        }.take(240)
    }

    private fun JsonArray?.orEmpty(): List<JsonElement> = this ?: emptyList()
}
