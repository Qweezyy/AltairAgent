package com.localaiagent.app.ui

import androidx.compose.ui.unit.em
import androidx.compose.ui.res.stringResource
import com.localaiagent.app.R

import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
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
 * Лёгкий рендер Markdown на Compose (без внешних зависимостей). Покрывает 90%
 * случаев ответов модели: блоки кода ```…```, заголовки, списки, и инлайн —
 * **жирный**, *курсив*, `код`, [ссылки](url). Остальное показывается как текст.
 *
 * Производительность: весь разбор (fences + инлайн-разметка в [AnnotatedString])
 * происходит ОДИН раз в [remember] по (text, цвета) — при стриминге ответа мы больше
 * не переразбираем весь текст на каждый токен. Рендер-композаблы лёгкие: только
 * раскладка уже готовых узлов [MdNode].
 */
@Composable
fun MarkdownText(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.onBackground,
) {
    val codeColor = color
    val linkColor = MaterialTheme.colorScheme.primary
    // Тяжёлый разбор кэшируем: пересчитывается только при смене текста или цветов темы.
    val nodes = remember(text, color, codeColor, linkColor) {
        parseMarkdown(text, color, codeColor, linkColor)
    }
    Column(modifier) {
        for (node in nodes) RenderNode(node, color)
    }
}

// ------------------------------------------------------------- рендер узлов

