package com.localaiagent.core

import com.localaiagent.core.tools.DeferredTools
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.Json

/**
 * Цикл агента: модель → инструменты → модель → … → ответ. Линейный и предсказуемый,
 * как core/agent/runner.py. Эмитит [AgentEvent] потоком; UI — тонкий потребитель.
 *
 * В Фазе 0 реестр инструментов пуст, поэтому это просто «спросил модель — стримит
 * ответ». В Фазе 1 добавляются инструменты, и заработает полный цикл.
 */
class Agent(
    private val llm: LlmClient,
    private val registry: ToolRegistry = ToolRegistry(),
    private val session: Session,
    private val workspaceDir: String = ".",
    private val globalMemoryDir: String = workspaceDir,
    private val contextWindow: Int = 128_000,
    private val onUiRequest: (suspend (String, JsonObject) -> JsonObject)? = null,
    private val secretProvider: (suspend (String) -> String?)? = null,
    private val secretsInfoProvider: (() -> List<String>)? = null,
    private val maxSteps: Int = 40,
) {
    private val json = Json { ignoreUnknownKeys = true }

    private companion object {
        // Столько раундов инструментов максимум, потом форсируем текстовый ответ.
        // С запасом на «прочитать несколько ссылок»: модель может читать страницы
        // кусками (fetch_url + offset), это отдельные раунды.
        const val MAX_TOOL_ROUNDS = 8
        val SECRET_REF = Regex("\\{\\{secret:([A-Za-z0-9_.-]+)\\}\\}")
    }

    // channelFlow (а не flow), потому что инструменты (deep_research) эмитят события
    // из withContext(Dispatchers.IO) — send из другого диспетчера безопасен только
    // в channelFlow. Обычный flow тут кидает «Flow invariant is violated».
    fun run(task: String, parts: List<Part> = emptyList()): Flow<AgentEvent> = channelFlow {
        val runId = randomId()
        session.addUser(task, parts)
        send(AgentEvent.RunStarted(runId, model = "model"))
        send(AgentEvent.ContextUsage(session.tokenEstimate()))

        // Инструменты, которые пользователь разрешил «всегда» в этом прогоне — не спрашиваем повторно.
        val alwaysAllowed = mutableSetOf<String>()

        val ctx = object : ToolContext {
            override val workspaceDir = this@Agent.workspaceDir
            override val globalMemoryDir = this@Agent.globalMemoryDir
            override val session = this@Agent.session
            override val contextWindow = this@Agent.contextWindow
            override val registry = this@Agent.registry
            override val scratch = mutableMapOf<String, Any?>()
            override suspend fun approve(name: String, reason: String, args: JsonObject): Boolean {
                // A call that injects secrets is always confirmed, even after "always allow":
                // prompt-injected text could otherwise ship a key to any server unnoticed.
                val secrets = secretNames(args)
                if (name in alwaysAllowed && secrets.isEmpty()) return true
                // Нет UI (headless/тесты) — сохраняем прежнее поведение (разрешаем).
                val ui = this@Agent.onUiRequest ?: return true
                val ans = ui(
                    "approve",
                    buildJsonObject {
                        put("name", name)
                        put("reason", reason)
                        put("detail", approvalDetail(args))
                        if (secrets.isNotEmpty()) put("secrets", secrets.joinToString(", "))
                    },
                )
                return when (ans["decision"]?.jsonPrimitive?.contentOrNull) {
                    "always" -> { if (secrets.isEmpty()) alwaysAllowed += name; true }
                    "once", "allow" -> true
                    else -> false // "deny" или пустой ответ (отмена) — не выполняем
                }
            }
            override suspend fun emit(event: AgentEvent) { send(event) }
            override suspend fun requestUi(kind: String, payload: JsonObject): JsonObject =
                this@Agent.onUiRequest?.invoke(kind, payload) ?: JsonObject(emptyMap())
            override suspend fun secret(name: String): String? = this@Agent.secretProvider?.invoke(name)
            override fun secretsInfo(): List<String> = this@Agent.secretsInfoProvider?.invoke() ?: emptyList()
        }

        var step = 0
        var toolRounds = 0
        try {
            while (step < maxSteps) {
                step++
                // После MAX_TOOL_ROUNDS раундов инструменты отключаем — заставляем
                // модель дать текстовый ответ (некоторые модели зацикливаются на tool_calls).
                val allowTools = registry.all().isNotEmpty() && toolRounds < MAX_TOOL_ROUNDS
                // Deferred loading: only core tools + those loaded/used in this chat go up front.
                val active = DeferredTools.activeNames(registry, session.snapshot(), ctx.scratch)
                // A broken stream is continued by the client where it stopped; only when it has to
                // start over does it discard the streamed text, which the UI then takes back.
                val turn = llm.complete(
                    messages = session.snapshot(),
                    tools = if (allowTools) registry.schemas(active) else null,
                    onText = { send(AgentEvent.TextDelta(it)) },
                    onReasoning = { send(AgentEvent.ReasoningDelta(it)) },
                    onRetry = { a, m, d, r -> send(AgentEvent.Reconnecting(a, m, d, r)) },
                    onDiscard = { chars -> send(AgentEvent.TextRetracted(chars)) },
                )
                session.addAssistant(turn)
                send(AgentEvent.ContextUsage(session.tokenEstimate()))

                if (!turn.wantsTools) {
                    send(AgentEvent.RunFinished(turn.content, turn.usage))
                    return@channelFlow
                }

                toolRounds++
                // Выполняем запрошенные инструменты и возвращаем результаты модели.
                for (call in turn.toolCalls) {
                    val args = parseArgs(call.arguments)
                    send(AgentEvent.ToolStarted(call.id, call.name, args))
                    val tool = registry.get(call.name)
                    val result = if (tool == null) {
                        ToolResult.fail("Tool '${call.name}' does not exist.")
                    } else {
                        // Согласие: read/edit — авто; execute/network — спрашиваем пользователя.
                        val permitted = when (tool.autoVerdict(args, ctx)) {
                            Verdict.ALLOW -> true
                            Verdict.DENY -> false
                            Verdict.ASK -> ctx.approve(call.name, tool.description, args)
                        }
                        if (!permitted) {
                            ToolResult.fail("The user declined running '${call.name}'.")
                        } else {
                            runCatching { tool.run(args, ctx) }
                                .getOrElse { ToolResult.fail(it.message ?: "tool failure") }
                        }
                    }
                    session.addToolResult(call.id, call.name, result.content)
                    send(AgentEvent.ToolFinished(call.id, call.name, result.ok, result.content))
                }
                send(AgentEvent.ContextUsage(session.tokenEstimate()))

                // Терминальный инструмент (pc_agent) уже стримнул финальный ответ —
                // завершаем прогон им, не переспрашивая модель.
                (ctx.scratch[TERMINAL_ANSWER_KEY] as? String)?.let { finalAns ->
                    ctx.scratch.remove(TERMINAL_ANSWER_KEY)
                    send(AgentEvent.RunFinished(finalAns, Usage()))
                    return@channelFlow
                }
            }
            // Потолок шагов — просим финальный ответ без инструментов.
            val fin = llm.complete(
                messages = session.snapshot(),
                tools = null,
                onText = { send(AgentEvent.TextDelta(it)) },
            )
            session.addAssistant(fin)
            send(AgentEvent.RunFinished(fin.content.ifBlank { "No answer received." }, fin.usage))
        } catch (t: Throwable) {
            send(AgentEvent.RunFailed(t.message ?: t.toString()))
        }
    }

    /** Names of the secrets a call would inject via {{secret:NAME}} placeholders. */
    private fun secretNames(args: JsonObject): List<String> =
        SECRET_REF.findAll(args.toString()).map { it.groupValues[1] }.distinct().toList()

    /** Короткая суть вызова для карточки согласия: команда/задача/первый строковый аргумент. */
    private fun approvalDetail(args: JsonObject): String {
        val keyed = (args["command"] ?: args["task"] ?: args["code"]) as? JsonPrimitive
        val value = keyed?.contentOrNull
            ?: args.values.firstNotNullOfOrNull { (it as? JsonPrimitive)?.contentOrNull }
            ?: return ""
        return value.trim().replace("\n", " ").take(160)
    }

    private fun parseArgs(raw: String): JsonObject =
        runCatching { json.parseToJsonElement(raw.ifBlank { "{}" }).jsonObject }
            .getOrElse { JsonObject(emptyMap()) }

    private fun randomId(): String {
        val pool = "0123456789abcdef"
        return buildString { repeat(12) { append(pool[kotlin.random.Random.nextInt(pool.length)]) } }
    }
}
