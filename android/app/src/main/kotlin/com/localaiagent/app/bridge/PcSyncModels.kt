package com.localaiagent.app.bridge

/**
 * DTO синхронизации плагинов/навыков с ПК по мосту (server/ws.py). Общие для обеих
 * сборок: full наполняет их из ответов моста, lite их не использует (заглушки в фасаде).
 *
 * Контракт сообщений моста (телефон → ПК → телефон):
 *  • mcp_list      → {"type":"mcp","servers":[{name,url,transport,headers}]}
 *  • skills_list   → {"type":"skills","items":[{name,description,version}]}
 *  • skill_get(name) → {"type":"skill","name",files:[{path,b64}]}
 */
data class PcMcpServer(
    val name: String,
    val url: String,
    val transport: String,
    val headers: Map<String, String>,
)

data class PcSkillMeta(val name: String, val description: String, val version: String)

data class PcSkillFile(val path: String, val b64: String)
