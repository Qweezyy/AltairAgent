package com.localaiagent.app.servers

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.localaiagent.app.App
import com.localaiagent.app.MainActivity
import com.localaiagent.app.Notifications
import com.localaiagent.app.R
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.util.concurrent.TimeUnit

/**
 * The phone's side of its servers, shared by the screens, the PC bridge and the background job:
 * the stored list, one client per server, and notices turned into notifications exactly once.
 */
@SuppressLint("StaticFieldLeak") // the application context only
object ServerHub {
    private lateinit var app: Context
    val store: ServerStore by lazy { ServerStore(app) }
    private val dedupe: NoticeDedupe by lazy { NoticeDedupe(initial = store.seenNotices()) }
    private val clients = HashMap<String, ServerClient>()

    /** The server chat on screen now: its notices are not notified (the user sees them). */
    @Volatile var viewing: Pair<String, String>? = null

    fun init(context: Context) {
        app = context.applicationContext
    }

    val ready: Boolean get() = ::app.isInitialized

    /** The client for a server, made again when its entry changed (a new QR, pairing done). */
    @Synchronized
    fun client(entry: ServerEntry): ServerClient {
        clients[entry.id]?.takeIf { it.entry == entry }?.let { return it }
        return ServerClient(entry, store.identity()).also { clients[entry.id] = it }
    }

    @Synchronized
    fun forget(id: String) {
        clients.remove(id)?.forgetToken()
        store.remove(id)
        schedule()
    }

    // ------------------------------------------------------------------ notices

    /** A notice from the PC's socket: `body_id` names the server. */
    fun onPcNotice(o: JsonObject) {
        if (!ready) return
        val n = ServerNotice.parse(o, selfServerId = null) ?: return
        // The PC also announces things that are not about this phone's servers.
        if (store.entry(n.serverId) == null) return
        onNotice(n)
    }

    /** A notice from a server's own socket or its news list: `self` is that server. */
    fun onServerNotice(serverId: String, o: JsonObject) {
        if (!ready) return
        ServerNotice.parse(o, selfServerId = serverId)?.let(::onNotice)
    }

    private fun onNotice(n: ServerNotice) {
        if (!dedupe.firstTime(n)) return
        store.saveSeenNotices(dedupe.snapshot())
        if (viewing == n.serverId to n.chat && App.isForeground) return
        post(n)
    }

    private fun post(n: ServerNotice) {
        if (!Notifications.canPost(app)) return
        val name = store.entry(n.serverId)?.name ?: n.serverId
        val open = Intent(app, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(EXTRA_SERVER, n.serverId)
            .putExtra(EXTRA_CHAT, n.chat)
        val pending = PendingIntent.getActivity(
            app, n.key.hashCode(), open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(app, App.CHANNEL_SERVERS)
            .setContentTitle(n.title.ifBlank { name })
            .setContentText(n.text.take(120))
            .setStyle(NotificationCompat.BigTextStyle().bigText(n.text.take(400)))
            .setSmallIcon(R.drawable.ic_notify)
            .setPriority(if (n.level == "info") NotificationCompat.PRIORITY_DEFAULT else NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(pending)
            .build()
        runCatching { NotificationManagerCompat.from(app).notify(20_000 + (n.key.hashCode() and 0xFFFF), notification) }
    }

    /**
     * Asks every paired server for its news since the last look (§6.3). The first look only takes the
     * position, so a new phone is not flooded with the past. Returns how many servers answered.
     */
    suspend fun poll(): Int {
        var answered = 0
        for (entry in store.entries().filter { it.paired }) {
            try {
                val since = entry.noticeNext
                val res = client(entry).get("/api/notices", if (since != null) mapOf("since" to since.toString()) else emptyMap())
                answered++
                if (since != null) {
                    (res["notices"] as? JsonArray).orEmpty().forEach { (it as? JsonObject)?.let { o -> onServerNotice(entry.id, o) } }
                }
                val next = res["next"]?.jsonPrimitive?.longOrNull ?: res["next"]?.jsonPrimitive?.contentOrNull?.toLongOrNull()
                if (next != null && next != since) store.update(entry.id) { it.copy(noticeNext = next) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ServerError) {
                // Offline or not trusted now: the next round tries again; the screens show why.
                android.util.Log.i("ServerHub", "notices of ${entry.id}: ${e.message}")
            }
        }
        return answered
    }

    /** Runs the background news check while there is a paired server, and stops it when there is none. */
    fun schedule() {
        if (!ready) return
        val wm = runCatching { WorkManager.getInstance(app) }.getOrNull() ?: return
        if (store.entries().none { it.paired }) {
            wm.cancelUniqueWork(WORK)
            return
        }
        val request = PeriodicWorkRequestBuilder<NoticeWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        wm.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    private fun JsonArray?.orEmpty(): List<kotlinx.serialization.json.JsonElement> = this ?: emptyList()

    const val EXTRA_SERVER = "altair.server_id"
    const val EXTRA_CHAT = "altair.server_chat"
    private const val WORK = "server-notices"
}

/** The background news check (WorkManager, every 15 minutes at most). */
class NoticeWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        ServerHub.init(applicationContext)
        ServerHub.poll()
        return Result.success()
    }
}
