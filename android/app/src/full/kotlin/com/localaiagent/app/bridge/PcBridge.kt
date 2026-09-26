package com.localaiagent.app.bridge

import com.localaiagent.core.AgentEvent
import com.localaiagent.core.TERMINAL_ANSWER_KEY
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.addJsonObject
import android.util.Base64
import java.io.File
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/** События ПК-агента, дошедшие по WS (подмножество, нужное телефону). */
internal sealed interface BridgeEvent {
    data class Text(val text: String) : BridgeEvent
    data class ToolStart(val name: String, val args: JsonObject) : BridgeEvent
    data class ToolDone(val name: String, val ok: Boolean, val output: String) : BridgeEvent
    data class Image(val url: String, val caption: String) : BridgeEvent
    data class Finished(val text: String) : BridgeEvent
    data class Failed(val message: String) : BridgeEvent

    // --- обратные запросы ПК → телефон (слой 3): исполнитель просит координатора ---
    /** ПК просит файл (photo=true — фото). Ответ отправляем через [send]. */
    data class NeedFile(val reqId: String, val hint: String, val photo: Boolean, val send: (JsonObject) -> Unit) : BridgeEvent
    /** ПК просит спросить пользователя. */
    data class AskUser(val reqId: String, val question: String, val options: List<String>, val send: (JsonObject) -> Unit) : BridgeEvent
    /** ПК просит выполнить недоступную ему возможность телефона. */
    data class NeedCapability(val reqId: String, val capability: String, val task: String, val send: (JsonObject) -> Unit) : BridgeEvent
}

/**
 * Клиент к WS-протоколу ПК-сервера (`server/ws.py`): открывает /ws, шлёт
 * `{"type":"run","task":…}` и превращает входящие события ПК в [BridgeEvent].
 * Токен — в query (?token=). http→ws, https→wss.
 */
internal class PcBridgeClient(private val config: PcBridgeConfig) {

