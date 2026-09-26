package com.localaiagent.core.skills

import java.io.File
import java.io.IOException
import java.io.InputStream
import java.util.zip.ZipInputStream

/**
 * One installed skill. [fromPc] — it came from the PC over the bridge, so the next sync may update or
 * remove it; skills made on the phone are never touched by a sync.
 */
data class Skill(
    val name: String,
    val description: String,
    val body: String,
    val dir: File,
    val fromPc: Boolean = false,
)

/**
 * Skills, the same format as on the PC (pc/core/skills/manager.py) and in Claude Skills: a folder
 * `skills/<name>/SKILL.md` with a frontmatter (name, description) plus optional bundled files. Only
 * the name and description are searched; the model loads the body on demand with use_skill.
 *
 * Origin is kept in a `.origin` file ("pc" or "phone"). A folder without one predates the marker; at
 * that time skills could only arrive from the PC, so it counts as a PC skill.
 */
object SkillStore {
    private val NAME_RE = Regex("^[A-Za-z0-9_-]{1,64}$")
    private val SKILL_FILES = listOf("SKILL.md", "skill.md", "Skill.md")
    private const val ORIGIN_FILE = ".origin"
    /** PC skills the user deleted on the phone; a sync does not bring them back. */
    private const val DISMISSED_FILE = ".dismissed_pc"

    /** Import limits: a skill is instructions plus a few scripts or references, not a dataset. */
    const val MAX_IMPORT_BYTES = 25L * 1024 * 1024
    const val MAX_IMPORT_FILES = 500
    private const val LISTED_FILES = 60
    private const val MAX_BUNDLED_READ = 200_000

    fun dir(filesDir: File): File = File(filesDir, "skills")

    /** A folder-safe name from whatever a downloaded skill calls itself; "" if nothing usable is left. */
    fun safeName(raw: String): String =
        raw.trim().replace(Regex("[^A-Za-z0-9_-]+"), "-").trim('-', '_').take(64)

    fun isValidName(name: String): Boolean = NAME_RE.matches(name)

    fun list(filesDir: File): List<Skill> {
        val folders = dir(filesDir).listFiles()?.filter { it.isDirectory && !it.name.startsWith(".") } ?: return emptyList()
        return folders.mapNotNull { load(it) }.sortedBy { it.name.lowercase() }
    }

    fun get(filesDir: File, name: String): Skill? {
        val n = name.trim()
        return list(filesDir).firstOrNull { it.name.equals(n, ignoreCase = true) || it.dir.name.equals(n, ignoreCase = true) }
    }

    /** Word search over names and descriptions; an empty query returns every skill. */
    fun search(filesDir: File, query: String): List<Skill> {
        val terms = query.lowercase().split(Regex("[^\\p{L}\\p{N}]+")).filter { it.length > 1 }
        val all = list(filesDir)
        if (terms.isEmpty()) return all
        return all.map { it to score(it, terms) }
            .filter { it.second > 0 }
            .sortedByDescending { it.second }
            .map { it.first }
    }

    private fun score(skill: Skill, terms: List<String>): Int {
        val name = skill.name.lowercase().replace('_', ' ').replace('-', ' ')
        val hay = skill.description.lowercase()
        // A name hit says more about the skill than a word somewhere in its description.
        return terms.sumOf { t -> (if (name.contains(t)) 3 else 0) + (if (hay.contains(t)) 1 else 0) }
    }

    /** Files bundled with a skill (scripts, references), relative to its folder. */
    fun bundledFiles(skill: Skill): List<String> {
        val skillFile = skillFile(skill.dir)
        return skill.dir.walkTopDown()
            .onEnter { it == skill.dir || !it.name.startsWith(".") }
            .filter { it.isFile && it != skillFile && !it.name.startsWith(".") }
            .map { it.relativeTo(skill.dir).invariantSeparatorsPath }
            .sorted()
            .toList()
    }

    /** What use_skill returns: the instructions, then the bundled files the model may read. */
    fun render(skill: Skill): String {
        val files = bundledFiles(skill)
        val sb = StringBuilder(skill.body.trim())
        if (files.isNotEmpty()) {
            sb.append("\n\n<skill_files>\nFiles bundled with this skill; paths in the instructions are relative to ")
            sb.append("its folder. Read one with use_skill(name, file).\n")
            files.take(LISTED_FILES).forEach { sb.append("- ").append(it).append('\n') }
            if (files.size > LISTED_FILES) sb.append("- … and ${files.size - LISTED_FILES} more\n")
            sb.append("</skill_files>")
        }
        return sb.toString()
    }

