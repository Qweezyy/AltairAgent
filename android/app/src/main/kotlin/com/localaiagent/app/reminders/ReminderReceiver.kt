package com.localaiagent.app.reminders

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import kotlin.concurrent.thread

/** Будильник сработал — проверяем напоминания в фоне. */
class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        val app = context.applicationContext
        thread {
            try {
                ReminderScheduler.check(app)
            } finally {
                pending.finish()
            }
        }
    }
}
