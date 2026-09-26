package com.localaiagent.app

import android.content.Context
import android.content.res.Configuration
import java.util.Locale

/**
 * In-app UI language: "system" (device locale), "en" or "ru". Stored in plain SharedPreferences so it
 * can be read synchronously in [android.app.Activity.attachBaseContext] (DataStore is async and too late
 * there). Applying a language recreates the Activity so every `stringResource` re-resolves.
 */
object LocaleManager {
    private const val PREFS = "altair_locale"
    private const val KEY = "language"
    const val SYSTEM = "system"

    fun get(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, SYSTEM) ?: SYSTEM

    fun set(context: Context, lang: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, lang).apply()
    }

    /** Wrap a base context with the chosen locale (no-op for "system"). */
    fun wrap(base: Context): Context {
        val lang = get(base)
        if (lang == SYSTEM) return base
        val locale = Locale(lang)
        Locale.setDefault(locale)
        val cfg = Configuration(base.resources.configuration)
        cfg.setLocale(locale)
        return base.createConfigurationContext(cfg)
    }
}
