package com.localaiagent.core.tools

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import com.localaiagent.core.Verdict
import com.localaiagent.core.skills.SkillStore
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.File

/** The skills folder sits next to the shared memory (filesDir/skills). */
private fun ToolContext.filesRoot(): File =
    File(globalMemoryDir).parentFile ?: File(globalMemoryDir)

private fun JsonObject.str(key: String): String = this[key]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()

/** Finds skills matching a task; returns names and descriptions only (bodies load with use_skill). */
class FindSkillsTool : Tool {
    override val name = "find_skills"
    override val description =
        "Finds installed skills (tested recipes for specific kinds of tasks) by keywords. Returns names " +
            "with short descriptions; load the matching one with use_skill before starting the task."
    override val category = ToolCategory.READ
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("query") {
                put("type", "string")
                put("description", "Keywords of the task; empty lists every skill")
            }
        }
        putJsonArray("required") {}
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val found = SkillStore.search(ctx.filesRoot(), args.str("query")).take(12)
        if (found.isEmpty()) return ToolResult("No matching skills.")
        val lines = found.joinToString("\n") { "- ${it.name}: ${it.description}" }
        return ToolResult("Skills (load one with use_skill):\n$lines")
    }
}

/** Loads a skill's instructions, or one of the files bundled with it. */
class UseSkillTool : Tool {
    override val name = "use_skill"
    override val description =
        "Loads a skill's instructions by name and lists its bundled files; follow the instructions. " +
            "Pass `file` to read one bundled file (a script, reference or template)."
    override val category = ToolCategory.READ
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("name") { put("type", "string"); put("description", "Skill name from find_skills") }
            putJsonObject("file") {
                put("type", "string")
                put("description", "Optional: a bundled file path relative to the skill folder")
            }
        }
        putJsonArray("required") { add("name") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val name = args.str("name")
        if (name.isEmpty()) return ToolResult.fail("Skill name is required.")
        val skill = SkillStore.get(ctx.filesRoot(), name)
            ?: return ToolResult.fail("Skill '$name' not found. Use find_skills to see what is installed.")
        val file = args.str("file")
        if (file.isNotEmpty()) {
            return runCatching { ToolResult(SkillStore.readBundled(skill, file)) }
                .getOrElse { ToolResult.fail(it.message ?: "cannot read '$file'") }
        }
        return ToolResult("SKILL '${skill.name}' — follow these instructions:\n\n${SkillStore.render(skill)}")
    }
}

/** Saves reusable instructions as a phone skill. Asks the user: a skill shapes every future task. */
class CreateSkillTool : Tool {
    override val name = "create_skill"
    override val description =
        "Creates or replaces a skill: reusable instructions for a kind of task. Use it when the user " +
            "describes a rule or a process that will help in future tasks, or asks to remember how to do something."
    override val category = ToolCategory.EDIT
    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("name") {
                put("type", "string")
                put("description", "Latin letters, digits, '_' or '-', e.g. 'trip_planning'")
            }
            putJsonObject("description") { put("type", "string"); put("description", "One sentence: when to use it") }
            putJsonObject("content") { put("type", "string"); put("description", "The instructions in Markdown") }
        }
        putJsonArray("required") { add("name"); add("description"); add("content") }
    }

    override fun autoVerdict(args: JsonObject, ctx: ToolContext): Verdict = Verdict.ASK

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val content = args.str("content")
        if (content.isEmpty()) return ToolResult.fail("Skill content is empty.")
        return runCatching { SkillStore.create(ctx.filesRoot(), args.str("name"), args.str("description"), content) }
            .fold(
                { ToolResult("Skill '${it.name}' saved. It is available in every chat via find_skills/use_skill.") },
                { ToolResult.fail(it.message ?: "could not save the skill") },
            )
    }
}
