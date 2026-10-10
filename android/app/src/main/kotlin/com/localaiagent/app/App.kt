package com.localaiagent.app

import android.app.Activity
import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Bundle
import coil.ImageLoader
import coil.ImageLoaderFactory
import okhttp3.OkHttpClient

/**
 * Application: кастомный Coil [ImageLoader] (User-Agent для Wikimedia), каналы
 * уведомлений (Фаза 3) и простой трекер «приложение на переднем плане» — чтобы
 * слать уведомление о завершении только когда пользователь ушёл из приложения.
 */
class App : Application(), ImageLoaderFactory {

    override fun onCreate() {
        super.onCreate()
        createChannels()
        registerActivityLifecycleCallbacks(ForegroundTracker)
        com.localaiagent.app.servers.ServerHub.init(this)
        // Reading the list decrypts it through the Keystore: not on the startup path.
        Thread { com.localaiagent.app.servers.ServerHub.schedule() }.start()
        // Встроенный Python (Chaquopy) — стартуем один раз при запуске приложения.
        if (!com.chaquo.python.Python.isStarted()) {
            com.chaquo.python.Python.start(com.chaquo.python.android.AndroidPlatform(this))
        }
    }

    private fun createChannels() {
        val mgr = getSystemService(NotificationManager::class.java) ?: return
        val ctx = LocaleManager.wrap(this)
        mgr.createNotificationChannel(
            NotificationChannel(CHANNEL_RUN, ctx.getString(R.string.channel_run_name), NotificationManager.IMPORTANCE_LOW)
                .apply { description = ctx.getString(R.string.channel_run_desc) },
        )
        mgr.createNotificationChannel(
            NotificationChannel(CHANNEL_DONE, ctx.getString(R.string.notif_answer_ready), NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = ctx.getString(R.string.channel_done_desc) },
        )
        mgr.createNotificationChannel(
            NotificationChannel(CHANNEL_REMIND, ctx.getString(R.string.channel_remind_name), NotificationManager.IMPORTANCE_HIGH)
                .apply { description = ctx.getString(R.string.channel_remind_desc) },
        )
        mgr.createNotificationChannel(
            NotificationChannel(CHANNEL_SERVERS, ctx.getString(R.string.channel_servers_name), NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = ctx.getString(R.string.channel_servers_desc) },
        )
    }

    override fun newImageLoader(): ImageLoader {
        // Wikimedia (upload.wikimedia.org) answers 403 to OkHttp's default User-Agent: its policy
        // requires a descriptive one.
        val client = OkHttpClient.Builder()
            .addInterceptor { chain ->
                val req = chain.request().newBuilder()
                    .header(
                        "User-Agent",
                        "Altair/${BuildConfig.VERSION_NAME} (Android; +https://github.com/Qweezyy/AltairAgent) Coil",
                    )
                    .build()
                chain.proceed(req)
            }
            .build()
        return ImageLoader.Builder(this)
            .okHttpClient(client)
            .components { add(coil.decode.VideoFrameDecoder.Factory()) } // превью-кадры видео
            .build()
    }

    companion object {
        const val CHANNEL_RUN = "agent_run"
        const val CHANNEL_DONE = "agent_done"
        const val CHANNEL_REMIND = "agent_remind"
        const val CHANNEL_SERVERS = "servers"

        /** True while at least one activity is visible (the app is in the foreground). */
        val isForeground: Boolean get() = ForegroundTracker.started > 0

        /** Called on the main thread each time the app comes back to the foreground. */
        @Volatile var onForeground: (() -> Unit)? = null
    }

    /** Counts visible activities: a rough but reliable foreground signal. */
    private object ForegroundTracker : ActivityLifecycleCallbacks {
        var started = 0
            private set

        override fun onActivityStarted(activity: Activity) {
            if (started++ == 0) onForeground?.invoke()
        }
        override fun onActivityStopped(activity: Activity) { if (started > 0) started-- }
        override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
        override fun onActivityResumed(activity: Activity) {}
        override fun onActivityPaused(activity: Activity) {}
        override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
        override fun onActivityDestroyed(activity: Activity) {}
    }
}