    /** Text of one bundled file, confined to the skill folder. */
    fun readBundled(skill: Skill, relPath: String): String {
        val target = File(skill.dir, relPath.trim().trimStart('/', '\\'))
        if (!isInside(skill.dir, target)) throw IllegalArgumentException("path escapes the skill folder: $relPath")
        if (!target.isFile) throw IllegalArgumentException("no file '$relPath' in skill '${skill.name}'")
        if (target.length() > MAX_BUNDLED_READ) {
            throw IllegalArgumentException("'$relPath' is too large to read (${target.length()} bytes)")
        }
        return target.readText()
    }

    // ------------------------------------------------------------------ create / import / delete

    /** Creates or replaces a phone skill. Throws IllegalArgumentException on a bad name. */
    fun create(filesDir: File, name: String, description: String, content: String): Skill {
        val n = name.trim()
        require(isValidName(n)) { "invalid skill name '$name': latin letters, digits, '_' and '-', up to 64 chars" }
        require(description.isNotBlank()) { "a skill needs a description: when should it be used" }
        var body = content.trim()
        // The model sometimes writes its own frontmatter; ours replaces it.
        if (body.startsWith("---")) body = parseFrontmatter(body).second.trim()
        val folder = File(dir(filesDir), n).apply { mkdirs() }
        val oneLine = description.trim().replace(Regex("\\s+"), " ")
        File(folder, "SKILL.md").writeText("---\nname: $n\ndescription: $oneLine\n---\n\n$body\n")
        File(folder, ORIGIN_FILE).writeText("phone")
        setDismissed(filesDir, dismissed(filesDir) - n.lowercase())
        return load(folder) ?: throw IOException("skill '$n' was written but cannot be read back")
    }

    /**
     * Installs a single SKILL.md (or any .md) whose text is [text]. [fallbackName] is used when the
     * frontmatter has no usable name (usually the file name without extension).
     */
    fun importMarkdown(filesDir: File, text: String, fallbackName: String, overwrite: Boolean = false): Skill {
        val stage = staging(filesDir)
        try {
            File(stage, "SKILL.md").writeText(text)
            return install(filesDir, listOf(stage to fallbackName), overwrite).single()
        } finally {
            stage.deleteRecursively()
        }
    }

    /**
     * Installs every skill found in a zip: the archive root itself, its child folders, or one wrapper
     * folder deeper. Rejects zip-slip paths and archives over the limits.
     */
    fun importZip(filesDir: File, input: InputStream, fallbackName: String, overwrite: Boolean = false): List<Skill> {
        val stage = staging(filesDir)
        try {
            unzip(input, stage)
            val roots = skillRoots(stage, fallbackName)
            require(roots.isNotEmpty()) { "no SKILL.md found: this is not a skill" }
            return install(filesDir, roots, overwrite)
        } finally {
            stage.deleteRecursively()
        }
    }

    /** Deletes a skill folder. A PC skill is remembered as dismissed. Returns false if there is none. */
    fun delete(filesDir: File, name: String): Boolean {
        val skill = get(filesDir, name) ?: return false
        if (skill.dir.parentFile?.canonicalPath != dir(filesDir).canonicalPath) return false
        if (skill.fromPc) setDismissed(filesDir, dismissed(filesDir) + skill.dir.name.lowercase())
        return skill.dir.deleteRecursively()
    }

    private fun dismissed(filesDir: File): Set<String> =
        File(dir(filesDir), DISMISSED_FILE).takeIf { it.isFile }?.readLines()
            ?.map { it.trim().lowercase() }?.filter { it.isNotEmpty() }?.toSet().orEmpty()

    private fun setDismissed(filesDir: File, names: Set<String>) {
        dir(filesDir).mkdirs()
        File(dir(filesDir), DISMISSED_FILE).writeText(names.sorted().joinToString("\n"))
    }

    // ------------------------------------------------------------------ sync from PC

