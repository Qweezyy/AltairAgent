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

    @Test
    fun nestedAndPlusBullets() {
        assertEquals(listOf("B0:a", "B1:b", "B0:c"), labels("- a\n  - b\n+ c"))
    }
}
