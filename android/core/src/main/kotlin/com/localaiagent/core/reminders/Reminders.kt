package com.localaiagent.core.reminders

import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Универсальное напоминание/условие. Агент сам формулирует условие из доступных сигналов.
 *
 * - kind="time": сработает в момент [fireAt] (epoch ms).
 * - kind="condition": сработает, когда сигнал [signal] удовлетворит [op] [value]
 *   (edge-triggered — один раз при первом выполнении условия).
 *
 * Доступные сигналы (condition): battery (0..100), charging (true/false),
 * pc_online (true/false), network (online/offline/wifi/cellular).
 */
@Serializable
data class Reminder(
    val id: String,
    val kind: String,               // "time" | "condition"
    val note: String,
    val chatId: String = "",
    val createdAt: Long = 0L,
    // time-based
    val fireAt: Long = 0L,
    // condition-based
    val signal: String = "",        // battery | charging | pc_online | network
    val op: String = "",            // ">=" | "<=" | "==" | "!="
    val value: String = "",
    // lifecycle
    val fired: Boolean = false,
    val firedAt: Long = 0L,
    val seenByModel: Boolean = false,   // модель уже получила это событие в контексте
) {
    val active: Boolean get() = !fired

    fun describe(): String = when (kind) {
        "time" -> "⏰ $note"
        else -> "🔔 когда $signal $op $value → $note"
    }
}

/** Файловое хранилище напоминаний (одно на все чаты). */
object ReminderStore {
    private val JSON = Json { ignoreUnknownKeys = true; prettyPrint = false }
    private val SER = ListSerializer(Reminder.serializer())

    fun file(dir: File): File = File(dir, "reminders.json")

    fun load(dir: File): MutableList<Reminder> {
        val f = file(dir)
        if (!f.isFile) return mutableListOf()
        return runCatching {
            JSON.decodeFromString(SER, f.readText()).toMutableList()
        }.getOrElse { mutableListOf() }
    }

    @Synchronized
    fun save(dir: File, list: List<Reminder>) {
        runCatching {
            dir.mkdirs()
            file(dir).writeText(JSON.encodeToString(SER, list))
        }
    }

    @Synchronized
    fun add(dir: File, r: Reminder): Reminder {
        val list = load(dir); list += r; save(dir, list); return r
    }

    @Synchronized
    fun update(dir: File, r: Reminder) {
        val list = load(dir)
        val i = list.indexOfFirst { it.id == r.id }
        if (i >= 0) list[i] = r else list += r
        save(dir, list)
    }

    @Synchronized
    fun remove(dir: File, id: String): Boolean {
        val list = load(dir)
        val removed = list.removeAll { it.id == id }
        if (removed) save(dir, list)
        return removed
    }
}
