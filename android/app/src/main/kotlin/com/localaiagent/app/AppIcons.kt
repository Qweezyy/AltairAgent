package com.localaiagent.app

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager

/**
 * Выбор иконки приложения (космос-варианты Altair) через activity-alias: активна ровно одна
 * точка входа в лаунчере. ВСЕ иконки — алиасы (включая «deep» → IconDeep); сама MainActivity
 * лаунчер-компонентом НЕ является и никогда не отключается, иначе смена иконки гасит запущенную
 * активити и приложение вылетает. Переключение — через PackageManager (DONT_KILL_APP).
 */
object AppIcons {
    private const val NS = "com.localaiagent.app."

    /** id → простое имя компонента-алиаса. Порядок = порядок показа в настройках. */
    val COMPONENTS: Map<String, String> = linkedMapOf(
        "deep" to "IconDeep",
        "blue" to "IconBlue",
        "aurora" to "IconAurora",
        "violet" to "IconViolet",
        "ember" to "IconEmber",
        "minimal" to "IconMinimal",
        "milky" to "IconMilky",
        "rose" to "IconRose",
    )

    val IDS: List<String> = COMPONENTS.keys.toList()

    /**
     * Отложенный выбор иконки. Переключать активный лаунчер-алиас, ПОКА приложение на переднем
     * плане, нельзя: Android завершает задачу, чей базовый компонент отключается, и приложение
     * «вылетает». Поэтому выбор запоминаем и применяем в `onStop` (когда ушли в фон).
     */
    @Volatile
    var pending: String? = null

    /** Применить отложенный выбор (вызывать из Activity.onStop). Ничего не делает, если выбора нет. */
    fun applyPending(context: Context) {
        val id = pending ?: return
        pending = null
        apply(context, id)
    }

    /** Включить выбранную иконку, выключить остальные (без убийства процесса). */
    fun apply(context: Context, id: String) {
        val pm = context.packageManager
        val pkg = context.packageName
        val chosen = COMPONENTS[id] ?: return
        COMPONENTS.values.forEach { comp ->
            val enable = comp == chosen
            val want = if (enable) PackageManager.COMPONENT_ENABLED_STATE_ENABLED
            else PackageManager.COMPONENT_ENABLED_STATE_DISABLED
            val cn = ComponentName(pkg, NS + comp)
            try {
                if (pm.getComponentEnabledSetting(cn) != want) {
                    pm.setComponentEnabledSetting(cn, want, PackageManager.DONT_KILL_APP)
                }
            } catch (e: Exception) {
                // компонент мог отсутствовать на старой установке — не роняем приложение
            }
        }
    }
}
