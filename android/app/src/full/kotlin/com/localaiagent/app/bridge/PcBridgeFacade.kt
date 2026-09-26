package com.localaiagent.app.bridge

import com.localaiagent.core.Tool
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/**
 * Единая точка входа моста к ПК для общего (main) кода. Реализация flavor `full`:
 * мост включён, инструменты и сетевой клиент доступны.
 *
 * Общий код (ChatViewModel, ChatScreen) обращается только сюда и никогда не трогает
 * `PcBridgeClient`/`PcAgentTool`/`pcFileTools` напрямую — поэтому в flavor `lite`,
 * где этих классов нет, тот же код собирается против [PcBridgeFacade]-заглушки.
 */
object PcBridgeFacade {

    /** Поддерживает ли сборка мост к ПК (флаг для показа/скрытия UI и подсказок). */
    const val SUPPORTED: Boolean = true

    /** Инструменты моста для агента, если мост настроен (иначе пусто). */
    fun tools(config: PcBridgeConfig): List<Tool> =
        if (config.enabled) listOf(PcAgentTool(config)) + pcFileTools(config) else emptyList()

    /** Проверка связи: null — связь есть, строка — текст ошибки. */
    suspend fun testConnection(config: PcBridgeConfig): String? =
        PcBridgeClient(config).testConnection()

    /**
     * Кусок системного промпта про мост (перечисляет pc_*-инструменты). Пусто, если
     * мост не настроен.
     */
    fun systemPrompt(bridgeEnabled: Boolean): String =
        if (bridgeEnabled) {
            "<pc_bridge>\n" +
                "The phone and the PC work as two agents: you coordinate, the PC executes. Delegate whole " +
                "computer tasks (programming, shell, tests, git, heavy tools) to `pc_agent`; when unsure what " +
                "the PC can do, check `pc_capabilities` first. For PC files, look before you fetch: " +
                "`pc_list_files` and `pc_stat_file`; `pc_read_file` returns text, `pc_fetch_file` downloads a " +
                "file or image into the chat, `pc_send_file` sends a chat file to the PC inbox. Don't pull large " +
                "files without need (25 MB limit). `pc_sync_memory` syncs shared memory with the PC.\n" +
                "</pc_bridge>"
        } else {
            ""
        }

    /** Хвост строки системных данных про мост (с ведущей запятой) или пусто. */
    fun systemInfoLabel(bridgeConfigured: Boolean): String =
        if (bridgeConfigured) ", PC bridge configured" else ", PC bridge not configured"

    // ---- синхронизация плагинов/навыков с ПК (Задача 6) ----

    /** Список MCP-серверов с ПК (mcp_list → mcp). null — нет связи/мост не настроен. */
    suspend fun fetchMcpServers(config: PcBridgeConfig): List<PcMcpServer>? {
        if (!config.enabled) return null
        val reply = PcBridgeClient(config)
            .oneShot(buildJsonObject { put("type", "mcp_list") }, setOf("mcp")) ?: return null
        return reply["servers"]?.jsonArray?.mapNotNull { el ->
            val o = el.jsonObject
            val name = o["name"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            val url = o["url"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            val headers = o["headers"]?.jsonObject?.mapNotNull { (k, v) ->
                v.jsonPrimitive.contentOrNull?.let { k to it }
            }?.toMap() ?: emptyMap()
            PcMcpServer(name, url, o["transport"]?.jsonPrimitive?.contentOrNull ?: "http", headers)
        } ?: emptyList()
    }

    /** Список навыков с ПК (skills_list → skills). */
    suspend fun fetchSkills(config: PcBridgeConfig): List<PcSkillMeta>? {
        if (!config.enabled) return null
        val reply = PcBridgeClient(config)
            .oneShot(buildJsonObject { put("type", "skills_list") }, setOf("skills")) ?: return null
        return reply["items"]?.jsonArray?.mapNotNull { el ->
            val o = el.jsonObject
            val name = o["name"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            PcSkillMeta(
                name,
                o["description"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                o["version"]?.jsonPrimitive?.contentOrNull.orEmpty(),
            )
        } ?: emptyList()
    }

    /** Файлы одного навыка с ПК (skill_get → skill): [{path, b64}]. */
    suspend fun fetchSkillFiles(config: PcBridgeConfig, name: String): List<PcSkillFile>? {
        if (!config.enabled) return null
        val reply = PcBridgeClient(config).oneShot(
            buildJsonObject { put("type", "skill_get"); put("name", name) }, setOf("skill"),
        ) ?: return null
        return reply["files"]?.jsonArray?.mapNotNull { el ->
            val o = el.jsonObject
            val path = o["path"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            val b64 = o["b64"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            PcSkillFile(path, b64)
        } ?: emptyList()
    }
}
