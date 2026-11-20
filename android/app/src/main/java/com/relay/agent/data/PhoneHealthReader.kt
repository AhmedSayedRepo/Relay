package com.relay.agent.data

import android.app.ActivityManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import androidx.core.net.toUri
import java.time.Instant
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * What the server needs to explain a silent phone, sent with each heartbeat:
 * when the phone and the app last started, why Android closed the app,
 * whether battery saving holds it back, and whether the photo folders can
 * still be read. No personal data: times, flags and Android's own reason names.
 */
class PhoneHealthReader(
    private val context: Context,
    private val folders: suspend () -> List<String>
) {
    suspend fun read(): JsonObject {
        val now = System.currentTimeMillis()
        val sinceBoot = SystemClock.elapsedRealtime()
        val power = context.getSystemService(PowerManager::class.java)
        val battery = context.getSystemService(BatteryManager::class.java)
        val activity = context.getSystemService(ActivityManager::class.java)
        val granted = context.contentResolver.persistedUriPermissions
            .filter { it.isReadPermission }
            .map { it.uri }
            .toSet()
        val chosen = folders()
        return buildJsonObject {
            put("reported_at", iso(now))
            put("boot_at", iso(now - sinceBoot))
            put("app_started_at", iso(now - (sinceBoot - Process.getStartElapsedRealtime())))
            put("battery_unrestricted", power.isIgnoringBatteryOptimizations(context.packageName))
            put("power_save", power.isPowerSaveMode)
            put("battery_percent", battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
            put("charging", battery.isCharging)
            put("folders_ok", chosen.all { it.toUri() in granted })
            put("android", Build.VERSION.SDK_INT)
            put("maker", Build.MANUFACTURER.take(40))
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                put("background_restricted", activity.isBackgroundRestricted)
                val usage = context.getSystemService(UsageStatsManager::class.java)
                put("standby_bucket", bucket(usage.appStandbyBucket))
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                put(
                    "exits",
                    buildJsonArray {
                        activity.getHistoricalProcessExitReasons(null, 0, MAX_EXITS)
                            .forEach { exit ->
                                add(
                                    buildJsonObject {
                                        put("at", iso(exit.timestamp))
                                        put("reason", exitReason(exit.reason))
                                    }
                                )
                            }
                    }
                )
            }
        }
    }

    private fun iso(millis: Long): String = Instant.ofEpochMilli(millis).toString()

    companion object {
        const val MAX_EXITS = 5

        // Android's numbers, written out: the named constants are newer than
        // this app's oldest Android and would trip the API check.
        fun bucket(value: Int): String = when (value) {
            10 -> "active"
            20 -> "working_set"
            30 -> "frequent"
            40 -> "rare"
            45 -> "restricted"
            else -> "other"
        }

        /** ApplicationExitInfo.REASON_* as words. */
        fun exitReason(value: Int): String = when (value) {
            1 -> "exit_self"
            2, 13 -> "system"
            3 -> "low_memory"
            4, 5, 6 -> "crash"
            9 -> "battery_saving"
            10 -> "force_stopped"
            11 -> "user_stopped"
            14 -> "frozen"
            16 -> "updated"
            else -> "other"
        }
    }
}
