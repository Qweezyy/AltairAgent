package com.localaiagent.app.reminders

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import kotlin.concurrent.thread

/** После перезагрузки перепланируем будильники напоминаний. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val pending = goAsync()
        val app = context.applicationContext
        thread {
            try {
                ReminderScheduler.sync(app)
                ReminderScheduler.check(app)
            } finally {
                pending.finish()
            }
        }
    }
}
