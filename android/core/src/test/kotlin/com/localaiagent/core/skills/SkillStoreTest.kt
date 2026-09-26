package com.localaiagent.core.skills

import com.localaiagent.core.AgentEvent
import com.localaiagent.core.ToolContext
import com.localaiagent.core.tools.CreateSkillTool
import com.localaiagent.core.tools.FindSkillsTool
import com.localaiagent.core.tools.ToolSearchTool
import com.localaiagent.core.tools.UseSkillTool
import com.localaiagent.core.Verdict
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class SkillStoreTest {
    @get:Rule val tmp = TemporaryFolder()

    private val files: File get() = tmp.root

    private fun zip(vararg entries: Pair<String, String>): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { z ->
            entries.forEach { (path, text) -> z.putNextEntry(ZipEntry(path)); z.write(text.toByteArray()); z.closeEntry() }
        }
        return out.toByteArray()
    }

    private val skillMd = """
        ---
        name: trip_planning
        description: >
          Plan a trip: budget, route
          and bookings.
        version: "1.2"
        ---
        # Trip planning
        Step one.
    """.trimIndent()

    @Test
    fun foldedMultilineDescriptionIsReadWhole() {
        val (meta, body) = SkillStore.parseFrontmatter("﻿" + skillMd.replace("\n", "\r\n"))
        assertEquals("Plan a trip: budget, route and bookings.", meta["description"])
        assertEquals("1.2", meta["version"])
        assertTrue(body.trim().startsWith("# Trip planning"))
    }

    @Test
    fun markdownImportInstallsAndIsFoundBySearch() {
        val skill = SkillStore.importMarkdown(files, skillMd, "whatever")
        assertEquals("trip_planning", skill.name)
        assertFalse(skill.fromPc)
        assertEquals("trip_planning", SkillStore.search(files, "planning a route").first().name)
        assertTrue(SkillStore.search(files, "quantum chemistry").isEmpty())
    }

    @Test
    fun importRefusesToOverwriteUnlessAsked() {
        SkillStore.importMarkdown(files, skillMd, "x")
        try {
            SkillStore.importMarkdown(files, skillMd.replace("Step one.", "Step two."), "x")
            fail("expected a clash")
        } catch (e: SkillExistsException) {
            assertEquals(listOf("trip_planning"), e.names)
        }
        SkillStore.importMarkdown(files, skillMd.replace("Step one.", "Step two."), "x", overwrite = true)
        assertTrue(SkillStore.get(files, "trip_planning")!!.body.contains("Step two."))
    }

    @Test
    fun freeFormNameFallsBackToSafeFolderName() {
        val s = SkillStore.importMarkdown(files, "---\nname: Git Flow!\ndescription: d\n---\nbody", "fallback")
        assertEquals("Git-Flow", s.dir.name)
        assertEquals("Git-Flow", s.name)
    }

    @Test
    fun zipWithSeveralSkillsInAWrapperFolder() {
        val data = zip(
            "pack/a/SKILL.md" to "---\nname: alpha\ndescription: first\n---\nA",
            "pack/b/skill.md" to "---\nname: beta\ndescription: second\n---\nB",
            "pack/b/scripts/run.py" to "print(1)",
        )
        val installed = SkillStore.importZip(files, data.inputStream(), "pack")
        assertEquals(listOf("alpha", "beta"), installed.map { it.name })
        val beta = SkillStore.get(files, "beta")!!
        assertTrue(File(beta.dir, "SKILL.md").isFile)
        assertEquals(listOf("scripts/run.py"), SkillStore.bundledFiles(beta))
        assertTrue(SkillStore.render(beta).contains("- scripts/run.py"))
        assertEquals("print(1)", SkillStore.readBundled(beta, "scripts/run.py"))
    }

    @Test
    fun zipSlipIsRejectedAndLeavesNothingBehind() {
        val data = zip("SKILL.md" to "---\nname: evil\ndescription: d\n---\nx", "../../escaped.txt" to "pwned")
        try {
            SkillStore.importZip(files, data.inputStream(), "evil")
            fail("zip-slip must be rejected")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("unsafe path"))
        }
        assertFalse(File(files.parentFile, "escaped.txt").exists())
        assertTrue(SkillStore.list(files).isEmpty())
    }

    @Test
    fun zipWithoutSkillIsNotASkill() {
        try {
            SkillStore.importZip(files, zip("readme.txt" to "hi").inputStream(), "x")
            fail("expected an error")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("no SKILL.md"))
        }
    }

    @Test
    fun bundledFileCannotEscapeTheSkillFolder() {
        val s = SkillStore.importMarkdown(files, skillMd, "x")
        File(files, "secret.txt").writeText("key")
        try {
            SkillStore.readBundled(s, "../../secret.txt")
            fail("expected an error")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("escapes"))
        }
    }

    @Test
    fun pcSyncReplacesWholeSkillAndPrunesRemovedOnes() {
        val md = "---\nname: pc_one\ndescription: from pc\n---\nv1".toByteArray()
        assertTrue(SkillStore.installFromPc(files, "pc_one", mapOf("SKILL.md" to md, "old.txt" to "x".toByteArray())))
        assertTrue(SkillStore.installFromPc(files, "pc_one", mapOf("SKILL.md" to md)))
        val s = SkillStore.get(files, "pc_one")!!
        assertTrue(s.fromPc)
        assertFalse("files removed on the PC must disappear", File(s.dir, "old.txt").exists())

        SkillStore.create(files, "mine", "my own", "phone body")
        assertEquals(listOf("pc_one"), SkillStore.prunePcSkills(files, emptyList()))
        assertNull(SkillStore.get(files, "pc_one"))
        assertNotNull("phone skills survive a sync", SkillStore.get(files, "mine"))
    }

    @Test
    fun pcSyncNeverOverwritesPhoneSkillOrWritesOutside() {
        SkillStore.create(files, "mine", "my own", "phone body")
        val md = "---\nname: mine\ndescription: pc\n---\npc body".toByteArray()
        assertFalse(SkillStore.installFromPc(files, "mine", mapOf("SKILL.md" to md)))
        assertEquals("phone body", SkillStore.get(files, "mine")!!.body)

        val ok = SkillStore.installFromPc(
            files, "other", mapOf("SKILL.md" to md, "../../../evil.txt" to "x".toByteArray()),
        )
        assertTrue(ok)
        assertFalse(File(tmp.root.parentFile, "evil.txt").exists())
    }

    @Test
    fun pcSkillDeletedOnPhoneIsNotBroughtBackBySync() {
        val md = "---\nname: pc_two\ndescription: d\n---\nbody".toByteArray()
        assertTrue(SkillStore.installFromPc(files, "pc_two", mapOf("SKILL.md" to md)))
        assertTrue(SkillStore.delete(files, "pc_two"))
        assertFalse(SkillStore.installFromPc(files, "pc_two", mapOf("SKILL.md" to md)))
        assertNull(SkillStore.get(files, "pc_two"))
        // Creating a skill with that name on the phone lifts the block for the user's own skill.
        SkillStore.create(files, "pc_two", "mine now", "b")
        assertFalse(SkillStore.get(files, "pc_two")!!.fromPc)
    }

    @Test
    fun legacyFolderWithoutMarkerCountsAsPcSkill() {
        val d = File(SkillStore.dir(files), "legacy").apply { mkdirs() }
        File(d, "SKILL.md").writeText("---\nname: legacy\ndescription: old\n---\nx")
        assertTrue(SkillStore.get(files, "legacy")!!.fromPc)
    }

    @Test
    fun deleteRemovesTheFolder() {
        SkillStore.create(files, "tmp_skill", "d", "b")
        assertTrue(SkillStore.delete(files, "tmp_skill"))
        assertFalse(SkillStore.delete(files, "tmp_skill"))
        assertTrue(SkillStore.list(files).isEmpty())
    }

    // ---------------------------------------------------------------- tools

    private inner class Ctx : ToolContext {
        override val workspaceDir = File(files, "chat").path
        override val globalMemoryDir = File(files, "memory").path
        override val scratch = mutableMapOf<String, Any?>()
        override suspend fun approve(name: String, reason: String, args: JsonObject) = true
        override suspend fun emit(event: AgentEvent) {}
    }

    @Test
    fun agentCreatesFindsAndUsesSkill() = runBlocking {
        val ctx = Ctx()
        val create = CreateSkillTool()
        val args = buildJsonObject {
            put("name", "invoice_check"); put("description", "Check invoices for errors")
            put("content", "---\nname: x\n---\n1. Sum the rows.")
        }
        assertEquals("creating a skill must ask the user", Verdict.ASK, create.autoVerdict(args, ctx))
        assertTrue(create.run(args, ctx).ok)

        val found = FindSkillsTool().run(buildJsonObject { put("query", "invoices") }, ctx)
        assertTrue(found.content.contains("invoice_check"))

        val used = UseSkillTool().run(buildJsonObject { put("name", "invoice_check") }, ctx)
        assertTrue(used.ok)
        assertTrue(used.content.contains("1. Sum the rows."))
        assertFalse("the model's own frontmatter is replaced", used.content.contains("name: x"))

        val missing = UseSkillTool().run(buildJsonObject { put("name", "nope") }, ctx)
        assertFalse(missing.ok)

        val bad = create.run(buildJsonObject { put("name", "bad name!"); put("description", "d"); put("content", "c") }, ctx)
        assertFalse(bad.ok)
    }

    @Test
    fun toolSearchShowsParameterTypesAndOptionality() {
        val sig = ToolSearchTool().signature(UseSkillTool().schema())
        assertEquals("name: string, file?: string", sig)
    }
}
