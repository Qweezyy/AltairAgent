package com.localaiagent.app.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.localaiagent.app.bridge.PcBridgeConfig
import com.localaiagent.app.ui.theme.ThemePrefs
import com.localaiagent.core.LlmConfig
import kotlinx.coroutines.flow.first

// Settings and keys (BYOK). Credentials (model API keys, bridge token) are stored encrypted with
// KeyVault (Android Keystore); see migrateToEncrypted() for data written by older builds.
private val Context.dataStore: DataStore<Preferences> by preferencesDataStore("settings")

class SettingsStore(private val context: Context) {
    private object Keys {
        val baseUrl = stringPreferencesKey("base_url")
        val model = stringPreferencesKey("model")
        val apiKey = stringPreferencesKey("api_key")
        val pcUrl = stringPreferencesKey("pc_url")
        val pcToken = stringPreferencesKey("pc_token")
        val pcWorkspace = stringPreferencesKey("pc_workspace")
        val themeMode = stringPreferencesKey("theme_mode")
        val accent = longPreferencesKey("accent_color")
        val models = stringPreferencesKey("models_json")
        val activeModel = stringPreferencesKey("active_model")
        val nickname = stringPreferencesKey("user_nickname")
        val reactionsOnUser = booleanPreferencesKey("reactions_on_user")
        val haptics = booleanPreferencesKey("haptics_enabled")
        val uiScale = androidx.datastore.preferences.core.floatPreferencesKey("ui_scale")
        val appIcon = stringPreferencesKey("app_icon")
    }

    /** Re-encrypts credentials that older builds stored in plaintext. Idempotent; call once at start. */
    suspend fun migrateToEncrypted() {
        context.dataStore.edit { prefs ->
            for (key in listOf(Keys.models, Keys.apiKey, Keys.pcToken)) {
                val v = prefs[key] ?: continue
                if (v.isNotEmpty() && !com.localaiagent.app.security.KeyVault.isEncrypted(v)) prefs[key] = com.localaiagent.app.security.KeyVault.encrypt(v)
            }
        }
    }

    /** Выбранная иконка приложения (id варианта, см. [com.localaiagent.app.AppIcons]). */
    suspend fun loadAppIcon(): String =
        context.dataStore.data.first()[Keys.appIcon] ?: "deep"

    suspend fun saveAppIcon(id: String) {
        context.dataStore.edit { it[Keys.appIcon] = id }
    }

    suspend fun loadReactionsOnUser(): Boolean =
        context.dataStore.data.first()[Keys.reactionsOnUser] ?: false

    suspend fun saveReactionsOnUser(enabled: Boolean) {
        context.dataStore.edit { it[Keys.reactionsOnUser] = enabled }
    }

    /** Text size multiplier chosen in Settings → Appearance (1.0 = default). */
    suspend fun loadUiScale(): Float =
        (context.dataStore.data.first()[Keys.uiScale] ?: 1f).coerceIn(UI_SCALE_MIN, UI_SCALE_MAX)

    suspend fun saveUiScale(scale: Float) {
        context.dataStore.edit { it[Keys.uiScale] = scale.coerceIn(UI_SCALE_MIN, UI_SCALE_MAX) }
    }

    suspend fun loadHaptics(): Boolean =
        context.dataStore.data.first()[Keys.haptics] ?: true

    suspend fun saveHaptics(enabled: Boolean) {
        context.dataStore.edit { it[Keys.haptics] = enabled }
    }

    // ---- профили моделей (Задача 2) ----