    private val json = Json { ignoreUnknownKeys = true }
    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS) // WS живёт долго
        .build()

    fun runTask(task: String): Flow<BridgeEvent> = callbackFlow {
        val request = Request.Builder().url(wsUrl()).build()
        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                // Представляемся телефоном, чтобы ПК знал: на том конце устройство с
                // камерой/файлами и ему можно слать обратные запросы (need_file и др.).
                webSocket.send(helloMsg())
                // Задачу делегировал пользователь (вызвал pc_agent) — на ПК идём без
                // подтверждений, иначе ПК-агент зависнет на approval (run_python и др.
                // «опасные» инструменты). Пользователь уже дал согласие делегированием.
                val setMode = buildJsonObject { put("type", "set_mode"); put("mode", "bypass") }
                webSocket.send(json.encodeToString(JsonObject.serializer(), setMode))
                val run = buildJsonObject {
                    put("type", "run"); put("task", task)
                    // Папка/проект на ПК: сервер применяет workspace к запуску.
                    if (config.workspace.isNotBlank()) put("workspace", config.workspace)
                }
                webSocket.send(json.encodeToString(JsonObject.serializer(), run))
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val obj = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return
                val send: (JsonObject) -> Unit = { m -> webSocket.send(json.encodeToString(JsonObject.serializer(), m)) }
                when (val type = obj["type"]?.jsonPrimitive?.contentOrNull) {
                    "text.delta" -> obj.str("text")?.let { trySend(BridgeEvent.Text(it)) }
                    "tool.started" -> trySend(
                        BridgeEvent.ToolStart(obj.str("name").orEmpty(), obj["args"]?.jsonObject ?: JsonObject(emptyMap())),
                    )
                    "tool.finished" -> trySend(
                        BridgeEvent.ToolDone(
                            obj.str("name").orEmpty(),
                            obj["ok"]?.jsonPrimitive?.contentOrNull?.toBoolean() ?: true,
                            obj.str("output").orEmpty(),
                        ),
                    )
                    "show_image" -> obj.str("url")?.let {
                        trySend(BridgeEvent.Image(it, obj.str("caption").orEmpty()))
                    }
                    "run.finished" -> {
                        trySend(BridgeEvent.Finished(obj.str("text").orEmpty()))
                        webSocket.close(1000, null)
                    }
                    "run.failed" -> {
                        trySend(BridgeEvent.Failed(obj.str("message").orEmpty().ifBlank { "ПК-агент вернул ошибку" }))
                        webSocket.close(1000, null)
                    }
                    "approval.requested" -> {
                        // Делегированную задачу пользователь уже одобрил (мы шлём set_mode bypass).
                        // Некоторые инструменты ПК (в т.ч. phone_*) всё равно эмитят approval —
                        // авто-подтверждаем, иначе ПК-агент виснет на approval внутри прогона.
                        send(
                            buildJsonObject {
                                put("type", "approval")
                                put("request_id", obj.str("request_id").orEmpty())
                                put("scope", "once")
                            },
                        )
                    }
                    BridgeProtocol.NEED_FILE, BridgeProtocol.NEED_PHOTO -> trySend(
                        BridgeEvent.NeedFile(
                            obj.str("req_id").orEmpty(), obj.str("hint").orEmpty(),
                            photo = type == BridgeProtocol.NEED_PHOTO, send = send,
                        ),
                    )
                    BridgeProtocol.ASK_USER -> trySend(
                        BridgeEvent.AskUser(
                            obj.str("req_id").orEmpty(), obj.str("question").orEmpty(),
                            obj["options"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull } ?: emptyList(),
                            send = send,
                        ),
                    )
                    BridgeProtocol.NEED_CAPABILITY -> trySend(
                        BridgeEvent.NeedCapability(
                            obj.str("req_id").orEmpty(), obj.str("capability").orEmpty(),
                            obj.str("task").orEmpty(), send = send,
                        ),
                    )
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { close() }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val code = response?.code
                val hint = when (code) {
                    4401, 401, 403 -> "ПК отклонил подключение (проверь токен моста)."
                    else -> t.message ?: "нет связи с ПК"
                }
                trySend(BridgeEvent.Failed(hint))
                close()
            }
        }
        val ws = http.newWebSocket(request, listener)
        awaitClose { ws.cancel() }
    }

    /** Представление телефона: платформа + возможности (слой 1 обнаружения). */
    private fun helloMsg(): String = json.encodeToString(
        JsonObject.serializer(),
        buildJsonObject {
            put("type", BridgeProtocol.HELLO)
            put("platform", "android")
            putJsonArray("capabilities") { BridgeProtocol.ANDROID_CAPABILITIES.forEach { add(it) } }
        },
    )

    /**
     * Одноразовый запрос/ответ по мосту: подключаемся, шлём hello + команду и ждём
     * первое сообщение сервера с типом из [accept]. Возвращает это сообщение или null
     * (нет связи/закрылось). Используется файловыми инструментами (`pc_*`).
     */
    suspend fun oneShot(command: JsonObject, accept: Set<String>): JsonObject? =
        suspendCancellableCoroutine { cont ->
            // Файловая операция открывает СВОЙ сокет = новую сессию ПК с пустой авто-папкой.
            // Поэтому явно указываем рабочую папку, чтобы ПК резолвил пути в неё, а не в
            // случайную папку чата (в run-соединении workspace уже задан и не нужен).
            val payload = if (config.workspace.isNotBlank() && "workspace" !in command) {
                buildJsonObject { command.forEach { (k, v) -> put(k, v) }; put("workspace", config.workspace) }
            } else {
                command
            }
            val ws = http.newWebSocket(Request.Builder().url(wsUrl()).build(), object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocket.send(helloMsg())
                    webSocket.send(json.encodeToString(JsonObject.serializer(), payload))
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    val obj = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return
                    val t = obj["type"]?.jsonPrimitive?.contentOrNull
                    if (t in accept) {
                        if (cont.isActive) cont.resume(obj)
                        webSocket.close(1000, null)
                    }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    if (cont.isActive) cont.resume(null)
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    if (cont.isActive) cont.resume(null)
                }
            })
            cont.invokeOnCancellation { ws.cancel() }
        }

    /** Возможности ПК из `ready` (список инструментов + рабочая папка). Обнаружение без правок ПК. */
    suspend fun capabilities(): JsonObject? = oneShot(
        buildJsonObject { put("type", "ping") },
        setOf(BridgeProtocol.R_HELLO, BridgeProtocol.R_READY),
    )

    /** Проверка связи: подключается и ждёт первое сообщение сервера (ready). */
    suspend fun testConnection(): String? = suspendCancellableCoroutine { cont ->
        val ws = http.newWebSocket(Request.Builder().url(wsUrl()).build(), object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                webSocket.close(1000, null)
                if (cont.isActive) cont.resume(null) // успех: сервер что-то прислал (ready)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val msg = when (response?.code) {
                    4401, 401, 403 -> "ПК отклонил токен"
                    else -> t.message ?: "нет связи"
                }
                if (cont.isActive) cont.resume(msg)
            }
        })
        cont.invokeOnCancellation { ws.cancel() }
    }

    /** http(s)-база ПК — чтобы достроить относительные URL картинок (show_image). */
    fun httpBase(): String {
        val b = config.url.trim().trimEnd('/')
        return when {
            b.startsWith("http://") || b.startsWith("https://") -> b
            b.startsWith("ws://") -> "http://" + b.removePrefix("ws://")
            b.startsWith("wss://") -> "https://" + b.removePrefix("wss://")
            else -> "http://$b"
        }
    }

    private fun wsUrl(): String {
        var base = config.url.trim().trimEnd('/')
        base = when {
            base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
            base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
            base.startsWith("ws://") || base.startsWith("wss://") -> base
            else -> "ws://$base"
        }
        // The token rides in the URL: never over plain ws:// to a public host (see NetPolicy).
        if (config.token.isNotBlank() && !com.localaiagent.core.NetPolicy.credentialsAllowed(base)) {
            throw java.io.IOException(
                "Refusing to send the bridge token over unencrypted ws:// to a public address; " +
                    "use https or a LAN/Tailscale address.",
            )
        }
        val sep = if ("?" in base) "&" else "?"
        val auth = if (config.token.isNotBlank()) "${sep}token=${config.token}" else ""
        return "$base/ws$auth"
    }
}

