package com.localaiagent.app.reminders

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import com.localaiagent.app.Notifications
import com.localaiagent.app.data.SettingsStore
import com.localaiagent.core.reminders.Reminder
import com.localaiagent.core.reminders.ReminderStore
import kotlinx.coroutines.runBlocking
import java.io.File

/**
 * Планировщик напоминаний на AlarmManager (без WorkManager):
 * — периодический неточный будильник каждые ~15 мин опрашивает условия;
 * — на каждое время-напоминание ставится отдельный будильник для точности.
 * Все будильники ведут в [ReminderReceiver], который вызывает [check].
 */
object ReminderScheduler {
    private const val ACTION = "com.localaiagent.app.REMINDER_TICK"
    private const val POLL_CODE = 424242

    /** Папка общей памяти = место хранения reminders.json. */
    fun dir(ctx: Context): File = File(ctx.filesDir, "memory")

    private fun alarmMgr(ctx: Context) = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager

    private fun pi(ctx: Context, code: Int): PendingIntent {
        val i = Intent(ctx, ReminderReceiver::class.java).setAction(ACTION)
        return PendingIntent.getBroadcast(
            ctx, code, i,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    /** Планирует опрос условий + точечные будильники под каждое время-напоминание. */
    fun sync(ctx: Context) {
        val list = ReminderStore.load(dir(ctx)).filter { it.active }
        val am = alarmMgr(ctx)
        // Периодический опрос условий — только если есть условные напоминания.
        if (list.any { it.kind == "condition" }) {
            val first = System.currentTimeMillis() + 60_000
            am.setInexactRepeating(
                AlarmManager.RTC_WAKEUP, first, AlarmManager.INTERVAL_FIFTEEN_MINUTES, pi(ctx, POLL_CODE),
            )
        } else {
            runCatching { am.cancel(pi(ctx, POLL_CODE)) }
        }
        // Точные будильники под каждое время-напоминание.
        for (r in list.filter { it.kind == "time" }) {
            runCatching {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.fireAt, pi(ctx, r.id.hashCode()))
            }
        }
    }

    /** Проверяет все напоминания; сработавшие — уведомляет и помечает fired. */
    fun check(ctx: Context) {
        val d = dir(ctx)
        val list = ReminderStore.load(d)
        if (list.isEmpty()) return
        val bridgeUrl = runCatching { runBlocking { SettingsStore(ctx).loadBridge().url } }.getOrDefault("")
        val needSignals = list.any { it.active && it.kind == "condition" }
        val snap = if (needSignals) Signals.snapshot(ctx, bridgeUrl) else null
        val now = System.currentTimeMillis()
        var changed = false
        val updated = list.map { r ->
            if (!r.active) return@map r
            val fire = when (r.kind) {
                "time" -> now >= r.fireAt
                "condition" -> snap != null && Signals.conditionMet(r, snap)
                else -> false
            }
            if (fire) {
                Notifications.notifyReminder(ctx, r.id.hashCode(), r.note)
                changed = true
                r.copy(fired = true, firedAt = now)
            } else r
        }
        if (changed) {
            ReminderStore.save(d, updated)
            sync(ctx) // перепланировать оставшиеся
        }
    }

    fun cancelAll(ctx: Context) {
        runCatching { alarmMgr(ctx).cancel(pi(ctx, POLL_CODE)) }
    }
}
