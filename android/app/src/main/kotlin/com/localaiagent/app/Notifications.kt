package com.localaiagent.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/** Локальные уведомления агента (Фаза 3). */
object Notifications {

    private const val DONE_ID = 2001

    fun canPost(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    /** Уведомление «ответ готов» — показываем, когда пользователь вне приложения. */
    fun notifyDone(ctx: Context, body: String) {
        if (!canPost(ctx)) return
        val n = NotificationCompat.Builder(ctx, App.CHANNEL_DONE)
            .setContentTitle(LocaleManager.wrap(ctx).getString(R.string.notif_answer_ready))
            .setContentText(body.take(120))
            .setStyle(NotificationCompat.BigTextStyle().bigText(body.take(400)))
            .setSmallIcon(R.drawable.ic_notify)
            .setAutoCancel(true)
            .setContentIntent(AgentService.openAppIntent(ctx))
            .build()
        NotificationManagerCompat.from(ctx).notify(DONE_ID, n)
    }

    /** Сработало напоминание/условие — уведомляем пользователя. */
    fun notifyReminder(ctx: Context, id: Int, body: String) {
        if (!canPost(ctx)) return
        val n = NotificationCompat.Builder(ctx, App.CHANNEL_REMIND)
            .setContentTitle(LocaleManager.wrap(ctx).getString(R.string.notif_reminder))
            .setContentText(body.take(120))
            .setStyle(NotificationCompat.BigTextStyle().bigText(body.take(400)))
            .setSmallIcon(R.drawable.ic_notify)
            .setAutoCancel(true)
            .setContentIntent(AgentService.openAppIntent(ctx))
            .build()
        NotificationManagerCompat.from(ctx).notify(10_000 + (id and 0xFFFF), n)
    }
}