private fun JsonObject.str(key: String): String? = this[key]?.jsonPrimitive?.contentOrNull

/** Относительный URL картинки с ПК достраиваем до полного (через http-базу ПК). */
private fun absoluteUrl(url: String, httpBase: String): String =
    if (url.startsWith("http")) url else httpBase.trimEnd('/') + "/" + url.trimStart('/')

/**
 * Инструмент делегирования задачи ПК-агенту. Открывает мост, шлёт задачу,
 * показывает удалённые шаги (ПК: …) и возвращает финальный ответ ПК-агента.
 * Регистрируется только когда в настройках задан адрес ПК.
 */
class PcAgentTool(private val config: PcBridgeConfig) : Tool {
    override val name = "pc_agent"
    override val description =
        "Делегирует задачу ПК-агенту на компьютере (программирование, shell, тесты, git, " +
            "работа с проектом) и возвращает результат. Используй для всего, что требует " +
            "среды разработки или файлов на ПК. Телефон — пульт, выполнение — на ПК."
    override val category = ToolCategory.EXECUTE

    // Делегирование на ПК = уже согласие пользователя (он настроил мост и просит выполнить
    // на ПК). Не спрашиваем ещё раз — сохраняем прежний поток full-сборки.
    override fun autoVerdict(args: JsonObject, ctx: ToolContext) = com.localaiagent.core.Verdict.ALLOW

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("task") { put("type", "string"); put("description", "Задача для ПК-агента (по-русски, как пользователю)") }
        }
        putJsonArray("required") { add("task") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val task = args["task"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (task.isEmpty()) return ToolResult.fail("пустая задача для ПК")
        if (!config.enabled) return ToolResult.fail("Мост к ПК не настроен (укажи адрес ПК в настройках).")

        val client = PcBridgeClient(config)
        val sb = StringBuilder()
        var finalText: String? = null
        var failure: String? = null
        var callSeq = 0

        // Сценарий A (авто-перекладка): вложения чата уезжают в inbox/ ПК до прогона,
        // а задача получает подсказку. Естественный гейт — без вложений ничего не шлём.
        val sent = pushAttachmentsToInbox(client, ctx)
        val runTask = if (sent.isNotEmpty()) {
            ctx.emit(AgentEvent.TextDelta("\n_Отправил на ПК в inbox/: ${sent.joinToString(", ")}_\n"))
            "$task\n\n[Файлы пользователя лежат в inbox/: ${sent.joinToString(", ")}. " +
                "Готовый результат клади в out/ — телефон заберёт его сам.]"
        } else {
            task
        }

        client.runTask(runTask).collect { ev ->
            when (ev) {
                // Стримим текст ПК прямо в пузырь ответа на телефоне.
                is BridgeEvent.Text -> { sb.append(ev.text); ctx.emit(AgentEvent.TextDelta(ev.text)) }
                is BridgeEvent.ToolStart ->
                    ctx.emit(AgentEvent.ToolStarted("pc_${callSeq++}", "ПК: ${ev.name}", ev.args))
                is BridgeEvent.ToolDone ->
                    ctx.emit(AgentEvent.ToolFinished("pc_$callSeq", "ПК: ${ev.name}", ev.ok, ev.output.take(200)))
                is BridgeEvent.Image -> ctx.emit(AgentEvent.ShowImage(absoluteUrl(ev.url, client.httpBase()), ev.caption))
                is BridgeEvent.Finished -> finalText = ev.text.ifBlank { sb.toString() }
                is BridgeEvent.Failed -> failure = ev.message
                is BridgeEvent.NeedFile -> handleNeedFile(ev, ctx)
                is BridgeEvent.AskUser -> handleAskUser(ev, ctx)
                is BridgeEvent.NeedCapability -> handleNeedCapability(ev, ctx)
            }
        }

        failure?.let { return ToolResult.fail(it) }

        // Сценарий A: забираем результаты из out/ ПК в чат (картинки покажем).
        val pulled = pullOutputsFromPc(client, ctx)
        if (pulled.isNotEmpty()) {
            ctx.emit(AgentEvent.TextDelta("\n_С ПК забрано в чат: ${pulled.joinToString(", ")}_\n"))
        }

        val answer = (finalText ?: sb.toString()).trim()
        // pc_agent — терминальный: его ответ уже показан пользователю, поэтому просим
        // цикл агента завершиться этим текстом, а не переспрашивать телефонную модель.
        if (answer.isNotEmpty()) ctx.scratch[TERMINAL_ANSWER_KEY] = answer
        return if (answer.isEmpty()) ToolResult("ПК-агент завершил задачу без текстового ответа.")
        else ToolResult("(ответ ПК-агента передан пользователю)")
    }
}

