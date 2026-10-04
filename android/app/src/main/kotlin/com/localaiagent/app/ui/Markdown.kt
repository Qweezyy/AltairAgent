package com.localaiagent.app.ui

import androidx.compose.ui.unit.em
import androidx.compose.ui.res.stringResource
import com.localaiagent.app.R

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.BaselineShift
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.unit.isSpecified
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Markdown of model answers on Compose. Parsed once per text (in [remember]) into ready nodes, so a
 * streaming answer is not re-parsed per frame. Covers what models send: code, every heading level,
 * nested lists and task lists, tables, rules, nested quotes and GitHub alerts, footnotes, details,
 * images; inline bold/italic/strike/highlight/sup/sub/code/links/autolinks/inline HTML and escapes;
 * formulas ($…$, $$…$$, \(…\), \[…\], ```math) through KaTeX and ```mermaid diagrams through Mermaid.
 */
@Composable
fun MarkdownText(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.onBackground,
) {
    val codeColor = color
    val linkColor = MaterialTheme.colorScheme.primary
    // The heavy parse is cached: redone only when the text or the theme colours change.
    val nodes = remember(text, color, codeColor, linkColor) {
        parseMarkdown(text, color, codeColor, linkColor)
    }
    val spacing = LocalAnswerSpacing.current.first
    Column(modifier) {
        nodes.forEachIndexed { i, node ->
            // Lines of an answer get a little air between them, scaled with the line spacing.
            if (i > 0 && node !is MdNode.Blank && nodes[i - 1] !is MdNode.Blank) {
                Spacer(Modifier.height((3 * spacing).dp))
            }
            RenderNode(node, color, spacing)
        }
    }
}

/**
 * A text style with its line height scaled by the answer line spacing. The first and last lines keep
 * their full line height too (no trim), so separate lines sit exactly as far apart as wrapped ones.
 */
private fun TextStyle.spaced(f: Float): TextStyle =
    if (!lineHeight.isSpecified) this
    else copy(
        lineHeight = lineHeight * f,
        lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None),
    )

// ------------------------------------------------------------- rendering

@Composable
private fun RenderNode(node: MdNode, color: Color, spacing: Float) {
    // Sizes come from the type scale (Type.kt); prose line height follows the answer spacing setting.
    val typo = MaterialTheme.typography
    val body = typo.bodyLarge.spaced(spacing)
    when (node) {
        is MdNode.Code -> CodeBlock(node.content, node.lang)
        is MdNode.Blank -> Spacer(Modifier.height((10 * spacing).dp))
        is MdNode.Rule -> androidx.compose.material3.HorizontalDivider(
            Modifier.padding(vertical = (10 * spacing).dp),
            // Derived from the text colour: outlineVariant is nearly invisible on the black theme.
            thickness = 1.dp, color = color.copy(alpha = 0.2f),
        )
        is MdNode.Quote -> Row(Modifier.padding(vertical = 2.dp)) {
            // Nested quotes get one bar per level.
            repeat(node.level.coerceIn(1, 4)) {
                Box(
                    Modifier.width(3.dp).heightIn(min = 18.dp).clip(RoundedCornerShape(2.dp))
                        .background(MaterialTheme.colorScheme.primary.copy(alpha = if (it == 0) 1f else 0.5f)),
                )
                Spacer(Modifier.width(8.dp))
            }
            Text(
                node.text,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontStyle = FontStyle.Italic,
                style = typo.bodyMedium.spaced(spacing),
            )
        }
        is MdNode.Alert -> AlertBlock(node, color, spacing)
        is MdNode.ImageLine -> Column(Modifier.fillMaxWidth()) {
            for (seg in node.segments) when (seg) {
                is MdSegment.Text -> Text(seg.text, color = color, style = body)
                is MdSegment.Img -> InlineImage(seg.url, seg.caption)
            }
        }
        is MdNode.Header -> Text(
            node.text,
            fontWeight = FontWeight.Bold,
            color = color,
            style = when (node.level) {
                1 -> typo.headlineSmall
                2 -> typo.titleLarge
                3 -> typo.titleMedium
                else -> typo.titleSmall
            },
            modifier = Modifier.padding(top = (10 * spacing).dp, bottom = (2 * spacing).dp),
        )
        is MdNode.Bullet -> Row(Modifier.padding(start = (4 + 16 * node.depth).dp)) {
            Text(if (node.depth == 0) "•  " else "◦  ", color = color, style = body)
            Text(node.text, color = color, style = body)
        }
        is MdNode.Task -> Row(Modifier.padding(start = (4 + 16 * node.depth).dp)) {
            Text(
                if (node.done) "☑  " else "☐  ",
                color = if (node.done) MaterialTheme.colorScheme.primary else color, style = body,
            )
            Text(
                node.text, style = body,
                color = if (node.done) color.copy(alpha = 0.6f) else color,
                textDecoration = if (node.done) TextDecoration.LineThrough else null,
            )
        }
        is MdNode.Numbered -> Row(Modifier.padding(start = (4 + 16 * node.depth).dp)) {
            Text("${node.num}.  ", color = color, style = body)
            Text(node.text, color = color, style = body)
        }
        is MdNode.Para -> Text(node.text, color = color, style = body)
        is MdNode.Footnote -> Row {
            Text("${node.label}  ", color = MaterialTheme.colorScheme.primary, style = typo.bodySmall)
            Text(node.text, color = color.copy(alpha = 0.75f), style = typo.bodySmall.spaced(spacing))
        }
        is MdNode.Table -> TableBlock(node, color)
        is MdNode.Math -> MathBlock(node.tex, color, body.fontSize)
        is MdNode.MathText -> MathLine(node.html, color, body.fontSize, body.lineHeight)
        is MdNode.Mermaid -> MermaidBlock(node.code, color, typo.bodyMedium.fontSize)
        is MdNode.Details -> DetailsBlock(node, color, spacing)
    }
}

