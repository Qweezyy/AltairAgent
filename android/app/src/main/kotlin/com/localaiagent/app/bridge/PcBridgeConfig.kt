package com.localaiagent.app.bridge

/**
 * Конфигурация моста к ПК-агенту. Пустой url — мост выключен.
 * workspace — папка/проект на ПК, в которой работать (пусто — папка по умолчанию).
 *
 * Тип-данные живут в общем (main) наборе исходников: им пользуются настройки и
 * ViewModel в обеих сборках. Сетевой клиент и инструменты моста — только во flavor
 * `full` (в `lite`-сборке моста нет вовсе).
 */
data class PcBridgeConfig(
    val url: String = "",
    val token: String = "",
    val workspace: String = "",
) {
    val enabled: Boolean get() = url.isNotBlank()
}
