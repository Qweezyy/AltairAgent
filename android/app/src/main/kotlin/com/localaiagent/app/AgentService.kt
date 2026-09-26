package com.localaiagent.app

import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat

/**
 * Foreground-сервис на время активного прогона агента (Фаза 3, «всегда доступен»).
 * Его задача — не дать системе убить процесс, пока агент думает/ходит в сеть, и
 * показывать неубираемое уведомление «работаю». Сам цикл агента живёт в
 * viewModelScope; сервис лишь удерживает процесс живым и стартует/останавливается
 * из [ChatViewModel].
 */
class AgentService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val text = intent?.getStringExtra(EXTRA_TEXT) ?: LocaleManager.wrap(this).getString(R.string.notif_agent_running)
        val notif = NotificationCompat.Builder(this, App.CHANNEL_RUN)
            .setContentTitle("Altair")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_notify)
            .setOngoing(true)
            .setContentIntent(openAppIntent(this))
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notif)
        }
        return START_NOT_STICKY
    }

    companion object {
        private const val EXTRA_TEXT = "text"
        private const val NOTIF_ID = 1001

        fun start(ctx: Context, text: String) {
            val i = Intent(ctx, AgentService::class.java).putExtra(EXTRA_TEXT, text)
            ContextCompat.startForegroundService(ctx, i)
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, AgentService::class.java))
        }

        internal fun openAppIntent(ctx: Context): PendingIntent {
            val launch = Intent(ctx, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
            return PendingIntent.getActivity(
                ctx, 0, launch,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }
    }
}