/** A GitHub alert: > [!NOTE] / [!TIP] / [!IMPORTANT] / [!WARNING] / [!CAUTION]. */
@Composable
private fun AlertBlock(node: MdNode.Alert, color: Color, spacing: Float) {
    val accent = when (node.kind) {
        "TIP" -> Color(0xFF3FB950)
        "IMPORTANT" -> Color(0xFFA371F7)
        "WARNING" -> Color(0xFFD29922)
        "CAUTION" -> Color(0xFFF85149)
        else -> MaterialTheme.colorScheme.primary
    }
    Row(
        Modifier.fillMaxWidth().padding(vertical = 4.dp).clip(RoundedCornerShape(10.dp))
            .background(accent.copy(alpha = 0.08f)),
    ) {
        Box(Modifier.width(3.dp).heightIn(min = 40.dp).background(accent))
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            Text(
                stringResource(
                    when (node.kind) {
                        "TIP" -> R.string.md_alert_tip
                        "IMPORTANT" -> R.string.md_alert_important
                        "WARNING" -> R.string.md_alert_warning
                        "CAUTION" -> R.string.md_alert_caution
                        else -> R.string.md_alert_note
                    },
                ),
                color = accent,
                fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.labelLarge,
            )
            Text(node.text, color = color, style = MaterialTheme.typography.bodyMedium.spaced(spacing))
        }
    }
}

/** <details><summary>…</summary>…</details>: folded until tapped. */
@Composable
private fun DetailsBlock(node: MdNode.Details, color: Color, spacing: Float) {
    var open by rememberSaveable(node.summary, node.body) { mutableStateOf(false) }
    Column(
        Modifier.fillMaxWidth().padding(vertical = 4.dp).clip(RoundedCornerShape(10.dp))
            .border(1.dp, color.copy(alpha = 0.15f), RoundedCornerShape(10.dp)),
    ) {
        Text(
            (if (open) "▾  " else "▸  ") + node.summary.ifBlank { stringResource(R.string.md_details) },
            color = color, fontWeight = FontWeight.SemiBold,
            style = MaterialTheme.typography.bodyLarge.spaced(spacing),
            modifier = Modifier.fillMaxWidth().clickable { open = !open }.padding(horizontal = 12.dp, vertical = 8.dp),
        )
        if (open) MarkdownText(node.body, Modifier.padding(start = 12.dp, end = 12.dp, bottom = 8.dp), color)
    }
}

