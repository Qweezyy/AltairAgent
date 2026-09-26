package com.localaiagent.app.tools

import com.chaquo.python.Python
import com.localaiagent.core.AgentEvent
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * Полноценный Python на телефоне через встроенный CPython (Chaquopy) — офлайн, без ПК.
 * Живёт в :app (Chaquopy — android-зависимость) и регистрируется рядом с builtinTools().
 * Категория EXECUTE → перед запуском спрашивается согласие пользователя (см. Agent.approve).
 *
 * Доступны sympy (точная математика) и numpy. matplotlib-графики (если пакет добавлен в
 * сборку) сохраняются в PNG и показываются в чате.
 */
class RunPythonTool : Tool {
    override val name = "run_python"
    override val description =
        "Runs Python code right on the phone (built-in CPython, offline). Available: " +
            "sympy (exact integrals/derivatives/equations) and numpy (arrays/numeric computation). " +
            "Returns stdout (use print). For any calculations and verifying solutions " +
            "prefer it over mental math. pip is unavailable at runtime — only preinstalled packages."
    override val category = ToolCategory.EXECUTE

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("code") { put("type", "string"); put("description", "Python code; output the result with print()") }
            putJsonObject("timeout") { put("type", "integer"); put("description", "Timeout, seconds (default 20, up to 60)") }
        }
        putJsonArray("required") { add("code") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val code = args["code"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (code.isEmpty()) return ToolResult.fail("empty code")
        val timeout = (args["timeout"]?.jsonPrimitive?.contentOrNull?.toLongOrNull() ?: 20).coerceIn(1, 60)

        var errMsg: String? = null
        val reply: String? = withContext(Dispatchers.IO) {
            withTimeoutOrNull(timeout * 1000L) {
                try {
                    Python.getInstance()
                        .getModule("agent_runner")
                        .callAttr("run", code, ctx.workspaceDir)
                        .toString()
                } catch (t: Throwable) {
                    errMsg = t.message ?: t.toString()
                    null
                }
            }
        }

        if (reply == null) {
            return errMsg?.let { ToolResult.fail("Python interpreter: $it") }
                ?: ToolResult.fail("Python did not finish in ${timeout}s (possibly an infinite loop).")
        }

        val obj = runCatching { Json.parseToJsonElement(reply).jsonObject }.getOrNull()
            ?: return ToolResult(reply)
        val ok = obj["ok"]?.jsonPrimitive?.booleanOrNull ?: true
        val output = obj["output"]?.jsonPrimitive?.contentOrNull.orEmpty()
        // Графики (если matplotlib присутствует) — показываем как картинки в чате.
        obj["images"]?.jsonArray?.forEach { el ->
            val path = el.jsonPrimitive.contentOrNull ?: return@forEach
            ctx.emit(AgentEvent.ShowImage(path, "Python: chart"))
        }
        val body = output.ifBlank { if (ok) "(code ran, no output)" else "(error with no output)" }
        return ToolResult(body, ok = ok)
    }
}

/** Инструменты на встроенном Python — регистрируются в обеих сборках. */
fun pythonTools(): List<Tool> = listOf(RunPythonTool())
