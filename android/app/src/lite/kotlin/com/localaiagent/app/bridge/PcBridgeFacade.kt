package com.localaiagent.app.bridge

import com.localaiagent.core.Tool

/**
 * Заглушка моста к ПК для flavor `lite` — «чистое» Android-приложение без моста и без
 * каких-либо его упоминаний. Ни сетевого клиента, ни pc_*-инструментов в этой сборке
 * нет: тот же общий код (ChatViewModel/ChatScreen) собирается против этих no-op.
 *
 * Полностью повторяет публичную поверхность `full`-версии [PcBridgeFacade], чтобы обе
 * сборки развивались из одного кода и обновлялись вместе.
 */
object PcBridgeFacade {

    /** В lite-сборке мост не поддерживается — UI и подсказки про ПК скрыты. */
    const val SUPPORTED: Boolean = false

    fun tools(config: PcBridgeConfig): List<Tool> = emptyList()

    suspend fun testConnection(config: PcBridgeConfig): String? = "мост не поддерживается в этой сборке"

    fun systemPrompt(bridgeEnabled: Boolean): String = ""

    fun systemInfoLabel(bridgeConfigured: Boolean): String = ""

    // Синхронизация с ПК в lite не поддерживается (нет моста) — заглушки.
    suspend fun fetchMcpServers(config: PcBridgeConfig): List<PcMcpServer>? = null

    suspend fun fetchSkills(config: PcBridgeConfig): List<PcSkillMeta>? = null

    suspend fun fetchSkillFiles(config: PcBridgeConfig, name: String): List<PcSkillFile>? = null
}