/** Рендер GFM-таблицы: рамка, шапка на surface, ячейки равной ширины, перенос текста. */
@Composable
private fun TableBlock(node: MdNode.Table, color: Color) {
    val border = MaterialTheme.colorScheme.outlineVariant
    val cols = node.header.size.coerceAtLeast(1)
    Column(
        Modifier.fillMaxWidth().padding(vertical = 6.dp)
            .clip(RoundedCornerShape(10.dp))
            .border(1.dp, border, RoundedCornerShape(10.dp)),
    ) {
        Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerHigh)) {
            for (i in 0 until cols) TableCell(node.header.getOrNull(i) ?: AnnotatedString(""), color, true)
        }
        node.rows.forEach { row ->
            androidx.compose.material3.HorizontalDivider(color = border, thickness = 1.dp)
            Row(Modifier.fillMaxWidth()) {
                for (i in 0 until cols) TableCell(row.getOrNull(i) ?: AnnotatedString(""), color, false)
            }
        }
    }
}

@Composable
private fun androidx.compose.foundation.layout.RowScope.TableCell(
    text: AnnotatedString, color: Color, bold: Boolean,
) {
    Text(
        text, color = color,
        modifier = Modifier.weight(1f).padding(horizontal = 10.dp, vertical = 8.dp),
        style = MaterialTheme.typography.bodyMedium,
        fontWeight = if (bold) FontWeight.SemiBold else FontWeight.Normal,
    )
}

@Composable
private fun InlineImage(url: String, caption: String) {
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        val model: Any = if (url.startsWith("http")) url else java.io.File(url)
        coil.compose.AsyncImage(
            model = model,
            contentDescription = caption,
            modifier = Modifier.fillMaxWidth()
                .heightIn(max = 360.dp)
                .clip(RoundedCornerShape(14.dp)),
            contentScale = androidx.compose.ui.layout.ContentScale.Fit,
        )
        if (caption.isNotBlank()) {
            Text(caption, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.outline, modifier = Modifier.padding(top = 3.dp))
        }
    }
}

@Composable
private fun CodeBlock(code: String, lang: String = "") {
    val clipboard = LocalClipboardManager.current
    val body = code.trimEnd('\n')
    val alt = com.localaiagent.app.ui.theme.LocalAltair.current
    // As on the PC: a quiet header (language + copy) over a core plate, both on a hairline.
    Column(
        Modifier.fillMaxWidth().padding(vertical = 6.dp)
            .border(1.dp, alt.hair, RoundedCornerShape(12.dp))
            .clip(RoundedCornerShape(12.dp)),
    ) {
        Row(
            Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.7f))
                .padding(start = 12.dp, end = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                lang.ifBlank { stringResource(R.string.code_block_label) }, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f),
            )
            IconButton(onClick = { clipboard.setText(AnnotatedString(body)) }, modifier = Modifier.size(34.dp)) {
                Icon(
                    Icons.Rounded.ContentCopy, stringResource(R.string.copy_code),
                    Modifier.size(15.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(alt.hair))
        Text(
            body,
            modifier = Modifier.fillMaxWidth().background(alt.core)
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 14.dp, vertical = 12.dp),
            fontFamily = com.localaiagent.app.ui.theme.AppMono,
            fontSize = 14.sp,
            lineHeight = 21.sp,
            color = MaterialTheme.colorScheme.onSurface,
        )
    }
}

// ------------------------------------------------------------- parse model

/** A markdown node ready to render (parsed up front, outside composition). */
internal sealed interface MdNode {
    data class Code(val content: String, val lang: String = "") : MdNode
    object Blank : MdNode
    /** A thematic break: ---, *** or ___ on a line of its own. */
    object Rule : MdNode
    data class Quote(val text: AnnotatedString, val level: Int = 1) : MdNode
    /** A GitHub alert: > [!NOTE] and its lines. */
    data class Alert(val kind: String, val text: AnnotatedString) : MdNode
    data class Header(val level: Int, val text: AnnotatedString) : MdNode
    data class Bullet(val text: AnnotatedString, val depth: Int = 0) : MdNode
    data class Task(val done: Boolean, val text: AnnotatedString, val depth: Int = 0) : MdNode
    data class Numbered(val num: String, val text: AnnotatedString, val depth: Int = 0) : MdNode
    data class Para(val text: AnnotatedString) : MdNode
    data class Footnote(val label: String, val text: AnnotatedString) : MdNode
    data class ImageLine(val segments: List<MdSegment>) : MdNode
    data class Table(val header: List<AnnotatedString>, val rows: List<List<AnnotatedString>>) : MdNode
    /** A display formula. */
    data class Math(val tex: String) : MdNode
    /** A line with inline formulas, as HTML with KaTeX placeholders. */
    data class MathText(val html: String) : MdNode
    data class Mermaid(val code: String) : MdNode
    data class Details(val summary: String, val body: String) : MdNode
}