/** Потолок файла для авто-перекладки (тот же, что у моста). */
private const val SHUTTLE_MAX_BYTES = 25L * 1024 * 1024

/** Отправляет вложения чата в inbox/ ПК до прогона. Возвращает имена отправленных. */
private suspend fun pushAttachmentsToInbox(client: PcBridgeClient, ctx: ToolContext): List<String> {
    val dir = File(ctx.workspaceDir, "attachments")
    val files = dir.listFiles()?.filter { it.isFile && it.length() in 1..SHUTTLE_MAX_BYTES } ?: return emptyList()
    val sent = mutableListOf<String>()
    for (f in files) {
        val bytes = runCatching { f.readBytes() }.getOrNull() ?: continue
        val cmd = buildJsonObject {
            put("type", BridgeProtocol.PUT_FILE)
            put("path", "inbox/${f.name}")
            put("b64", Base64.encodeToString(bytes, Base64.NO_WRAP))
        }
        val ok = client.oneShot(cmd, setOf(BridgeProtocol.R_PUT_OK, BridgeProtocol.R_PUT_ERROR))
        if (ok?.str("type") == BridgeProtocol.R_PUT_OK) sent += f.name
    }
    return sent
}

/** Забирает новые файлы из out/ ПК в attachments/ чата. Возвращает имена забранных. */
private suspend fun pullOutputsFromPc(client: PcBridgeClient, ctx: ToolContext): List<String> {
    val listCmd = buildJsonObject { put("type", BridgeProtocol.LIST_FILES); put("glob", "out/**") }
    val listed = client.oneShot(listCmd, setOf(BridgeProtocol.R_FILES)) ?: return emptyList()
    val items = listed["files"]?.jsonArray ?: listed["items"]?.jsonArray ?: return emptyList()
    val dir = File(ctx.workspaceDir, "attachments").apply { mkdirs() }
    val pulled = mutableListOf<String>()
    for (el in items.take(20)) {
        val path = el.jsonObject.str("path") ?: continue
        val getCmd = buildJsonObject { put("type", BridgeProtocol.GET_FILE); put("path", path) }
        val reply = client.oneShot(getCmd, setOf(BridgeProtocol.R_FILE, BridgeProtocol.R_FILE_MISSING)) ?: continue
        val b64 = reply.str("b64") ?: continue
        val bytes = runCatching { Base64.decode(b64, Base64.DEFAULT) }.getOrNull() ?: continue
        val name = path.substringAfterLast('/')
        val dest = File(dir, name)
        runCatching { dest.writeBytes(bytes) }.getOrNull() ?: continue
        pulled += name
        val ext = name.substringAfterLast('.', "").lowercase()
        if (ext in setOf("png", "jpg", "jpeg", "gif", "webp", "bmp")) {
            ctx.emit(AgentEvent.ShowImage(dest.absolutePath, "С ПК: $name"))
        }
    }
    return pulled
}

// ---- слой 3: обработка обратных запросов ПК через человеко-в-контуре телефона ----

