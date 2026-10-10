package com.localaiagent.app.servers

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.localaiagent.app.LocaleManager
import com.localaiagent.app.R
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.Locale

/** A server in the list, with how it is reachable now. */
data class ServerCard(
    val entry: ServerEntry,
    val route: ServerRoute? = null,
    val checking: Boolean = false,
    val problem: String? = null,
)

data class ServerChatRow(val id: String, val title: String, val running: Boolean)

data class JournalRow(val seq: Long, val ts: Double, val kind: String, val chat: String, val text: String)

/** The open server's page: its state, chats and Journal. */
data class ServerDetail(
    val serverId: String,
    val status: JsonObject? = null,
    val chats: List<ServerChatRow> = emptyList(),
    val journal: List<JournalRow> = emptyList(),
    /** The Journal is not open on this route (the server keeps it for its own machine). */
    val journalClosed: Boolean = false,
    val loading: Boolean = false,
    val problem: String? = null,
)

data class ServersUi(
    val cards: List<ServerCard> = emptyList(),
    val pairing: Boolean = false,
    val message: String? = null,
    val detail: ServerDetail? = null,
    val chatServerId: String? = null,
    val chat: ServerChatState? = null,
)

class ServersViewModel(app: Application) : AndroidViewModel(app) {
    private val json = Json { ignoreUnknownKeys = true }
    private val _ui = MutableStateFlow(ServersUi())
    val ui: StateFlow<ServersUi> = _ui.asStateFlow()

    private var socket: WebSocket? = null
    @Volatile private var socketGen = 0

    init {
        ServerHub.init(app)
    }

    private fun text(id: Int, vararg args: Any): String = LocaleManager.wrap(getApplication()).getString(id, *args)

    private fun problemOf(e: Throwable): String = when (e) {
        is ServerError.NotTrusted -> text(R.string.srv_err_not_trusted)
        is ServerError.CodeRefused -> text(R.string.srv_err_code)
        is ServerError.Gone -> text(R.string.srv_err_gone)
        is ServerError.Unreachable -> text(R.string.srv_err_offline)
        is ServerError.Http -> text(R.string.srv_err_http, e.code, e.message.orEmpty())
        else -> e.message ?: e.javaClass.simpleName
    }

    fun consumeMessage() = _ui.update { it.copy(message = null) }

    // ------------------------------------------------------------------ the list

    /** Loads the list and checks each server's route in parallel. */
    fun refresh() {
        viewModelScope.launch {
            val entries = withContext(Dispatchers.IO) { ServerHub.store.entries() }
            _ui.update { ui -> ui.copy(cards = entries.map { e -> ServerCard(e, checking = true) }) }
            entries.map { e ->
                async {
                    val card = try {
                        ServerCard(e, route = ServerHub.client(e).probe())
                    } catch (c: CancellationException) {
                        throw c
                    } catch (x: Exception) {
                        ServerCard(e, problem = problemOf(x))
                    }
                    _ui.update { ui -> ui.copy(cards = ui.cards.map { if (it.entry.id == e.id) card.copy(
                        problem = card.problem ?: if (card.route == null) text(R.string.srv_err_offline) else null) else it }) }
                }
            }.forEach { it.await() }
        }
    }

    /**
     * Pairs with the server of a scanned `altair://body` link. The server is kept only once it trusts
     * this phone: the one-time code is useless afterwards and is never stored.
     */
    fun pair(link: ServerLink) {
        if (_ui.value.pairing) return
        _ui.update { it.copy(pairing = true) }
        viewModelScope.launch {
            val entry = ServerEntry.of(link)
            val result = try {
                val client = ServerClient(entry, withContext(Dispatchers.IO) { ServerHub.store.identity() })
                val card = client.pair(link.code)
                val name = card["name"]?.jsonPrimitive?.contentOrNull?.takeIf { link.name.isBlank() }
                withContext(Dispatchers.IO) {
                    ServerHub.store.put(entry.copy(paired = true, name = name ?: entry.name))
                    ServerHub.schedule()
                }
                text(R.string.srv_paired, name ?: entry.name)
            } catch (c: CancellationException) {
                throw c
            } catch (e: Exception) {
                text(R.string.srv_pair_failed, problemOf(e))
            }
            _ui.update { it.copy(pairing = false, message = result) }
            refresh()
        }
    }

