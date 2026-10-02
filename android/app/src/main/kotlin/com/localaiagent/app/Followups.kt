package com.localaiagent.app

/**
 * The quick-reply line the model adds at the end of an answer: `?>> option1 || option2 || option3`.
 *
 * Models are loose with it, so this accepts what they actually send: `?>>`, `!>>` or `? >>`, the line
 * wrapped in backticks, bold or a list bullet, `|` instead of `||`, quoted options, and text after
 * the line. A marker inside a code block is code, not a quick reply.
 */
object Followups {
    private val MARKER = Regex("""^[\s>*_`\-•]*[?!]\s?>>\s*(.*)$""")
    private const val MAX_OPTIONS = 3
    private const val MAX_LEN = 80

    /** Splits [text] into the answer without the quick-reply line and up to three options. */
    fun extract(text: String): Pair<String, List<String>> {
        val lines = text.lines()
        var inCode = false
        var at = -1
        var options: List<String> = emptyList()
        lines.forEachIndexed { i, raw ->
            if (raw.trimStart().startsWith("```")) { inCode = !inCode; return@forEachIndexed }
            if (inCode) return@forEachIndexed
            val m = MARKER.find(raw) ?: return@forEachIndexed
            val parsed = split(m.groupValues[1])
            if (parsed.isNotEmpty()) { at = i; options = parsed }
        }
        if (at < 0) return text to emptyList()
        val clean = (lines.take(at) + lines.drop(at + 1)).joinToString("\n").trimEnd()
        return clean to options
    }

    /** The answer as it may be shown while it streams: a half-written quick-reply line is hidden. */
    fun hideWhileStreaming(text: String): String {
        val lastLine = text.substringAfterLast('\n')
        return if (Regex("""^[\s>*_`\-•]*[?!]\s?>?""").matches(lastLine) || MARKER.containsMatchIn(lastLine)) {
            text.substringBeforeLast('\n', "").trimEnd()
        } else {
            text
        }
    }

    private fun split(body: String): List<String> {
        val cleaned = body.trim().trim('`', '*', '_').trim()
        val parts = if (cleaned.contains("||")) cleaned.split("||") else cleaned.split("|")
        return parts
            .map { it.trim().trim('`', '*', '_', '"', '«', '»', '“', '”').trim() }
            .filter { it.isNotEmpty() && it.length <= MAX_LEN }
            .distinct()
            .take(MAX_OPTIONS)
    }
}
