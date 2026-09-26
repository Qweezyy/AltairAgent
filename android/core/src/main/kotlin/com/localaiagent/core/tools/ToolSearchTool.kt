package com.localaiagent.core.tools

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/** tool_search — loads deferred tools on demand (see [DeferredTools]). */
class ToolSearchTool : Tool {
    override val name = "tool_search"
    override val description =
        "Loads tools that are not active yet. The system prompt lists them by name under " +
            "<deferred_tools>; search by keywords or load exact names with 'select:a,b'. Loaded tools " +
            "become callable from your next step."
    override val category = ToolCategory.READ

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("query") {
                put("type", "string")
                put(
                    "description",
                    "Keywords describing the capability you need (e.g. 'reminder timer', 'pc files'), " +
                        "or 'select:name1,name2' to load tools by exact name",
                )
            }
            putJsonObject("max_results") {
                put("type", "integer")
                put("description", "How many tools to load (1-15, default 5)")
            }
        }
        putJsonArray("required") { add("query") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val registry = ctx.registry ?: return ToolResult.fail("Tool search is unavailable outside an agent run.")
        val query = args["query"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (query.isEmpty()) return ToolResult.fail("Empty query.")
        val limit = (args["max_results"]?.jsonPrimitive?.intOrNull ?: 5).coerceIn(1, 15)
        val candidates = registry.all().filter { it.name != name }

        val unknown = mutableListOf<String>()
        val found: List<Tool> = if (query.startsWith("select:", ignoreCase = true)) {
            query.substringAfter(':').split(',').map { it.trim() }.filter { it.isNotEmpty() }.mapNotNull { n ->
                registry.get(n) ?: run { unknown += n; null }
            }
        } else {
            DeferredTools.rank(candidates, query, limit)
        }

        if (found.isEmpty()) {
            val hint = if (unknown.isNotEmpty()) " Unknown: ${unknown.joinToString(", ")}." else ""
            return ToolResult.fail(
                "No tools match '$query'.$hint Try other keywords, or pick a name from <deferred_tools>.",
            )
        }
        DeferredTools.markLoaded(ctx.scratch, found.map { it.name })
        val lines = mutableListOf("Loaded ${found.size} tool(s); they are callable from your next step:")
        for (t in found) lines += "- ${t.name}(${signature(t.schema())}): ${t.description}"
        if (unknown.isNotEmpty()) lines += "Not found: ${unknown.joinToString(", ")}."
        return ToolResult(lines.joinToString("\n"))
    }

    /**
     * Real parameters with types, optional ones marked '?'. MCP tools come with arbitrary schemas, and
     * a bare list of names made the model guess types and which arguments it must pass.
     */
    internal fun signature(schema: JsonObject): String {
        val props = schema["properties"] as? JsonObject ?: return "no parameters"
        if (props.isEmpty()) return "no parameters"
        val required = (schema["required"] as? kotlinx.serialization.json.JsonArray)
            ?.mapNotNull { (it as? kotlinx.serialization.json.JsonPrimitive)?.contentOrNull }?.toSet().orEmpty()
        return props.entries.joinToString(", ") { (k, v) ->
            val type = ((v as? JsonObject)?.get("type") as? kotlinx.serialization.json.JsonPrimitive)?.contentOrNull
            (if (k in required) k else "$k?") + (type?.let { ": $it" } ?: "")
        }
    }
}