internal sealed interface MdSegment {
    data class Text(val text: AnnotatedString) : MdSegment
    data class Img(val url: String, val caption: String) : MdSegment
}

private sealed interface Block {
    data class Text(val content: String) : Block
    data class Code(val content: String, val lang: String) : Block
    data class Math(val tex: String) : Block
    data class Details(val summary: String, val body: String) : Block
}

// Compiled once (they used to be compiled per line on every recomposition).
private val IMG_RE = Regex("!\\[([^\\]]*)\\]\\(([^)\\s]+)(?:\\s+\"[^\"]*\")?\\)")
// ATX headings as in CommonMark: up to three spaces, 1-6 hashes, then a space or the end of the line.
private val HEADER_RE = Regex("^ {0,3}(#{1,6})(?:[ \\t]+(.*?))?[ \\t]*$")
// The optional closing run of hashes ("## Title ##"), which must follow a space.
private val HEADER_CLOSE_RE = Regex("(?:^|[ \\t]+)#+[ \\t]*$")
// A thematic break: three or more -, * or _ (spaces between allowed), nothing else.
private val RULE_RE = Regex("^ {0,3}([-*_])(?:[ \\t]*\\1){2,}[ \\t]*$")
// A setext "===" underline turns the paragraph line above into a level-1 heading. A "---" under text
// stays a rule: models use it as a section separator far more often than as an underline.
private val SETEXT_RE = Regex("^ {0,3}=+[ \\t]*$")
private val TASK_RE = Regex("^(\\s*)[-*+]\\s+\\[([ xX])]\\s+(.*)")
private val BULLET_RE = Regex("^(\\s*)[-*+]\\s+(.*)")
private val NUMBERED_RE = Regex("^(\\s*)(\\d{1,9})[.)]\\s+(.*)")
private val QUOTE_RE = Regex("^\\s*((?:>\\s?)+)(.*)")
private val ALERT_RE = Regex("^\\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)]\\s*(.*)", RegexOption.IGNORE_CASE)
private val FOOTNOTE_DEF_RE = Regex("^\\[\\^([^\\]]+)]:\\s*(.*)")
private val LINK_RE = Regex("^\\[([^\\]]+)]\\(([^)\\s]+)(?:\\s+\"[^\"]*\")?\\)")
private val AUTOLINK_RE = Regex("^<((?:https?|mailto):[^>\\s]+)>")
private val BARE_URL_RE = Regex("^https?://[^\\s<>()\\[\\]]*[^\\s<>()\\[\\].,;:!?\"')]")
private val FOOTNOTE_REF_RE = Regex("^\\[\\^([^\\]]+)]")
private val HTML_TAG_RE = Regex("^<(/?)(b|strong|i|em|u|s|del|strike|mark|sup|sub|kbd|code|small)>", RegexOption.IGNORE_CASE)
private val BR_RE = Regex("^<br\\s*/?>", RegexOption.IGNORE_CASE)
// Inline formulas: \( … \) or $ … $ (not $$; no space inside the dollars; "$5 and $10" is money).
private val INLINE_MATH_RE = Regex("\\\\\\((.+?)\\\\\\)|(?<![\\\\$\\w])\\$(?![\\s$])((?:\\\\.|[^$\\n])+?)(?<![\\s\\\\])\\$(?![\\w$])")
private const val ESCAPABLE = "\\`*_{}[]()#+-.!|~=^$<>\""

/** Parses the whole text into ready nodes; called once per text inside remember. */
internal fun parseMarkdown(
    text: String,
    color: Color,
    codeColor: Color,
    linkColor: Color,
): List<MdNode> {
    val nodes = mutableListOf<MdNode>()
    for (block in splitBlocks(text)) {
        when (block) {
            is Block.Code -> nodes += when (block.lang.lowercase()) {
                "math", "latex", "tex", "katex" -> MdNode.Math(block.content)
                "mermaid" -> MdNode.Mermaid(block.content)
                else -> MdNode.Code(block.content, block.lang)
            }
            is Block.Math -> nodes += MdNode.Math(block.tex)
            is Block.Details -> nodes += MdNode.Details(block.summary, block.body)
            is Block.Text -> parseTextBlock(block.content, nodes, color, codeColor, linkColor)
        }
    }
    return nodes
}