    fun remove(id: String) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) { ServerHub.forget(id) }
            _ui.update { ui -> ui.copy(cards = ui.cards.filterNot { it.entry.id == id }, detail = ui.detail?.takeIf { it.serverId != id }) }
        }
    }

    // ------------------------------------------------------------------ one server

    fun openServer(id: String) {
        _ui.update { it.copy(detail = ServerDetail(id, loading = true)) }
        reloadServer()
    }

    fun closeServer() = _ui.update { it.copy(detail = null) }

    fun reloadServer() {
        val id = _ui.value.detail?.serverId ?: return
        _ui.update { it.copy(detail = it.detail?.copy(loading = true, problem = null)) }
        viewModelScope.launch {
            val entry = withContext(Dispatchers.IO) { ServerHub.store.entry(id) } ?: return@launch closeServer()
            val client = ServerHub.client(entry)
            val detail = try {
                val status = client.get("/api/body/status")
                val chats = client.get("/api/sessions")
                var closed = false
                val journal = try {
                    client.get("/api/journal", mapOf("limit" to "100"))
                } catch (e: ServerError.Http) {
                    if (e.code == 403) { closed = true; JsonObject(emptyMap()) } else throw e
                }
                ServerDetail(id, status, chatRows(chats), journalRows(journal), journalClosed = closed)
            } catch (c: CancellationException) {
                throw c
            } catch (e: Exception) {
                ServerDetail(id, problem = problemOf(e))
            }
            _ui.update { ui -> if (ui.detail?.serverId == id) ui.copy(detail = detail) else ui }
        }
    }

    /** The one button that stops every task on the server (confirmed in the UI first). */
    fun stopAll() {
        val id = _ui.value.detail?.serverId ?: return
        viewModelScope.launch {
            val msg = try {
                val entry = withContext(Dispatchers.IO) { ServerHub.store.entry(id) } ?: return@launch
                val res = ServerHub.client(entry).post("/api/runs/stop")
                val n = (res["stopped"] as? JsonArray)?.size ?: 0
                text(R.string.srv_stopped, n)
            } catch (c: CancellationException) {
                throw c
            } catch (e: Exception) {
                problemOf(e)
            }
            _ui.update { it.copy(message = msg) }
            reloadServer()
        }
    }

    private fun chatRows(o: JsonObject): List<ServerChatRow> = (o["sessions"] as? JsonArray).orEmpty().mapNotNull {
        val s = it as? JsonObject ?: return@mapNotNull null
        ServerChatRow(
            id = s["id"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null,
            title = s["title"]?.jsonPrimitive?.contentOrNull.orEmpty(),
            running = s["running"]?.jsonPrimitive?.booleanOrNull ?: false,
        )
    }

    private fun journalRows(o: JsonObject): List<JournalRow> = (o["records"] as? JsonArray).orEmpty().mapNotNull {
        val r = it as? JsonObject ?: return@mapNotNull null
        JournalRow(
            seq = r["seq"]?.jsonPrimitive?.longOrNull ?: 0,
            ts = r["ts"]?.jsonPrimitive?.doubleOrNull ?: 0.0,
            kind = r["kind"]?.jsonPrimitive?.contentOrNull.orEmpty(),
            chat = r["chat"]?.jsonPrimitive?.contentOrNull.orEmpty(),
            text = journalText(r["data"] as? JsonObject),
        )
    }

    /** The record's few words that say what happened. */
    private fun journalText(data: JsonObject?): String {
        if (data == null) return ""
        for (k in listOf("text", "message", "reason", "name", "summary", "title")) {
            data[k]?.jsonPrimitive?.contentOrNull?.takeIf { it.isNotBlank() }?.let { return it.replace('\n', ' ').take(200) }
        }
        return ""
    }

    private fun JsonArray?.orEmpty(): List<kotlinx.serialization.json.JsonElement> = this ?: emptyList()

    // ------------------------------------------------------------------ a server chat

    /** Opens a chat of the server (null: a new one) over its `/ws`, the same protocol as the PC's. */
    fun openChat(serverId: String, chatId: String?) {
        closeSocket()
        _ui.update { it.copy(chatServerId = serverId, chat = ServerChatState(chatId = chatId?.ifBlank { null })) }
        connect(serverId, retryAuth = true)
    }

    fun reconnect() {
        val id = _ui.value.chatServerId ?: return
        closeSocket()
        connect(id, retryAuth = true)
    }

    fun closeChat() {
        closeSocket()
        ServerHub.viewing = null
        _ui.update { it.copy(chatServerId = null, chat = null) }
        reloadServer()
    }

    private fun closeSocket() {
        socketGen++
        socket?.close(1000, null)
        socket = null
    }

    private fun connect(serverId: String, retryAuth: Boolean) {
        val gen = ++socketGen
        viewModelScope.launch {
            val entry = withContext(Dispatchers.IO) { ServerHub.store.entry(serverId) } ?: return@launch
            val client = ServerHub.client(entry)
            val listener = object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    if (gen != socketGen) return
                    send(webSocket, buildJsonObject { put("type", "ui_lang"); put("lang", uiLang()) })
                    _ui.value.chat?.chatId?.let { id ->
                        send(webSocket, buildJsonObject { put("type", "load_session"); put("session_id", id) })
                    }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    if (gen != socketGen) return
                    val o = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return
                    if (o["type"]?.jsonPrimitive?.contentOrNull == "notice") {
                        ServerHub.onServerNotice(serverId, o)
                        return
                    }
                    _ui.update { ui -> ui.chat?.let { ui.copy(chat = ServerChatReducer.apply(it, o)) } ?: ui }
                    _ui.value.chat?.chatId?.let { ServerHub.viewing = serverId to it }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    if (gen != socketGen) return
                    // A token from before the server restarted: sign in again, once.
                    if (response?.code == 401 && retryAuth && client.route == ServerRoute.DIRECT) {
                        client.forgetToken()
                        viewModelScope.launch { connect(serverId, retryAuth = false) }
                        return
                    }
                    val why = if (response?.code == 401) text(R.string.srv_err_not_trusted) else text(R.string.srv_err_offline)
                    _ui.update { ui -> ui.copy(chat = ui.chat?.copy(connected = false), message = why) }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    if (gen != socketGen) return
                    _ui.update { ui -> ui.copy(chat = ui.chat?.copy(connected = false)) }
                }
            }
            try {
                val ws = client.openSocket(listener)
                if (gen == socketGen) socket = ws else ws.close(1000, null)
            } catch (c: CancellationException) {
                throw c
            } catch (e: Exception) {
                if (gen == socketGen) _ui.update { it.copy(message = problemOf(e)) }
            }
        }
    }

    private fun uiLang(): String {
        val chosen = LocaleManager.get(getApplication())
        val lang = if (chosen == LocaleManager.SYSTEM) Locale.getDefault().language else chosen
        return if (lang == "ru") "ru" else "en"
    }

    private fun send(ws: WebSocket, o: JsonObject) {
        ws.send(o.toString())
    }

    /** Sends a task; during a run the server takes it as steering for the next step. */
    fun sendTask(task: String) {
        val text = task.trim()
        val ws = socket ?: return
        if (text.isEmpty()) return
        send(ws, buildJsonObject { put("type", "run"); put("task", text) })
        _ui.update { ui -> ui.copy(chat = ui.chat?.let { ServerChatReducer.sent(it, text) }) }
    }

    fun stopRun() {
        socket?.let { send(it, buildJsonObject { put("type", "stop") }) }
    }

    /** once | project | global | deny — the same choices as on the PC. */
    fun answerApproval(scope: String) {
        val id = _ui.value.chat?.approval?.requestId ?: return
        socket?.let { send(it, buildJsonObject { put("type", "approval"); put("request_id", id); put("scope", scope) }) }
        _ui.update { ui -> ui.copy(chat = ui.chat?.copy(approval = null)) }
    }

    /** Answers to the agent's questions, by index: the chosen option labels. */
    fun answerQuestion(answers: Map<Int, List<String>>) {
        val id = _ui.value.chat?.question?.requestId ?: return
        socket?.let {
            send(it, buildJsonObject {
                put("type", "answer"); put("request_id", id)
                putJsonObject("answers") {
                    answers.forEach { (i, labels) -> putJsonArray(i.toString()) { labels.forEach { l -> add(kotlinx.serialization.json.JsonPrimitive(l)) } } }
                }
            })
        }
        _ui.update { ui -> ui.copy(chat = ui.chat?.copy(question = null)) }
    }

    override fun onCleared() {
        closeSocket()
        ServerHub.viewing = null
    }
}