@Composable
private fun RenderNode(node: MdNode, color: Color) {
    // Все размеры/интерлиньяж — из типошкалы (Type.kt) вместо «сырых» sp:
    // проза → bodyLarge, цитата → bodyMedium, заголовки → headlineSmall/titleLarge/titleMedium.
    val typo = MaterialTheme.typography
    when (node) {
        is MdNode.Code -> CodeBlock(node.content, node.lang)
        is MdNode.Blank -> Spacer(Modifier.height(6.dp))
        is MdNode.Quote -> Row(Modifier.padding(vertical = 2.dp)) {
            androidx.compose.foundation.layout.Box(
                Modifier.width(3.dp).heightIn(min = 18.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(MaterialTheme.colorScheme.primary),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                node.text,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontStyle = FontStyle.Italic,
                style = typo.bodyMedium,
            )
        }
        is MdNode.ImageLine -> Column(Modifier.fillMaxWidth()) {
            for (seg in node.segments) when (seg) {
                is MdSegment.Text -> Text(seg.text, color = color, style = typo.bodyLarge)
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
                else -> typo.titleMedium
            },
            modifier = Modifier.padding(top = 8.dp, bottom = 2.dp),
        )
        is MdNode.Bullet -> Row(Modifier.padding(start = 4.dp, top = 1.dp)) {
            Text("•  ", color = color, style = typo.bodyLarge)
            Text(node.text, color = color, style = typo.bodyLarge)
        }
        is MdNode.Numbered -> Row(Modifier.padding(start = 4.dp, top = 1.dp)) {
            Text("${node.num}.  ", color = color, style = typo.bodyLarge)
            Text(node.text, color = color, style = typo.bodyLarge)
        }
        is MdNode.Para -> Text(node.text, color = color, style = typo.bodyLarge)
        is MdNode.Table -> TableBlock(node, color)
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
                lang.ifBlank { "code" }, style = MaterialTheme.typography.labelSmall,
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

// ------------------------------------------------------------- модель разбора

/** Готовый к рендеру узел markdown (разобран заранее, без композиции). */
private sealed interface MdNode {
    data class Code(val content: String, val lang: String = "") : MdNode
    object Blank : MdNode
    data class Quote(val text: AnnotatedString) : MdNode
    data class Header(val level: Int, val text: AnnotatedString) : MdNode
    data class Bullet(val text: AnnotatedString) : MdNode
    data class Numbered(val num: String, val text: AnnotatedString) : MdNode
    data class Para(val text: AnnotatedString) : MdNode
    data class ImageLine(val segments: List<MdSegment>) : MdNode
    data class Table(val header: List<AnnotatedString>, val rows: List<List<AnnotatedString>>) : MdNode
}

private sealed interface MdSegment {
    data class Text(val text: AnnotatedString) : MdSegment
    data class Img(val url: String, val caption: String) : MdSegment
}

private data class Block(val isCode: Boolean, val content: String, val lang: String = "")

// Regex-ы скомпилированы один раз (были — на каждую строку при каждой рекомпозиции).
private val IMG_RE = Regex("!\\[([^\\]]*)\\]\\(([^)]+)\\)")
private val HEADER_RE = Regex("^(#{1,4})\\s+(.*)")
private val BULLET_RE = Regex("^\\s*[-*]\\s+(.*)")
private val NUMBERED_RE = Regex("^\\s*(\\d+)\\.\\s+(.*)")
private val QUOTE_RE = Regex("^\\s*>\\s?(.*)")
private val LINK_RE = Regex("^\\[([^\\]]+)\\]\\(([^)]+)\\)")

/** Полный разбор текста в список готовых узлов — вызывается один раз в remember. */
private fun parseMarkdown(
    text: String,
    color: Color,
    codeColor: Color,
    linkColor: Color,
): List<MdNode> {
    val nodes = mutableListOf<MdNode>()
    for (block in splitByFences(text)) {
        if (block.isCode) {
            nodes += MdNode.Code(block.content, block.lang)
            continue
        }
        val lines = block.content.split("\n")
        var i = 0
        while (i < lines.size) {
            val line = lines[i]
            // GFM-таблица: строка с «|» + следующая строка-разделитель (---|:--:|).
            if (i + 1 < lines.size && line.contains("|") && isTableSeparator(lines[i + 1])) {
                val header = splitTableRow(line)
                i += 2
                val rows = mutableListOf<List<String>>()
                while (i < lines.size && lines[i].contains("|") && lines[i].isNotBlank() &&
                    !isTableSeparator(lines[i])
                ) {
                    rows += splitTableRow(lines[i]); i++
                }
                val cols = header.size
                fun pad(r: List<String>) = (0 until cols).map { r.getOrElse(it) { "" } }
                nodes += MdNode.Table(
                    header = header.map { parseInline(it, codeColor, linkColor) },
                    rows = rows.map { pad(it).map { c -> parseInline(c, codeColor, linkColor) } },
                )
            } else {
                nodes += parseLine(line, color, codeColor, linkColor); i++
            }
        }
    }
    return nodes
}

/** Строка-разделитель шапки таблицы: только |, -, :, пробелы, и есть хотя бы один «-». */
private fun isTableSeparator(line: String): Boolean {
    val t = line.trim()
    if (!t.contains('-')) return false
    return t.all { it == '|' || it == '-' || it == ':' || it == ' ' } && t.contains('|')
}

/** Разбивает строку таблицы на ячейки: убирает крайние «|» и делит по «|». */
private fun splitTableRow(line: String): List<String> =
    line.trim().trim('|').split("|").map { it.trim() }

private fun parseLine(line: String, color: Color, codeColor: Color, linkColor: Color): MdNode {
    if (line.isBlank()) return MdNode.Blank
    val header = HEADER_RE.find(line)
    val bullet = BULLET_RE.find(line)
    val numbered = NUMBERED_RE.find(line)
    val quote = QUOTE_RE.find(line)
    return when {
        quote != null -> MdNode.Quote(parseInline(quote.groupValues[1], codeColor, linkColor))
        IMG_RE.containsMatchIn(line) -> {
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
            MdNode.ImageLine(segments)
        }
        header != null -> MdNode.Header(
            header.groupValues[1].length,
            parseInline(header.groupValues[2], codeColor, linkColor),
        )
        bullet != null -> MdNode.Bullet(parseInline(bullet.groupValues[1], codeColor, linkColor))
        numbered != null -> MdNode.Numbered(
            numbered.groupValues[1],
            parseInline(numbered.groupValues[2], codeColor, linkColor),
        )
        else -> MdNode.Para(parseInline(line, codeColor, linkColor))
    }
}

/** Делит текст на блоки кода (```…```) и обычный текст. */
private fun splitByFences(text: String): List<Block> {
    val blocks = mutableListOf<Block>()
    val lines = text.split("\n")
    val buf = StringBuilder()
    var inCode = false
    var lang = ""
    fun flush(isCode: Boolean) {
        if (buf.isNotEmpty()) { blocks += Block(isCode, buf.toString(), if (isCode) lang else ""); buf.clear() }
    }
    for (line in lines) {
        if (line.trimStart().startsWith("```")) {
            flush(inCode)
            // The fence's info string ("```bash") names the language shown in the code header.
            if (!inCode) lang = line.trimStart().removePrefix("```").trim().substringBefore(' ')
            inCode = !inCode
            continue
        }
        if (buf.isNotEmpty()) buf.append('\n')
        buf.append(line)
    }
    flush(inCode)
    return blocks
}

/**
 * Инлайн-разметка → AnnotatedString: **жирный**, *курсив*, `код`, [текст](url).
 * Чистая функция (не @Composable): цвета передаются параметрами, чтобы разбор
 * можно было закэшировать вне композиции.
 */
private fun parseInline(text: String, codeColor: Color, linkColor: Color): AnnotatedString =
    buildAnnotatedString {
        var i = 0
        while (i < text.length) {
            val rest = text.substring(i)
            when {
                rest.startsWith("**") -> {
                    val end = text.indexOf("**", i + 2)
                    if (end > 0) {
                        withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(text.substring(i + 2, end)) }
                        i = end + 2
                    } else { append("**"); i += 2 }
                }
                rest.startsWith("`") -> {
                    val end = text.indexOf("`", i + 1)
                    if (end > 0) {
                        // Inline code as on the PC: mono on a faint pill, in the text colour.
                        withStyle(SpanStyle(fontFamily = com.localaiagent.app.ui.theme.AppMono, fontSize = 0.9.em, background = codeColor.copy(alpha = 0.08f))) {
                            append(text.substring(i + 1, end))
                        }
                        i = end + 1
                    } else { append("`"); i += 1 }
                }
                rest.startsWith("[") -> {
                    val m = LINK_RE.find(rest)
                    if (m != null) {
                        withLink(LinkAnnotation.Url(m.groupValues[2])) {
                            withStyle(SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline)) {
                                append(m.groupValues[1])
                            }
                        }
                        i += m.value.length
                    } else { append('['); i += 1 }
                }
                (rest.startsWith("*") || rest.startsWith("_")) && rest.length > 1 && !rest[1].isWhitespace() -> {
                    val mark = rest[0]
                    val end = text.indexOf(mark, i + 1)
                    if (end > 0) {
                        withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { append(text.substring(i + 1, end)) }
                        i = end + 1
                    } else { append(mark); i += 1 }
                }
                else -> { append(text[i]); i += 1 }
            }
        }
    }