    /**
     * Writes a skill received from the PC, replacing its previous copy whole (files removed on the PC
     * disappear here too). A phone skill with the same name wins and is left alone: returns false.
     */
    fun installFromPc(filesDir: File, name: String, files: Map<String, ByteArray>): Boolean {
        val n = safeName(name)
        if (!isValidName(n)) return false
        val target = File(dir(filesDir), n)
        if (target.isDirectory && !isFromPc(target)) return false
        if (n.lowercase() in dismissed(filesDir)) return false
        val stage = staging(filesDir)
        try {
            var total = 0L
            for ((rel, bytes) in files) {
                total += bytes.size
                if (total > MAX_IMPORT_BYTES || files.size > MAX_IMPORT_FILES) return false
                val dest = File(stage, rel)
                if (!isInside(stage, dest)) continue
                dest.parentFile?.mkdirs()
                dest.writeBytes(bytes)
            }
            if (SKILL_FILES.none { File(stage, it).isFile }) return false
            normalizeSkillFile(stage)
            File(stage, ORIGIN_FILE).writeText("pc")
            swapIn(stage, target)
            return true
        } finally {
            stage.deleteRecursively()
        }
    }

    /** Removes PC skills that are no longer on the PC. Phone skills stay. Returns the removed names. */
    fun prunePcSkills(filesDir: File, keep: Collection<String>): List<String> {
        val keepSet = keep.map { safeName(it).lowercase() }.toSet()
        return list(filesDir)
            .filter { it.fromPc && it.dir.name.lowercase() !in keepSet }
            .onEach { it.dir.deleteRecursively() }
            .map { it.name }
    }

    // ------------------------------------------------------------------ parsing

    /**
     * `key: value` frontmatter without a YAML dependency, matching what real SKILL.md files use:
     * quoted values, block scalars (`description: >` / `|`) and indented continuation lines — a
     * description folded over several lines must not be cut to its first line.
     */
    fun parseFrontmatter(raw: String): Pair<Map<String, String>, String> {
        val text = raw.removePrefix("﻿").replace("\r\n", "\n")
        val m = Regex("^---[ \\t]*\\n(.*?)\\n---[ \\t]*(\\n|$)", RegexOption.DOT_MATCHES_ALL).find(text)
            ?: return emptyMap<String, String>() to text
        val meta = linkedMapOf<String, String>()
        var key = ""
        var block = mutableListOf<String>()
        var folded = true
        fun flush() {
            if (key.isNotEmpty() && block.isNotEmpty()) {
                val joined = block.map { it.trim() }.filter { it.isNotEmpty() }.joinToString(if (folded) " " else "\n")
                meta[key] = joined.trim().trim('"', '\'')
            }
        }
        for (line in m.groupValues[1].lines()) {
            if (line.isBlank() || line.trimStart().startsWith("#")) continue
            if ((line.startsWith(" ") || line.startsWith("\t")) && key.isNotEmpty()) {
                block += line
                continue
            }
            if (':' !in line) continue
            flush()
            key = line.substringBefore(':').trim().lowercase()
            val value = line.substringAfter(':').trim()
            block = mutableListOf()
            folded = true
            when (value) {
                ">", ">-", "|", "|-" -> folded = value.startsWith(">")
                "" -> {}
                else -> block += value
            }
        }
        flush()
        return meta to text.substring(m.range.last + 1)
    }

    private fun load(folder: File): Skill? {
        val file = runCatching { skillFile(folder) }.getOrNull() ?: return null
        val (meta, body) = runCatching { parseFrontmatter(file.readText()) }.getOrNull() ?: return null
        // A free-form frontmatter name ("Git Flow!") could not be used on disk or typed by the model.
        val declared = meta["name"].orEmpty()
        val name = if (isValidName(declared)) declared else folder.name
        val description = meta["description"].orEmpty().ifBlank {
            body.lineSequence().map { it.trim() }.firstOrNull { it.isNotEmpty() && !it.startsWith("#") }?.take(200)
                ?: "Specialised instructions."
        }
        return Skill(name, description, body.trim(), folder, isFromPc(folder))
    }

    private fun isFromPc(folder: File): Boolean {
        val origin = File(folder, ORIGIN_FILE)
        return !origin.isFile || origin.readText().trim() == "pc"
    }

    private fun skillFile(folder: File): File =
        SKILL_FILES.map { File(folder, it) }.firstOrNull { it.isFile }
            ?: throw IllegalArgumentException("no SKILL.md in ${folder.name}")

    private fun normalizeSkillFile(folder: File) {
        val f = skillFile(folder)
        if (f.name != "SKILL.md") f.renameTo(File(folder, "SKILL.md"))
    }

