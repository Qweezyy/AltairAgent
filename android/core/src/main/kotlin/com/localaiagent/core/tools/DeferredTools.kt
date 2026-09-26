package com.localaiagent.core.tools

import com.localaiagent.core.Message
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolRegistry
import kotlin.math.ln

/**
 * Deferred tool loading — the same approach as the PC agent (pc/core/tools/deferred.py).
 *
 * Sending every tool schema on every request costs thousands of tokens and a long tool list
 * measurably hurts tool selection. So only a small core set goes up front; every other tool is
 * listed by name in the system prompt and becomes available once the model loads it with
 * `tool_search` (or simply calls it by name).
 *
 * The active set is derived from the conversation itself — core tools, tools loaded in this
 * run, and every tool already called in the history — so it only grows within a chat, which keeps
 * the request prefix stable.
 */
object DeferredTools {
    /** Always sent: everyday chat, web, files, code and memory. */
    val CORE: Set<String> = setOf(
        "tool_search",
        "ask",
        "calc",
        "web_search",
        "fetch_url",
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "run_python",
        "remember",
        "show_image",
        "find_skills",
        "use_skill",
        "pc_agent",
    )

    /** A registry this small is sent whole: deferral would only add a search round trip. */
    const val MIN_TOOLS_TO_DEFER = 20

    /** ctx.scratch key with the names tool_search loaded during the current run. */
    const val LOADED_KEY = "_loaded_tools"

    fun active(registry: ToolRegistry): Boolean =
        registry.get("tool_search") != null && registry.names().size >= MIN_TOOLS_TO_DEFER

    /** Tools that are not sent up front (sorted), or empty when deferral is off. */
    fun deferredNames(registry: ToolRegistry): List<String> =
        if (!active(registry)) emptyList() else registry.names().filter { it !in CORE }.sorted()

    fun calledToolNames(messages: List<Message>): Set<String> =
        messages.flatMap { m -> m.toolCalls.map { it.name } }.toSet()

    /** Tools to send with the next request, or null for "all of them". */
    fun activeNames(registry: ToolRegistry, messages: List<Message>, scratch: Map<String, Any?>): List<String>? {
        if (!active(registry)) return null
        @Suppress("UNCHECKED_CAST")
        val loaded = (scratch[LOADED_KEY] as? List<String>).orEmpty()
        val wanted = CORE + calledToolNames(messages) + loaded
        return registry.names().filter { it in wanted }
    }

    fun markLoaded(scratch: MutableMap<String, Any?>, names: List<String>) {
        @Suppress("UNCHECKED_CAST")
        val loaded = (scratch[LOADED_KEY] as? MutableList<String>) ?: mutableListOf<String>().also {
            scratch[LOADED_KEY] = it
        }
        names.forEach { if (it !in loaded) loaded += it }
    }

    private val WORD = Regex("[a-zа-яё0-9]+", RegexOption.IGNORE_CASE)

    // Tool names are snake_case: "set_reminder" must match both "set" and "reminder".
    private fun words(text: String): List<String> =
        WORD.findAll(text.replace('_', ' ')).map { it.value.lowercase() }.toList()

    /** BM25-style ranking over name + description; a hit in the name weighs triple. */
    fun rank(tools: List<Tool>, query: String, limit: Int): List<Tool> {
        val terms = words(query)
        if (terms.isEmpty() || tools.isEmpty()) return emptyList()
        val docs = tools.associate { t -> t.name to (List(3) { words(t.name) }.flatten() + words(t.description)) }
        val avgLen = docs.values.sumOf { it.size }.toDouble() / docs.size.coerceAtLeast(1)
        val df = HashMap<String, Int>()
        docs.values.forEach { doc -> doc.toSet().forEach { df[it] = (df[it] ?: 0) + 1 } }

        fun score(t: Tool): Double {
            val doc = docs.getValue(t.name)
            val counts = doc.groupingBy { it }.eachCount()
            var total = 0.0
            for (term in terms) {
                var tf = counts[term] ?: 0
                // A prefix hit: "remind" finds "reminder", "reminders".
                if (term.length >= 4) tf += counts.filterKeys { it != term && it.startsWith(term) }.values.sum()
                if (tf == 0) continue
                val n = df[term] ?: 0
                val idf = ln(1 + (docs.size - n + 0.5) / (n + 0.5))
                total += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * doc.size / avgLen))
            }
            return total
        }

        return tools.map { score(it) to it }
            .filter { it.first > 0 }
            .sortedWith(compareByDescending<Pair<Double, Tool>> { it.first }.thenBy { it.second.name })
            .take(limit)
            .map { it.second }
    }
}