    /** Список профилей + id активного. Если пусто — сидируем из старого single-конфига. */
    suspend fun loadModels(): Pair<List<ModelProfile>, String> {
        val p = context.dataStore.data.first()
        var list = modelsFromJson(p[Keys.models]?.let(com.localaiagent.app.security.KeyVault::decrypt))
        var active = p[Keys.activeModel] ?: ""
        if (list.isEmpty()) {
            // Миграция: один профиль из старых полей (или дефолт gateyourway/0x-alpha).
            val seed = ModelProfile(
                id = "default",
                title = (p[Keys.model] ?: "0x-alpha"),
                model = p[Keys.model] ?: "0x-alpha",
                baseUrl = p[Keys.baseUrl] ?: "https://api.gateyourway.com/v1",
                apiKey = p[Keys.apiKey]?.let(com.localaiagent.app.security.KeyVault::decrypt) ?: "",
                caps = setOf("image"),
            )
            list = listOf(seed)
            active = seed.id
        }
        if (list.none { it.id == active }) active = list.first().id
        return list to active
    }

    suspend fun saveModels(models: List<ModelProfile>, activeId: String) {
        context.dataStore.edit {
            it[Keys.models] = com.localaiagent.app.security.KeyVault.encrypt(modelsToJson(models))
            it[Keys.activeModel] = activeId
        }
    }

    /** Конфиг активной модели для LLM-клиента. */
    suspend fun activeConfig(): LlmConfig {
        val (list, active) = loadModels()
        val m = list.firstOrNull { it.id == active } ?: list.first()
        return LlmConfig(baseUrl = m.baseUrl, model = m.model, apiKey = m.apiKey)
    }

    suspend fun activeProfile(): ModelProfile {
        val (list, active) = loadModels()
        return list.firstOrNull { it.id == active } ?: list.first()
    }

    /** Тема и акцентный цвет. */
    suspend fun loadTheme(): ThemePrefs {
        val p = context.dataStore.data.first()
        return ThemePrefs(
            mode = p[Keys.themeMode] ?: "black",
            accent = p[Keys.accent] ?: com.localaiagent.app.ui.theme.Brand.accent,
        )
    }

    suspend fun saveTheme(prefs: ThemePrefs) {
        context.dataStore.edit {
            it[Keys.themeMode] = prefs.mode
            it[Keys.accent] = prefs.accent
        }
    }

    /** Отображаемый профиль, локальный до появления аккаунтов и синхронизации. */
    suspend fun loadUserProfile(): UserProfile {
        val p = context.dataStore.data.first()
        return UserProfile.fromNickname(p[Keys.nickname].orEmpty())
    }

    suspend fun saveUserProfile(profile: UserProfile) {
        val normalized = UserProfile.fromNickname(profile.nickname)
        context.dataStore.edit { it[Keys.nickname] = normalized.nickname }
    }

    suspend fun load(): LlmConfig {
        val p = context.dataStore.data.first()
        return LlmConfig(
            baseUrl = p[Keys.baseUrl] ?: "https://api.gateyourway.com/v1",
            model = p[Keys.model] ?: "0x-alpha",
            apiKey = p[Keys.apiKey]?.let(com.localaiagent.app.security.KeyVault::decrypt) ?: "",
        )
    }

    suspend fun save(config: LlmConfig) {
        context.dataStore.edit {
            it[Keys.baseUrl] = config.baseUrl
            it[Keys.model] = config.model
            it[Keys.apiKey] = com.localaiagent.app.security.KeyVault.encrypt(config.apiKey)
        }
    }

    /** Настройки моста к ПК (Фаза 4). */
    suspend fun loadBridge(): PcBridgeConfig {
        val p = context.dataStore.data.first()
        return PcBridgeConfig(
            url = p[Keys.pcUrl] ?: "",
            token = p[Keys.pcToken]?.let(com.localaiagent.app.security.KeyVault::decrypt) ?: "",
            workspace = p[Keys.pcWorkspace] ?: "",
        )
    }

    suspend fun saveBridge(url: String, token: String, workspace: String) {
        context.dataStore.edit {
            it[Keys.pcUrl] = url
            it[Keys.pcToken] = com.localaiagent.app.security.KeyVault.encrypt(token)
            it[Keys.pcWorkspace] = workspace
        }
    }
}


const val UI_SCALE_MIN = 0.7f
const val UI_SCALE_MAX = 2.0f
