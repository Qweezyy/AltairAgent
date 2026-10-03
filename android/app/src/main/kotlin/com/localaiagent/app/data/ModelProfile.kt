package com.localaiagent.app.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.add
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/**
 * Профиль модели (Задача 2): своё имя, id модели, endpoint, ключ и НАБОР
 * поддерживаемых типов ввода (capabilities). Пользователь сам задаёт, что модель
 * умеет принимать: image / video / audio / file (текст умеют все).
 */
data class ModelProfile(
    val id: String,
    val title: String,
    val model: String,
    val baseUrl: String,
    val apiKey: String,
    val caps: Set<String> = emptySet(),
    /** Размер контекстного окна модели (токенов) — для индикатора и самоконтроля ИИ. */
    val contextWindow: Int = 128_000,
) {
    fun accepts(cap: String) = cap in caps
}

/** Все известные типы вложений (кроме текста). */
val ALL_CAPS = listOf("image", "video", "audio", "file")

val CAP_LABEL = mapOf(
    "image" to com.localaiagent.app.R.string.attach_photo,
    "video" to com.localaiagent.app.R.string.attach_video,
    "audio" to com.localaiagent.app.R.string.attach_audio,
    "file" to com.localaiagent.app.R.string.attach_files,
)

private val JSON = Json { ignoreUnknownKeys = true }

fun modelsToJson(models: List<ModelProfile>): String = buildJsonArray {
    for (m in models) addJsonObject {
        put("id", m.id); put("title", m.title); put("model", m.model)
        put("baseUrl", m.baseUrl); put("apiKey", m.apiKey)
        put("contextWindow", m.contextWindow)
        putJsonArray("caps") { m.caps.forEach { add(it) } }
    }
}.toString()

fun modelsFromJson(raw: String?): List<ModelProfile> {
    if (raw.isNullOrBlank()) return emptyList()
    val arr = runCatching { JSON.parseToJsonElement(raw) as? JsonArray }.getOrNull() ?: return emptyList()
    return arr.mapNotNull { el ->
        val o = runCatching { el.jsonObject }.getOrNull() ?: return@mapNotNull null
        fun s(k: String) = o[k]?.jsonPrimitive?.contentOrNull.orEmpty()
        val caps = o["caps"]?.jsonArray?.mapNotNull { it.jsonPrimitive.contentOrNull }?.toSet() ?: emptySet()
        val id = s("id").ifBlank { return@mapNotNull null }
        val ctxWin = s("contextWindow").toIntOrNull() ?: 128_000
        ModelProfile(id, s("title"), s("model"), s("baseUrl"), s("apiKey"), caps, ctxWin)
    }
}

/**
 * A context window typed by the user: "128K", "1M", "200 000" or "200000". Null when it is not a
 * size; too small or absurd values are refused too.
 */
fun parseContextWindow(input: String): Int? {
    val t = input.trim().replace(" ", "").replace("_", "").replace(",", ".").uppercase()
    if (t.isEmpty()) return null
    val mult = when (t.last()) { 'K' -> 1_000.0; 'M' -> 1_000_000.0; else -> 1.0 }
    val num = (if (mult == 1.0) t else t.dropLast(1)).toDoubleOrNull() ?: return null
    val v = (num * mult).toLong()
    return if (v in 1_000..100_000_000) v.toInt() else null
}

/** A context window as the user reads it: 128K, 1M, 1.5M. */
fun formatContextWindow(tokens: Int): String = when {
    tokens >= 1_000_000 && tokens % 1_000_000 == 0 -> "${tokens / 1_000_000}M"
    tokens >= 1_000_000 -> "%.1fM".format(java.util.Locale.ROOT, tokens / 1_000_000.0)
    tokens >= 1_000 -> "${tokens / 1_000}K"
    else -> tokens.toString()
}