/** ПК просит файл/фото → показываем выбор, кладём выбранное на ПК в inbox/, отвечаем. */
private suspend fun handleNeedFile(ev: BridgeEvent.NeedFile, ctx: ToolContext) {
    ctx.emit(AgentEvent.TextDelta("\n\n_ПК просит ${if (ev.photo) "фото" else "файл"}: ${ev.hint}_\n"))
    val ans = ctx.requestUi(
        "file",
        buildJsonObject {
            put("purpose", (if (ev.photo) "ПК просит фото" else "ПК просит файл") + ": " + ev.hint)
            put("accept", if (ev.photo) "image/*" else "*/*")
            put("multiple", false)
        },
    )
    val first = ans["files"]?.jsonArray?.firstOrNull()?.jsonObject
    if (first == null) {
        ev.send(buildJsonObject { put("type", BridgeProtocol.NEED_FILE_CANCEL); put("req_id", ev.reqId) })
        return
    }
    val rel = first.str("path").orEmpty() // attachments/<name>
    val nm = first.str("name") ?: rel.substringAfterLast('/')
    val bytes = runCatching { File(ctx.workspaceDir, rel).readBytes() }.getOrNull()
    if (bytes == null || bytes.size > BridgeProtocol.MAX_FILE_BYTES) {
        ev.send(buildJsonObject { put("type", BridgeProtocol.NEED_FILE_CANCEL); put("req_id", ev.reqId) })
        return
    }
    ev.send(
        buildJsonObject {
            put("type", BridgeProtocol.PUT_FILE); put("path", "inbox/$nm")
            put("b64", Base64.encodeToString(bytes, Base64.NO_WRAP))
        },
    )
    ev.send(buildJsonObject { put("type", BridgeProtocol.NEED_FILE_DONE); put("req_id", ev.reqId); put("path", "inbox/$nm") })
}

/** ПК просит спросить пользователя → форма ask на телефоне → ответ ПК. */
private suspend fun handleAskUser(ev: BridgeEvent.AskUser, ctx: ToolContext) {
    val chosen = askUserOnPhone(ctx, ev.question, ev.options)
    // Отдаём ПК в формате, который понимает его _flatten_answers: {"0":[значения]}.
    ev.send(
        buildJsonObject {
            put("type", BridgeProtocol.ANSWER); put("request_id", ev.reqId)
            putJsonObject("answers") { putJsonArray("0") { chosen.forEach { add(it) } } }
        },
    )
}

/**
 * Показывает форму ask на телефоне и возвращает выбранные значения (label выбранной
 * опции или свой текст). Строит payload в формате, который понимает parseAsk
 * (id/title/type/options[].id), иначе форма выходит пустой.
 */
private suspend fun askUserOnPhone(ctx: ToolContext, question: String, options: List<String>): List<String> {
    val payload = buildJsonObject {
        putJsonArray("questions") {
            addJsonObject {
                put("id", "q0")
                put("title", question)
                put("type", "single")
                put("allow_custom", true) // всегда даём и свободный ответ
                if (options.isNotEmpty()) {
                    putJsonArray("options") {
                        options.forEachIndexed { i, o -> addJsonObject { put("id", "o$i"); put("label", o) } }
                    }
                }
            }
        }
    }
    val ans = ctx.requestUi("ask", payload)
    val chosen = mutableListOf<String>()
    ans["answers"]?.jsonArray?.forEach { aEl ->
        val a = aEl.jsonObject
        a["selected"]?.jsonArray?.forEach { s ->
            val id = s.jsonPrimitive.contentOrNull ?: return@forEach
            val idx = id.removePrefix("o").toIntOrNull()
            chosen += if (idx != null && idx in options.indices) options[idx] else id
        }
        a["custom"]?.jsonPrimitive?.contentOrNull?.takeIf { it.isNotBlank() }?.let { chosen += it }
    }
    return chosen
}

/**
 * ПК просит выполнить недоступную ему возможность телефона. MVP: телефон всегда умеет
 * спросить человека — показываем задачу пользователю и возвращаем его ответ ПК.
 * Конкретные захваты (файл/фото) ПК делает через need_file/need_photo. Полноценный
 * «телефон-исполнитель» (нативные датчики/геолокация без участия юзера) — следующий этап.
 */
private suspend fun handleNeedCapability(ev: BridgeEvent.NeedCapability, ctx: ToolContext) {
    ctx.emit(AgentEvent.TextDelta("\n\n_ПК просит телефон: ${ev.task} (${ev.capability})_\n"))
    val chosen = askUserOnPhone(ctx, "ПК просит выполнить на телефоне: ${ev.task}. Что ответить ПК?", emptyList())
    ev.send(
        buildJsonObject {
            put("type", BridgeProtocol.CAPABILITY_RESULT); put("req_id", ev.reqId)
            put("ok", chosen.isNotEmpty())
            putJsonObject("answers") { putJsonArray("0") { chosen.forEach { add(it) } } }
        },
    )
}
