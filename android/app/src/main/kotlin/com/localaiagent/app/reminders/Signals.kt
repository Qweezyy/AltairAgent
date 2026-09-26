package com.localaiagent.app.reminders

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import com.localaiagent.core.reminders.Reminder
import java.io.File
import java.net.InetSocketAddress
import java.net.Socket

/** Чтение реальных сигналов устройства + оценка условий напоминаний. */
object Signals {

    data class Snapshot(
        val battery: Int,          // 0..100, -1 если неизвестно
        val charging: Boolean,
        val network: String,       // "wifi" | "cellular" | "online" | "offline"
        val pcOnline: Boolean,
    )

    fun battery(ctx: Context): Int =
        (ctx.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager)
            ?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1

    fun charging(ctx: Context): Boolean =
        (ctx.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager)?.isCharging ?: false

    /** Тип сети: wifi/cellular/online (иное соединение) / offline. */
    fun network(ctx: Context): String {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return "offline"
        val caps = cm.activeNetwork?.let { cm.getNetworkCapabilities(it) } ?: return "offline"
        if (!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) return "offline"
        return when {
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            else -> "online"
        }
    }

    /** Доступен ли ПК-агент: пробуем открыть сокет к host:port из url моста. */
    fun pcOnline(bridgeUrl: String): Boolean {
        if (bridgeUrl.isBlank()) return false
        val hp = hostPort(bridgeUrl) ?: return false
        return runCatching {
            Socket().use { it.connect(InetSocketAddress(hp.first, hp.second), 1200); true }
        }.getOrDefault(false)
    }

    private fun hostPort(url: String): Pair<String, Int>? {
        var s = url.trim()
        s = s.substringAfter("://", s)          // убрать схему
        s = s.substringBefore("/")               // убрать путь
        val host = s.substringBefore(":", s)
        val port = s.substringAfter(":", "").toIntOrNull() ?: 80
        if (host.isBlank()) return null
        return host to port
    }

    fun snapshot(ctx: Context, bridgeUrl: String): Snapshot =
        Snapshot(battery(ctx), charging(ctx), network(ctx), pcOnline(bridgeUrl))

    /** Выполнено ли условие напоминания при данном снимке сигналов. */
    fun conditionMet(r: Reminder, s: Snapshot): Boolean {
        return when (r.signal) {
            "battery" -> {
                if (s.battery < 0) return false
                val v = r.value.toIntOrNull() ?: return false
                compareNum(s.battery, r.op, v)
            }
            "charging" -> compareBool(s.charging, r.op, r.value)
            "pc_online" -> compareBool(s.pcOnline, r.op, r.value)
            "network" -> {
                val online = s.network != "offline"
                when (r.value.lowercase()) {
                    "online", "true" -> compareBool(online, r.op, "true")
                    "offline", "false" -> compareBool(online, r.op, "true").let { if (r.op == "==") !online else it }
                    "wifi", "cellular" -> if (r.op == "==") s.network == r.value.lowercase()
                    else s.network != r.value.lowercase()
                    else -> false
                }
            }
            else -> false
        }
    }

    private fun compareNum(actual: Int, op: String, threshold: Int): Boolean = when (op) {
        ">=" -> actual >= threshold
        "<=" -> actual <= threshold
        "==" -> actual == threshold
        "!=" -> actual != threshold
        else -> false
    }

    private fun compareBool(actual: Boolean, op: String, value: String): Boolean {
        val target = value.lowercase() in setOf("true", "1", "yes", "online", "on")
        return when (op) {
            "==" -> actual == target
            "!=" -> actual != target
            else -> false
        }
    }
}