private fun parseTextBlock(
    content: String,
    nodes: MutableList<MdNode>,
    color: Color,
    codeColor: Color,
    linkColor: Color,
) {
    val lines = content.split("\n")
    var i = 0
    while (i < lines.size) {
        val line = lines[i]
        // A GFM table: a line with "|" followed by a separator line (---|:--:|).
        if (i + 1 < lines.size && line.contains("|") && isTableSeparator(lines[i + 1])) {
            val header = splitTableRow(line)
            i += 2
            val rows = mutableListOf<List<String>>()
            while (i < lines.size && lines[i].contains("|") && lines[i].isNotBlank() && !isTableSeparator(lines[i])) {
                rows += splitTableRow(lines[i]); i++
            }
            val cols = header.size
            fun pad(r: List<String>) = (0 until cols).map { r.getOrElse(it) { "" } }
            nodes += MdNode.Table(
                header = header.map { parseInline(it, codeColor, linkColor) },
                rows = rows.map { pad(it).map { c -> parseInline(c, codeColor, linkColor) } },
            )
            continue
        }
        // A GitHub alert takes the quote lines that follow it.
        val quote = QUOTE_RE.find(line)
        val alert = quote?.let { ALERT_RE.find(it.groupValues[2].trim()) }
        if (alert != null) {
            val body = mutableListOf<String>()
            alert.groupValues[2].takeIf { it.isNotBlank() }?.let { body += it }
            i++
            while (i < lines.size) {
                val q = QUOTE_RE.find(lines[i]) ?: break
                body += q.groupValues[2]; i++
            }
            nodes += MdNode.Alert(alert.groupValues[1].uppercase(), parseInline(body.joinToString("\n"), codeColor, linkColor))
            continue
        }
        val setext = SETEXT_RE.find(line)
        val prev = nodes.lastOrNull()
        if (setext != null && prev is MdNode.Para) {
            nodes[nodes.lastIndex] = MdNode.Header(1, prev.text)
        } else {
            nodes += parseLine(line, color, codeColor, linkColor)
        }
        i++
    }
}

/** A table's header separator: only |, -, :, spaces, with at least one "-". */
private fun isTableSeparator(line: String): Boolean {
    val t = line.trim()
    if (!t.contains('-')) return false
    return t.all { it == '|' || it == '-' || it == ':' || it == ' ' } && t.contains('|')
}

/** Splits a table row into cells: drops the outer "|" and splits by "|" (an escaped \| stays). */
private fun splitTableRow(line: String): List<String> =
    line.trim().trim('|').split(Regex("(?<!\\\\)\\|")).map { it.trim().replace("\\|", "|") }

private fun depthOf(indent: String) = (indent.replace("\t", "    ").length / 2).coerceAtMost(3)

private fun parseLine(line: String, color: Color, codeColor: Color, linkColor: Color): MdNode {
    if (line.isBlank()) return MdNode.Blank
    // Checked before bullets: "* * *" and "- - -" are rules, not list items.
    if (RULE_RE.matches(line)) return MdNode.Rule
    FOOTNOTE_DEF_RE.find(line.trim())?.let {
        return MdNode.Footnote(it.groupValues[1], parseInline(it.groupValues[2], codeColor, linkColor))
    }
    val quote = QUOTE_RE.find(line)
    if (quote != null) {
        val level = quote.groupValues[1].count { it == '>' }
        return MdNode.Quote(parseInline(quote.groupValues[2], codeColor, linkColor), level)
    }
    // A line with inline formulas is drawn by KaTeX (only plain prose lines; list items keep their marker).
    val header = HEADER_RE.find(line)
    if (header == null && hasInlineMath(line) && !IMG_RE.containsMatchIn(line)) {
        val item = TASK_RE.find(line) ?: BULLET_RE.find(line)
        val num = NUMBERED_RE.find(line)
        val (prefix, rest) = when {
            item != null -> (if (item.groupValues.size > 3) "☐ " else "• ") to item.groupValues.last()
            num != null -> "${num.groupValues[2]}. " to num.groupValues[3]
            else -> "" to line
        }
        return MdNode.MathText(escapeHtml(prefix) + inlineMathHtml(rest))
    }
    if (IMG_RE.containsMatchIn(line)) {
        val segments = mutableListOf<MdSegment>()
        var last = 0
        for (m in IMG_RE.findAll(line)) {
            val pre = line.substring(last, m.range.first)
            if (pre.isNotBlank()) segments += MdSegment.Text(parseInline(pre, codeColor, linkColor))
            segments += MdSegment.Img(m.groupValues[2], m.groupValues[1])
            last = m.range.last + 1
        }
        val tail = line.substring(last)
        if (tail.isNotBlank()) segments += MdSegment.Text(parseInline(tail, codeColor, linkColor))
        return MdNode.ImageLine(segments)
    }
    if (header != null) {
        // A bare "###" is an empty heading: it shows as a little space, never as hashes.
        val title = header.groupValues[2].replace(HEADER_CLOSE_RE, "").trim()
        return if (title.isEmpty()) MdNode.Blank
        else MdNode.Header(header.groupValues[1].length, parseInline(title, codeColor, linkColor))
    }
    TASK_RE.find(line)?.let {
        return MdNode.Task(it.groupValues[2].isNotBlank(), parseInline(it.groupValues[3], codeColor, linkColor), depthOf(it.groupValues[1]))
    }
    BULLET_RE.find(line)?.let {
        return MdNode.Bullet(parseInline(it.groupValues[2], codeColor, linkColor), depthOf(it.groupValues[1]))
    }
    NUMBERED_RE.find(line)?.let {
        return MdNode.Numbered(it.groupValues[2], parseInline(it.groupValues[3], codeColor, linkColor), depthOf(it.groupValues[1]))
    }
    return MdNode.Para(parseInline(line, codeColor, linkColor))
}

