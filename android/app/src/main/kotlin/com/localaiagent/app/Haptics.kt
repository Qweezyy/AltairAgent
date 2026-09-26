package com.localaiagent.app

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * «Хаптик-язык» агента: разные тактильные паттерны на события —
 * начал думать / нужен ты / готово. Тонко, не назойливо.
 */
object Haptics {

    private fun vibrator(ctx: Context): Vibrator? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            (ctx.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            ctx.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }

    private fun play(ctx: Context, timings: LongArray, amplitudes: IntArray) {
        val v = vibrator(ctx) ?: return
        if (!v.hasVibrator()) return
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                v.vibrate(VibrationEffect.createWaveform(timings, amplitudes, -1))
            } else {
                @Suppress("DEPRECATION")
                v.vibrate(timings, -1)
            }
        }
    }

    /** Агент начал работу — лёгкий тик. */
    fun thinking(ctx: Context) = play(ctx, longArrayOf(0, 16), intArrayOf(0, 70))

    /** Нужен ты (форма ask/файл/секрет/память) — два уверенных импульса. */
    fun needYou(ctx: Context) = play(ctx, longArrayOf(0, 40, 90, 40), intArrayOf(0, 180, 0, 180))

    /** Готово — мягкий двойной тик. */
    fun done(ctx: Context) = play(ctx, longArrayOf(0, 18, 50, 26), intArrayOf(0, 110, 0, 150))
}
