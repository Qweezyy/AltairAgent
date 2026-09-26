package com.localaiagent.core.tools

import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import com.localaiagent.core.reminders.Reminder
import com.localaiagent.core.reminders.ReminderStore
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId

/**
 * Инструменты взаимодействия с пользователем: таймеры, события по времени и
 * условные уведомления. Хранятся в общем reminders.json; фоновый воркер приложения
 * проверяет их и шлёт уведомление И пользователю, И (в контексте) модели.
 */

private fun jstr(desc: String): JsonObject = buildJsonObject { put("type", "string"); put("description", desc) }

private fun objSchema(props: Map<String, JsonObject>, required: List<String>): JsonObject = buildJsonObject {
    put("type", "object")
    putJsonObject("properties") { props.forEach { (k, v) -> put(k, v) } }
    putJsonArray("required") { required.forEach { add(it) } }
}

private fun JsonObject.s(key: String): String = this[key]?.jsonPrimitive?.contentOrNull.orEmpty()

private fun remindersDir(ctx: ToolContext): File = File(ctx.globalMemoryDir)

private fun newId(): String = java.lang.Long.toHexString(System.nanoTime()).takeLast(8)

private fun chatIdOf(ctx: ToolContext): String = File(ctx.workspaceDir).name

/** Ставит напоминание/таймер на конкретное время. */
class SetReminderTool : Tool {
    override val name = "set_reminder"
    override val description =
        "Ставит напоминание на время. Укажи ЛИБО after_minutes (через сколько минут), " +
            "ЛИБО at (абсолютно: 'HH:mm' сегодня/завтра или 'yyyy-MM-dd HH:mm'). " +
            "note — что напомнить. Пользователь получит уведомление, а ты — контекст о срабатывании."
    override val category = ToolCategory.EDIT
    override fun schema() = objSchema(
        mapOf(
            "note" to jstr("Текст напоминания"),
            "after_minutes" to jstr("Через сколько минут сработает (напр. 30)"),
            "at" to jstr("Абсолютное время: 'HH:mm' или 'yyyy-MM-dd HH:mm'"),
        ),
        listOf("note"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val note = args.s("note").trim()
        if (note.isEmpty()) return ToolResult.fail("нужен текст напоминания (note)")
        val zone = ZoneId.systemDefault()
        val now = System.currentTimeMillis()
        val fireAt: Long = when {
            args.s("after_minutes").toDoubleOrNull() != null -> {
                val mins = args.s("after_minutes").toDouble()
                if (mins <= 0) return ToolResult.fail("after_minutes должно быть > 0")
                now + (mins * 60_000).toLong()
            }
            args.s("at").isNotBlank() -> parseAt(args.s("at"), zone)
                ?: return ToolResult.fail("не понял время 'at': нужно 'HH:mm' или 'yyyy-MM-dd HH:mm'")
            else -> return ToolResult.fail("укажи after_minutes или at")
        }
        val r = Reminder(
            id = newId(), kind = "time", note = note, chatId = chatIdOf(ctx),
            createdAt = now, fireAt = fireAt,
        )
        ReminderStore.add(remindersDir(ctx), r)
        val whenStr = LocalDateTime.ofInstant(java.time.Instant.ofEpochMilli(fireAt), zone)
            .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"))
        return ToolResult("Напоминание поставлено на $whenStr (id=${r.id}): $note")
    }

    private fun parseAt(raw: String, zone: ZoneId): Long? {
        val t = raw.trim()
        // Полная дата-время
        runCatching {
            val dt = LocalDateTime.parse(t.replace(' ', 'T'))
            return dt.atZone(zone).toInstant().toEpochMilli()
        }
        // Только время HH:mm — ближайшее сегодня/завтра
        runCatching {
            val time = LocalTime.parse(t)
            var dt = LocalDateTime.of(LocalDate.now(zone), time)
            if (dt.atZone(zone).toInstant().toEpochMilli() <= System.currentTimeMillis()) dt = dt.plusDays(1)
            return dt.atZone(zone).toInstant().toEpochMilli()
        }
        return null
    }
}

/** Ставит условное уведомление: сработает, когда сигнал устройства выполнит условие. */
class WatchConditionTool : Tool {
    override val name = "watch_condition"
    override val description =
        "Условное уведомление: сработает ОДИН раз, когда сигнал выполнит условие. " +
            "signal: battery (0..100) | charging (true/false) | pc_online (true/false) | network (online/offline). " +
            "op: >= | <= | == | !=. value — порог (напр. '80', 'true', 'online'). " +
            "Пример: 'сообщи когда зарядка выше 80%' → signal=battery, op=>=, value=80. " +
            "'сообщи когда ПК-агент готов' → signal=pc_online, op===, value=true."
    override val category = ToolCategory.EDIT
    override fun schema() = objSchema(
        mapOf(
            "note" to jstr("Что сообщить при срабатывании"),
            "signal" to jstr("battery | charging | pc_online | network"),
            "op" to jstr(">= | <= | == | !="),
            "value" to jstr("Порог/значение: '80', 'true', 'online'…"),
        ),
        listOf("note", "signal", "op", "value"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val note = args.s("note").trim()
        val signal = args.s("signal").trim().lowercase()
        val op = args.s("op").trim()
        val value = args.s("value").trim()
        if (note.isEmpty() || signal.isEmpty() || op.isEmpty() || value.isEmpty())
            return ToolResult.fail("нужны note, signal, op, value")
        if (signal !in setOf("battery", "charging", "pc_online", "network"))
            return ToolResult.fail("signal должен быть: battery | charging | pc_online | network")
        if (op !in setOf(">=", "<=", "==", "!="))
            return ToolResult.fail("op должен быть: >= | <= | == | !=")
        val r = Reminder(
            id = newId(), kind = "condition", note = note, chatId = chatIdOf(ctx),
            createdAt = System.currentTimeMillis(), signal = signal, op = op, value = value,
        )
        ReminderStore.add(remindersDir(ctx), r)
        return ToolResult("Слежу: когда $signal $op $value → «$note» (id=${r.id}).")
    }
}

/** Показывает активные напоминания/условия. */
class ListRemindersTool : Tool {
    override val name = "list_reminders"
    override val description = "Показывает активные (несработавшие) напоминания и условия с их id."
    override val category = ToolCategory.READ
    override fun schema() = objSchema(emptyMap(), emptyList())

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val list = ReminderStore.load(remindersDir(ctx)).filter { it.active }
        if (list.isEmpty()) return ToolResult("Активных напоминаний нет.")
        return ToolResult(list.joinToString("\n") { "• [${it.id}] ${it.describe()}" })
    }
}

/** Отменяет напоминание по id. */
class CancelReminderTool : Tool {
    override val name = "cancel_reminder"
    override val description = "Отменяет (удаляет) напоминание/условие по его id."
    override val category = ToolCategory.EDIT
    override fun schema() = objSchema(mapOf("id" to jstr("id напоминания")), listOf("id"))

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val id = args.s("id").trim()
        if (id.isEmpty()) return ToolResult.fail("нужен id")
        return if (ReminderStore.remove(remindersDir(ctx), id)) ToolResult("Отменено: $id")
        else ToolResult.fail("напоминание $id не найдено")
    }
}