internal fun hasInlineMath(line: String): Boolean = INLINE_MATH_RE.containsMatchIn(line)

/**
 * Splits the text into code fences (``` or ~~~, any language), display formulas ($$…$$ and \[…\],
 * on one line or many), <details> sections and plain text.
 */
private fun splitBlocks(text: String): List<Block> {
    val blocks = mutableListOf<Block>()
    val lines = text.split("\n")
    val buf = StringBuilder()
    fun flushText() {
        if (buf.isNotEmpty()) { blocks += Block.Text(buf.toString()); buf.clear() }
    }
    var i = 0
    while (i < lines.size) {
        val line = lines[i]
        val t = line.trim()
        val fence = Regex("^(`{3,}|~{3,})(.*)$").find(t)
        when {
            fence != null -> {
                flushText()
                val mark = fence.groupValues[1]
                // The fence's info string ("```bash") names the language shown in the code header.
                val lang = fence.groupValues[2].trim().substringBefore(' ')
                val code = StringBuilder()
                i++
                while (i < lines.size && !lines[i].trim().startsWith(mark)) {
                    if (code.isNotEmpty()) code.append('\n')
                    code.append(lines[i]); i++
                }
                blocks += Block.Code(code.toString(), lang)
                i++
            }
            t.startsWith("$$") || t.startsWith("\\[") -> {
                val close = if (t.startsWith("$$")) "$$" else "\\]"
                val first = t.removePrefix(if (close == "$$") "$$" else "\\[")
                if (first.trimEnd().endsWith(close) && first.trim().length > close.length - 1) {
                    flushText()
                    blocks += Block.Math(first.trimEnd().removeSuffix(close))
                    i++
                } else {
                    // Multi-line: up to the closing line. An unclosed formula (still streaming) stays text.
                    var j = i + 1
                    while (j < lines.size && !lines[j].trimEnd().endsWith(close)) j++
                    if (j >= lines.size) { buf.append(if (buf.isEmpty()) line else "\n$line"); i++ }
                    else {
                        flushText()
                        val tex = (listOf(first) + lines.subList(i + 1, j) + lines[j].trimEnd().removeSuffix(close))
                            .joinToString("\n")
                        blocks += Block.Math(tex)
                        i = j + 1
                    }
                }
            }
            t.startsWith("<details", ignoreCase = true) -> {
                var j = i
                while (j < lines.size && !lines[j].contains("</details>", ignoreCase = true)) j++
                if (j >= lines.size) { buf.append(if (buf.isEmpty()) line else "\n$line"); i++ }
                else {
                    flushText()
                    val raw = lines.subList(i, j + 1).joinToString("\n")
                    val summary = Regex("<summary>(.*?)</summary>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
                        .find(raw)?.groupValues?.get(1)?.trim().orEmpty()
                    val body = raw.replace(Regex("<summary>.*?</summary>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)), "")
                        .replace(Regex("</?details[^>]*>", RegexOption.IGNORE_CASE), "").trim()
                    // A missing summary is labelled on screen, in the user's language.
                    blocks += Block.Details(summary, body)
                    i = j + 1
                }
            }
            else -> {
                if (buf.isNotEmpty()) buf.append('\n')
                buf.append(line)
                i++
            }
        }
    }
    flushText()
    return blocks
}

/**
 * A prose line with inline formulas as HTML: formulas become KaTeX placeholders, the rest keeps
 * bold, italic, strike, code and links.
 */
internal fun inlineMathHtml(line: String): String {
    val out = StringBuilder()
    var last = 0
    for (m in INLINE_MATH_RE.findAll(line)) {
        out.append(inlineHtml(line.substring(last, m.range.first)))
        val tex = m.groupValues[1].ifEmpty { m.groupValues[2] }
        out.append("<span class=\"m\" data-t=\"").append(escapeHtml(tex)).append("\"></span>")
        last = m.range.last + 1
    }
    out.append(inlineHtml(line.substring(last)))
    return out.toString()
}

private fun inlineHtml(s: String): String {
    var h = escapeHtml(s)
    h = h.replace(Regex("`([^`]+)`"), "<code>$1</code>")
    h = h.replace(Regex("\\*\\*(.+?)\\*\\*|__(.+?)__")) { "<b>${it.groupValues[1].ifEmpty { it.groupValues[2] }}</b>" }
    h = h.replace(Regex("(?<![*\\w])[*_](?!\\s)(.+?)(?<!\\s)[*_](?![*\\w])"), "<i>$1</i>")
    h = h.replace(Regex("~~(.+?)~~"), "<s>$1</s>")
    h = h.replace(Regex("\\[([^\\]]+)]\\((https?://[^)\\s]+)\\)"), "<a href=\"$2\">$1</a>")
    return h
}

/**
 * Inline markup → AnnotatedString: **bold**, *italic*, ***both***, ~~strike~~, ==highlight==,
 * ^sup^, ~sub~, `code`, [text](url), <url>, bare URLs, footnote refs [^1], inline HTML tags
 * (b, i, u, s, mark, sup, sub, kbd, code, br) and backslash escapes. A pure function: colours come
 * as parameters, so the parse can be cached outside composition.
 */
private fun parseInline(text: String, codeColor: Color, linkColor: Color): AnnotatedString =
    buildAnnotatedString {
        val codeStyle = SpanStyle(
            fontFamily = com.localaiagent.app.ui.theme.AppMono, fontSize = 0.9.em,
            background = codeColor.copy(alpha = 0.08f),
        )
        // Open inline HTML tags, closed by their </tag>.
        val openTags = ArrayDeque<String>()
        fun tagStyle(tag: String): SpanStyle = when (tag) {
            "b", "strong" -> SpanStyle(fontWeight = FontWeight.Bold)
            "i", "em" -> SpanStyle(fontStyle = FontStyle.Italic)
            "u" -> SpanStyle(textDecoration = TextDecoration.Underline)
            "s", "del", "strike" -> SpanStyle(textDecoration = TextDecoration.LineThrough)
            "mark" -> SpanStyle(background = linkColor.copy(alpha = 0.25f))
            "sup" -> SpanStyle(baselineShift = BaselineShift.Superscript, fontSize = 0.75.em)
            "sub" -> SpanStyle(baselineShift = BaselineShift.Subscript, fontSize = 0.75.em)
            "small" -> SpanStyle(fontSize = 0.85.em)
            else -> codeStyle // kbd, code
        }
        // A span with matching closing delimiter; the inside is parsed again (nesting works).
        fun spanned(i: Int, open: String, close: String, style: SpanStyle): Int? {
            if (text.length <= i + open.length || text[i + open.length].isWhitespace()) return null
            val end = text.indexOf(close, i + open.length)
            if (end <= i + open.length || text[end - 1].isWhitespace()) return null
            withStyle(style) { append(parseInline(text.substring(i + open.length, end), codeColor, linkColor)) }
            return end + close.length
        }
        var i = 0
        while (i < text.length) {
            val rest = text.substring(i)
            val c = text[i]
            var next: Int? = null
            when {
                c == '\\' && i + 1 < text.length && text[i + 1] in ESCAPABLE -> {
                    append(text[i + 1]); next = i + 2
                }
                c == '`' -> {
                    val ticks = rest.takeWhile { it == '`' }
                    val end = text.indexOf(ticks, i + ticks.length)
                    if (end > 0) {
                        // Inline code as on the PC: mono on a faint pill, in the text colour.
                        withStyle(codeStyle) { append(text.substring(i + ticks.length, end).trim()) }
                        next = end + ticks.length
                    }
                }
                rest.startsWith("***") -> next = spanned(i, "***", "***", SpanStyle(fontWeight = FontWeight.Bold, fontStyle = FontStyle.Italic))
                rest.startsWith("**") -> next = spanned(i, "**", "**", SpanStyle(fontWeight = FontWeight.Bold))
                rest.startsWith("__") -> next = spanned(i, "__", "__", SpanStyle(fontWeight = FontWeight.Bold))
                rest.startsWith("~~") -> next = spanned(i, "~~", "~~", SpanStyle(textDecoration = TextDecoration.LineThrough))
                rest.startsWith("==") -> next = spanned(i, "==", "==", SpanStyle(background = linkColor.copy(alpha = 0.25f)))
                c == '^' -> next = spanned(i, "^", "^", SpanStyle(baselineShift = BaselineShift.Superscript, fontSize = 0.75.em))
                c == '~' -> next = spanned(i, "~", "~", SpanStyle(baselineShift = BaselineShift.Subscript, fontSize = 0.75.em))
                c == '[' -> {
                    val fn = FOOTNOTE_REF_RE.find(rest)
                    val m = LINK_RE.find(rest)
                    if (fn != null) {
                        withStyle(SpanStyle(baselineShift = BaselineShift.Superscript, fontSize = 0.75.em, color = linkColor)) {
                            append(fn.groupValues[1])
                        }
                        next = i + fn.value.length
                    } else if (m != null) {
                        withLink(LinkAnnotation.Url(m.groupValues[2])) {
                            withStyle(SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline)) {
                                append(parseInline(m.groupValues[1], codeColor, linkColor))
                            }
                        }
                        next = i + m.value.length
                    }
                }
                c == '<' -> {
                    val auto = AUTOLINK_RE.find(rest)
                    val br = BR_RE.find(rest)
                    val tag = HTML_TAG_RE.find(rest)
                    when {
                        auto != null -> {
                            val url = auto.groupValues[1]
                            withLink(LinkAnnotation.Url(url)) {
                                withStyle(SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline)) {
                                    append(url.removePrefix("mailto:"))
                                }
                            }
                            next = i + auto.value.length
                        }
                        br != null -> { append('\n'); next = i + br.value.length }
                        tag != null -> {
                            val name = tag.groupValues[2].lowercase()
                            if (tag.groupValues[1].isEmpty()) {
                                // An opening tag: style the text up to its closing tag.
                                val close = "</$name>"
                                val end = text.indexOf(close, i + tag.value.length, ignoreCase = true)
                                if (end > 0) {
                                    withStyle(tagStyle(name)) {
                                        append(parseInline(text.substring(i + tag.value.length, end), codeColor, linkColor))
                                    }
                                    next = end + close.length
                                } else { openTags.addLast(name); next = i + tag.value.length }
                            } else next = i + tag.value.length // a stray closing tag
                        }
                    }
                }
                c == 'h' && (rest.startsWith("http://") || rest.startsWith("https://")) &&
                    (i == 0 || !text[i - 1].isLetterOrDigit()) -> {
                    BARE_URL_RE.find(rest)?.let { m ->
                        withLink(LinkAnnotation.Url(m.value)) {
                            withStyle(SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline)) { append(m.value) }
                        }
                        next = i + m.value.length
                    }
                }
                (c == '*' || c == '_') && rest.length > 1 && !rest[1].isWhitespace() &&
                    // snake_case and 2*3*4 are not emphasis.
                    !(c == '_' && i > 0 && text[i - 1].isLetterOrDigit()) -> {
                    val end = text.indexOf(c, i + 1)
                    if (end > 0 && !text[end - 1].isWhitespace() &&
                        !(c == '_' && end + 1 < text.length && text[end + 1].isLetterOrDigit())
                    ) {
                        withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                            append(parseInline(text.substring(i + 1, end), codeColor, linkColor))
                        }
                        next = end + 1
                    }
                }
            }
            val n = next
            if (n == null) { append(c); i += 1 } else i = n
        }
    }
