package com.relay.agent.data

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.relay.agent.R
import com.relay.agent.RelayApplication
import com.relay.agent.domain.SyncOutcome
import com.relay.agent.presentation.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock

/**
 * Keeps the app connected with a small permanent notification. Phones such as
 * Redmi and Oppo never wake a closed app in the background, even with battery
 * set to Unrestricted, so the 15-minute check-in stopped until someone opened
 * the app. A running notification keeps it alive: it syncs, uploads receipts
 * and bank SMS, and reports to Relay every 15 minutes.
 */
class KeepConnectedService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loop: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!showNotification()) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (loop?.isActive != true) {
            loop = scope.launch {
                val app = RelayApplication.from(applicationContext)
                while (isActive) {
                    if (app.store.read().credentials == null) {
                        stopSelf()
                        break
                    }
                    runCatching { cycle(app) }
                    delay(INTERVAL_MILLIS)
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun cycle(app: RelayApplication) = app.cycleLock.withLock {
        if (app.controller.sync() == SyncOutcome.SUCCESS) {
            app.controller.upload()
            app.imageCollector.collect()
            app.imageUploader.upload()
        }
    }

    // The manifest declares specialUse; lint misses it once the full build adds a listener.
    @SuppressLint("ForegroundServiceType")
    private fun showNotification(): Boolean {
        getSystemService(NotificationManager::class.java)?.createNotificationChannel(
            NotificationChannel(
                CHANNEL,
                getString(R.string.keep_connected_channel),
                NotificationManager.IMPORTANCE_MIN
            )
        )
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(getString(R.string.keep_connected_title))
            .setContentText(getString(R.string.keep_connected_text))
            .setContentIntent(open)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .build()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else {
            0
        }
        return try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, type)
            true
        } catch (_: RuntimeException) {
            false // Android refused (started from the background): try again later
        }
    }

    companion object {
        const val CHANNEL = "relay_connected"
        private const val NOTIFICATION_ID = 4108
        private const val INTERVAL_MILLIS = 15L * 60 * 1000

        /** Starts it when the app opens, after an update and after a restart. */
        fun start(context: Context) {
            try {
                ContextCompat.startForegroundService(
                    context,
                    Intent(context, KeepConnectedService::class.java)
                )
            } catch (_: RuntimeException) {
                Unit // not allowed from the background now; the next app open starts it
            }
        }
    }
}
