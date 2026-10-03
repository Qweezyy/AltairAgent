package com.localaiagent.app.ui

import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The answer markup forms models really send: rules, every heading level, nested lists. */
class MarkdownParseTest {

    private fun parse(text: String) = parseMarkdown(text, Color.White, Color.White, Color.Blue)

    private fun MdNode.label(): String = when (this) {
        is MdNode.Header -> "H$level:${text.text}"
        is MdNode.Para -> "P:${text.text}"
        is MdNode.Bullet -> "B$depth:${text.text}"
        is MdNode.Numbered -> "N$num:${text.text}"
        MdNode.Rule -> "HR"
        MdNode.Blank -> "_"
        else -> javaClass.simpleName
    }

    private fun labels(text: String) = parse(text).map { it.label() }

    @Test
    fun dashRulesOfAnyLengthSeparateBlocks() {
        assertEquals(listOf("P:One", "_", "HR", "_", "P:Two"), labels("One\n\n---\n\nTwo"))
        assertEquals(listOf("P:One", "HR", "P:Two"), labels("One\n------------\nTwo"))
        assertEquals(listOf("HR", "HR", "HR", "HR"), labels("***\n___\n- - -\n * * *"))
    }

    @Test
    fun twoDashesOrTextWithDashesAreNotRules() {
        assertEquals(listOf("P:--"), labels("--"))
        assertEquals(listOf("P:--- not a rule"), labels("--- not a rule"))
        assertEquals(listOf("B0:item"), labels("- item"))
    }

    @Test
    fun allSixHeadingLevelsAndClosingHashes() {
        assertEquals(
            listOf("H1:a", "H2:b", "H3:c", "H4:d", "H5:e", "H6:f"),
            labels("# a\n## b\n### c\n#### d\n##### e\n###### f"),
        )
        assertEquals(listOf("H3:Title"), labels("### Title ###"))
        assertEquals(listOf("H2:C#"), labels("## C#"))
        assertEquals(listOf("H3:Indented"), labels("   ### Indented"))
    }

    @Test
    fun bareHashesNeverShowAsText() {
        val nodes = labels("Text\n###\nMore")
        assertEquals(listOf("P:Text", "_", "P:More"), nodes)
        assertTrue(labels("#").none { it.contains("#") })
    }

    @Test
    fun hashesWithoutSpaceOrTooManyStayText() {
        assertEquals(listOf("P:#hashtag"), labels("#hashtag"))
        assertEquals(listOf("P:####### seven"), labels("####### seven"))
    }

    @Test
    fun equalsUnderlineMakesAHeading() {
        assertEquals(listOf("H1:Title", "P:body"), labels("Title\n=====\nbody"))
    }

    private fun inline(text: String) = (parse(text).single() as MdNode.Para).text

    @Test
    fun displayFormulasInEveryForm() {
        assertEquals(listOf(MdNode.Math("E = mc^2")), parse("$\$E = mc^2$$").filterIsInstance<MdNode.Math>())
        assertEquals("\\int_0^1 x\\,dx", (parse("\\[\n\\int_0^1 x\\,dx\n\\]").single() as MdNode.Math).tex.trim())
        assertEquals("a^2+b^2", (parse("```math\na^2+b^2\n```").single() as MdNode.Math).tex)
        val multi = parse("Before\n$$\n\\sum_{i=1}^n i\n$$\nAfter")
        assertEquals(listOf("P:Before", "Math", "P:After"), multi.map { if (it is MdNode.Math) "Math" else it.label() })
    }

    @Test
    fun anUnclosedFormulaStaysTextWhileStreaming() {
        assertTrue(parse("$$\n\\frac{a}{b").none { it is MdNode.Math })
    }

    @Test
    fun inlineFormulasGoToKatexButMoneyDoesNot() {
        val line = parse("The area is \$\\pi r^2\$ for **any** circle").single()
        assertTrue(line is MdNode.MathText)
        val html = (line as MdNode.MathText).html
        assertTrue(html, html.contains("data-t=\"\\pi r^2\""))
        assertTrue(html, html.contains("<b>any</b>"))
        assertTrue(parse("It costs \$5 and \$10 today").single() is MdNode.Para)
        assertTrue(parse("Euler: \\(e^{i\\pi}+1=0\\)").single() is MdNode.MathText)
        // Formula text with quotes or tags cannot break out of the attribute.
        assertTrue((parse("\$a\"<b>\$").single() as MdNode.MathText).html.contains("a&quot;&lt;b&gt;"))
    }

    @Test
    fun mermaidAndOtherCodeFences() {
        assertTrue(parse("```mermaid\ngraph TD; A-->B\n```").single() is MdNode.Mermaid)
        assertEquals("py", (parse("~~~py\nprint(1)\n~~~").single() as MdNode.Code).lang)
    }

    @Test
    fun rareInlineMarkup() {
        assertEquals("gone", inline("~~gone~~").text)
        assertEquals("note", inline("==note==").text)
        assertEquals("x2", inline("x^2^").text)
        assertEquals("H2O", inline("H~2~O").text)
        assertEquals("bold italic", inline("***bold italic***").text)
        assertEquals("a*b", inline("a\\*b").text)
        assertEquals("snake_case_name", inline("snake_case_name").text)
        assertEquals("Ctrl+C", inline("<kbd>Ctrl+C</kbd>").text)
        assertEquals("E=mc2", inline("E=mc<sup>2</sup>").text)
        assertEquals("a\nb", inline("a<br>b").text)
        assertEquals("see 1", inline("see [^1]").text)
        assertEquals("go https://example.com/a?b=1 now", inline("go https://example.com/a?b=1 now").text)
        assertEquals("https://x.org", inline("<https://x.org>").text)
        // Links are real links.
        assertEquals(1, inline("[site](https://x.org)").getLinkAnnotations(0, 4).size)
        assertEquals(1, inline("see https://x.org.").getLinkAnnotations(4, 10).size)
    }

    @Test
    fun tasksQuotesAlertsFootnotesDetails() {
        val tasks = parse("- [ ] todo\n- [x] done")
        assertEquals(listOf(false, true), tasks.map { (it as MdNode.Task).done })
        assertEquals(2, (parse(">> deeper").single() as MdNode.Quote).level)
        val alert = parse("> [!WARNING]\n> Mind the gap").single() as MdNode.Alert
        assertEquals("WARNING", alert.kind)
        assertEquals("Mind the gap", alert.text.text)
        assertEquals("1", (parse("[^1]: The source.").single() as MdNode.Footnote).label)
        val details = parse("<details>\n<summary>More</summary>\nHidden **text**\n</details>").single() as MdNode.Details
        assertEquals("More", details.summary)
        assertEquals("Hidden **text**", details.body)
        assertEquals("N3:third", labels("3) third").single())
    }

    @Test
    fun nestedAndPlusBullets() {
        assertEquals(listOf("B0:a", "B1:b", "B0:c"), labels("- a\n  - b\n+ c"))
    }
}
