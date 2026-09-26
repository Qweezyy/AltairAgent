package com.localaiagent.app.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.File

/**
 * Хранилище секретов (API-ключи и т.п.). Значение видит только пользователь и
 * инструменты при подстановке — модель работает с секретом по ИМЕНИ (плейсхолдер
 * {{secret:ИМЯ}}), но само значение не получает.
 *
 * availability: "always" — may be used without asking; "ask" — с разрешения.
 * Файл в приватной папке приложения (не виден другим приложениям).
 */
data class Secret(
    val name: String,
    val value: String,
    val availability: String = "always", // always | ask
    val purpose: String = "",
)

class SecretStore(private val filesDir: File) {
    private val file = File(filesDir, "secrets.json")
    private val json = Json { ignoreUnknownKeys = true }

    fun load(): MutableList<Secret> {
        if (!file.isFile) return mutableListOf()
        return runCatching {
            (json.parseToJsonElement(com.localaiagent.app.security.KeyVault.decrypt(file.readText())) as JsonArray).mapNotNull { el ->
                val o = el.jsonObject
                val name = o["name"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
                Secret(
                    name,
                    o["value"]?.jsonPrimitive?.contentOrNull ?: "",
                    o["availability"]?.jsonPrimitive?.contentOrNull ?: "always",
                    o["purpose"]?.jsonPrimitive?.contentOrNull ?: "",
                )
            }.toMutableList()
        }.getOrElse { mutableListOf() }
    }

    /** Re-encrypts a file written in plaintext by an older build. Idempotent; call once at start. */
    fun migrateToEncrypted() {
        if (!file.isFile) return
        val raw = runCatching { file.readText() }.getOrNull() ?: return
        if (raw.isNotBlank() && !com.localaiagent.app.security.KeyVault.isEncrypted(raw)) save(load())
    }

    private fun save(list: List<Secret>) {
        runCatching {
            filesDir.mkdirs()
            val arr = buildJsonArray {
                list.forEach { s ->
                    addJsonObject {
                        put("name", s.name); put("value", s.value)
                        put("availability", s.availability); put("purpose", s.purpose)
                    }
                }
            }
            file.writeText(com.localaiagent.app.security.KeyVault.encrypt(arr.toString()))
        }
    }

    fun put(secret: Secret) {
        val list = load()
        val i = list.indexOfFirst { it.name == secret.name }
        if (i >= 0) list[i] = secret else list += secret
        save(list)
    }

    fun setAvailability(name: String, availability: String) {
        val list = load()
        val i = list.indexOfFirst { it.name == name }
        if (i >= 0) { list[i] = list[i].copy(availability = availability); save(list) }
    }

    fun remove(name: String) = save(load().filterNot { it.name == name })

    fun value(name: String): String? = load().firstOrNull { it.name == name }?.value

    fun get(name: String): Secret? = load().firstOrNull { it.name == name }

    /** Метаданные для модели (без значений). */
    fun info(): List<String> = load().map {
        "${it.name} (${it.availability})" + if (it.purpose.isNotBlank()) ": ${it.purpose}" else ""
    }
}