    private fun install(filesDir: File, roots: List<Pair<File, String>>, overwrite: Boolean): List<Skill> {
        val base = dir(filesDir).apply { mkdirs() }
        val plan = roots.map { (folder, fallback) ->
            val meta = parseFrontmatter(skillFile(folder).readText()).first
            val name = safeName(meta["name"].orEmpty().ifBlank { fallback }).ifEmpty { safeName(fallback) }
            require(isValidName(name)) { "cannot make a skill name from '${meta["name"] ?: fallback}'" }
            checkSize(folder)
            folder to name
        }
        val clashes = plan.map { it.second }.filter { File(base, it).exists() }
        if (clashes.isNotEmpty() && !overwrite) throw SkillExistsException(clashes)
        return plan.map { (folder, name) ->
            normalizeSkillFile(folder)
            File(folder, ORIGIN_FILE).writeText("phone")
            val target = File(base, name)
            swapIn(folder, target)
            setDismissed(filesDir, dismissed(filesDir) - name.lowercase())
            load(target) ?: throw IOException("skill '$name' was installed but cannot be read back")
        }
    }

    /** Replaces [target] with a copy of [source] so a failed copy never leaves a half-written skill. */
    private fun swapIn(source: File, target: File) {
        val incoming = File(target.parentFile, ".${target.name}.incoming")
        incoming.deleteRecursively()
        target.parentFile?.mkdirs()
        if (!source.copyRecursively(incoming, overwrite = true)) throw IOException("could not copy skill files")
        target.deleteRecursively()
        if (!incoming.renameTo(target)) {
            incoming.copyRecursively(target, overwrite = true)
            incoming.deleteRecursively()
        }
    }

    private fun skillRoots(root: File, fallback: String): List<Pair<File, String>> {
        fun hasSkill(f: File) = SKILL_FILES.any { File(f, it).isFile }
        if (hasSkill(root)) return listOf(root to fallback)
        val children = root.listFiles()?.filter { it.isDirectory && !it.name.startsWith(".") && !it.name.startsWith("__") }
            ?.sortedBy { it.name }.orEmpty()
        val found = children.filter { hasSkill(it) }.map { it to it.name }
        if (found.isEmpty() && children.size == 1) {
            // A zip of a folder of skills.
            return children[0].listFiles()?.filter { it.isDirectory && hasSkill(it) }?.sortedBy { it.name }
                ?.map { it to it.name }.orEmpty()
        }
        return found
    }

    private fun unzip(input: InputStream, dest: File) {
        var count = 0
        var total = 0L
        ZipInputStream(input).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                if (entry.isDirectory) continue
                val target = File(dest, entry.name)
                require(isInside(dest, target)) { "unsafe path in the archive: ${entry.name}" }
                require(++count <= MAX_IMPORT_FILES) { "too many files in the archive (limit $MAX_IMPORT_FILES)" }
                target.parentFile?.mkdirs()
                target.outputStream().use { out ->
                    val buf = ByteArray(16 * 1024)
                    while (true) {
                        val n = zip.read(buf)
                        if (n < 0) break
                        total += n
                        // Checked while reading: the size in the zip header is not trustworthy.
                        require(total <= MAX_IMPORT_BYTES) { "the archive is too large for a skill (limit 25 MB)" }
                        out.write(buf, 0, n)
                    }
                }
            }
        }
    }

    private fun checkSize(folder: File) {
        val files = folder.walkTopDown().filter { it.isFile }.toList()
        require(files.size <= MAX_IMPORT_FILES) { "too many files in the skill (limit $MAX_IMPORT_FILES)" }
        require(files.sumOf { it.length() } <= MAX_IMPORT_BYTES) { "the skill is too large (limit 25 MB)" }
    }

    private fun staging(filesDir: File): File {
        val root = File(dir(filesDir), ".staging").apply { mkdirs() }
        return File.createTempFile("skill-", "", root).let { f -> f.delete(); f.mkdirs(); f }
    }

    private fun isInside(root: File, target: File): Boolean {
        val r = root.canonicalFile
        var p: File? = target.canonicalFile
        while (p != null) {
            if (p == r) return target.canonicalFile != r
            p = p.parentFile
        }
        return false
    }
}

/** Import would overwrite existing skills; [names] lists them so the UI can ask. */
class SkillExistsException(val names: List<String>) : IllegalStateException("skills already exist: ${names.joinToString(", ")}")
