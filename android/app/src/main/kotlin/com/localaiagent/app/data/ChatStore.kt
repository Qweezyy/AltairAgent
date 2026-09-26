package com.localaiagent.app.data

import com.localaiagent.app.ChatMessage
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.add
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.File

/** Персистентность чатов: сообщения каждого чата — в chats/<id>/session.json. */
object ChatStore {
    private val JSON = Json { ignoreUnknownKeys = true }

    data class PersistedChat(val id: String, val created: Long, val messages: List<ChatMessage>)

    fun save(chatDir: File, id: String, created: Long, messages: List<ChatMessage>) {
        runCatching {
            chatDir.mkdirs()
            val arr = buildJsonArray {
                for (m in messages) addJsonObject {
                    put("u", m.fromUser); put("t", m.text)
                    m.imageUrl?.let { put("img", it) }
                    m.attachPath?.let { put("ap", it) }
                    m.attachName?.let { put("an", it) }
                    m.attachKind?.let { put("ak", it) }
                    m.html?.let { put("html", it) }
                    m.replyQuote?.let { put("rq", it) }
                    m.reaction?.let { put("re", it) }
                    put("id", m.id)
                    if (m.versions.isNotEmpty()) {
                        put("vi", m.verIndex)
                        putJsonArray("vs") { m.versions.forEach { add(it) } }
                        putJsonArray("vr") { m.versionReplies.forEach { add(it) } }
                    }
                }
            }
            val obj = buildJsonObject { put("id", id); put("created", created); put("messages", arr) }
            File(chatDir, "session.json").writeText(obj.toString())
        }
    }

    /** Загружает все сохранённые чаты (по папкам chats/<id>/session.json), новые сверху. */
    fun loadAll(chatsRoot: File): List<PersistedChat> {
        val out = mutableListOf<PersistedChat>()
        chatsRoot.listFiles()?.forEach { dir ->
            val f = File(dir, "session.json")
            if (!f.isFile) return@forEach
            runCatching {
                val o = JSON.parseToJsonElement(f.readText()).jsonObject
                val id = o["id"]?.jsonPrimitive?.contentOrNull ?: dir.name
                val created = o["created"]?.jsonPrimitive?.contentOrNull?.toLongOrNull() ?: 0L
                val msgs = (o["messages"] as? JsonArray)?.mapNotNull { el ->
                    val m = el.jsonObject
                    ChatMessage(
                        fromUser = m["u"]?.jsonPrimitive?.contentOrNull?.toBoolean() ?: false,
                        text = m["t"]?.jsonPrimitive?.contentOrNull ?: "",
                        imageUrl = m["img"]?.jsonPrimitive?.contentOrNull,
                        attachPath = m["ap"]?.jsonPrimitive?.contentOrNull,
                        attachName = m["an"]?.jsonPrimitive?.contentOrNull,
                        attachKind = m["ak"]?.jsonPrimitive?.contentOrNull,
                        html = m["html"]?.jsonPrimitive?.contentOrNull,
                        replyQuote = m["rq"]?.jsonPrimitive?.contentOrNull,
                        reaction = m["re"]?.jsonPrimitive?.contentOrNull,
                        id = m["id"]?.jsonPrimitive?.contentOrNull ?: com.localaiagent.app.randomMsgId(),
                        versions = (m["vs"] as? JsonArray)?.mapNotNull { it.jsonPrimitive.contentOrNull } ?: emptyList(),
                        versionReplies = (m["vr"] as? JsonArray)?.mapNotNull { it.jsonPrimitive.contentOrNull } ?: emptyList(),
                        verIndex = m["vi"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 0,
                    )
                } ?: emptyList()
                out += PersistedChat(id, created, msgs)
            }
        }
        return out.sortedByDescending { it.created }
    }

    fun delete(chatDir: File) {
        runCatching { chatDir.deleteRecursively() }
    }
}
