package com.localaiagent.core.tools

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Ограниченный «терминал» телефона: выполняет команду в ПЕСОЧНИЦЕ приложения
 * (uid приложения, без root). Работают: getprop, echo, ls/cat по доступным путям,
 * простые утилиты toybox. Многое системное недоступно (нет прав shell/root) — для
 * настоящей разработки/шелла используй pc_agent (выполнится на ПК).
 */
class RunShellTool : Tool {
    override val name = "run_shell"
    override val description =
        "Выполняет shell-команду на телефоне в песочнице приложения (без root): напр. " +
            "getprop ro.product.model, echo, ls, cat доступных файлов, date. Многие системные " +
            "команды недоступны без прав. Для полноценного терминала/разработки — pc_agent (ПК)."
    override val category = ToolCategory.EXECUTE

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("command") { put("type", "string"); put("description", "Команда для sh -c") }
            putJsonObject("timeout") { put("type", "integer"); put("description", "Таймаут, сек (по умолчанию 15, до 60)") }
        }
        putJsonArray("required") { add("command") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val rawCmd = args["command"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (rawCmd.isEmpty()) return ToolResult.fail("пустая команда")
        // Substitute {{secret:NAME}} — the model never sees the values.
        val (cmd, missing, used) = substituteSecrets(rawCmd, ctx)
        if (missing.isNotEmpty()) return ToolResult.fail("secret(s) unavailable: ${missing.joinToString(", ")}")
        val timeout = (args["timeout"]?.jsonPrimitive?.contentOrNull?.toLongOrNull() ?: 15).coerceIn(1, 60)
        val result = withContext(Dispatchers.IO) { execute(cmd, timeout, ctx) }
        // A command can echo a secret back (echo, env, verbose curl) — mask it so the value never
        // reaches the model context or the saved chat history.
        return if (used.isEmpty()) result else result.copy(content = redactSecrets(result.content, used))
    }

    private data class Substituted(val cmd: String, val missing: List<String>, val used: Map<String, String>)

    private suspend fun substituteSecrets(cmd: String, ctx: ToolContext): Substituted {
        val re = Regex("\\{\\{secret:([A-Za-z0-9_.-]+)\\}\\}")
        val missing = mutableListOf<String>()
        val used = linkedMapOf<String, String>()
        var out = cmd
        for (m in re.findAll(cmd).toList()) {
            val name = m.groupValues[1]
            val v = ctx.secret(name)
            if (v == null) missing += name else { out = out.replace(m.value, v); used[name] = v }
        }
        return Substituted(out, missing, used)
    }

    private fun execute(cmd: String, timeout: Long, ctx: ToolContext): ToolResult {
        return runCatching {
            val proc = ProcessBuilder("/system/bin/sh", "-c", cmd)
                .directory(File(ctx.workspaceDir))
                .redirectErrorStream(true)
                .start()
            // Drain stdout on a separate thread: reading only after waitFor() deadlocks as soon as the
            // output exceeds the pipe buffer (~64 KB) — the child blocks on write, we block on wait.
            val buf = StringBuilder()
            val reader = Thread {
                runCatching {
                    proc.inputStream.bufferedReader().use { r ->
                        val chunk = CharArray(8192)
                        while (true) {
                            val n = r.read(chunk)
                            if (n < 0) break
                            synchronized(buf) { if (buf.length < MAX_OUT) buf.append(chunk, 0, n) }
                        }
                    }
                }
            }.apply { isDaemon = true; start() }
            val done = proc.waitFor(timeout, TimeUnit.SECONDS)
            if (!done) {
                proc.destroyForcibly()
                reader.join(1000)
                return@runCatching ToolResult.fail("timed out after ${timeout}s")
            }
            reader.join(2000)
            val out = synchronized(buf) { buf.toString() }.trim()
            val code = proc.exitValue()
            val body = if (out.isEmpty()) "(empty output)" else out.take(8000)
            if (code == 0) ToolResult(body) else ToolResult("exit code $code\n$body", ok = false)
        }.getOrElse { ToolResult.fail("could not run: ${it.message}") }
    }

    private companion object {
        const val MAX_OUT = 64_000
    }
}

/**
 * Masks injected secret values in command output with their {{secret:NAME}} placeholders, longest
 * value first so a secret containing another one is masked whole. Values shorter than 4 chars are
 * left alone: masking "1" or "ab" would shred ordinary output.
 */
internal fun redactSecrets(text: String, used: Map<String, String>): String {
    var out = text
    for ((name, value) in used.entries.sortedByDescending { it.value.length }) {
        if (value.length >= 4) out = out.replace(value, "{{secret:$name}}")
    }
    return out
}
