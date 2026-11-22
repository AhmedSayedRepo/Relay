@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation

import android.Manifest
import android.app.ActivityManager
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.StringRes
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.lifecycle.compose.LifecycleResumeEffect
import com.relay.agent.R
import com.relay.agent.data.KeepConnectedService

/** A phone setting that stops Relay syncing in the background, worst first. */
enum class PhoneProblem(@StringRes val title: Int, @StringRes val body: Int) {
    NOTIFICATIONS_OFF(R.string.check_notifications_title, R.string.check_notifications_body),
    POWER_SAVE(R.string.check_power_save_title, R.string.check_power_save_body),
    BATTERY_RESTRICTED(R.string.check_battery_title, R.string.check_battery_body),
    BACKGROUND_RESTRICTED(R.string.check_background_title, R.string.check_background_body),
    BACKGROUND_DATA(R.string.check_data_title, R.string.check_data_body)
}

/** What is wrong right now; empty when the phone lets Relay run. */
fun phoneProblems(context: Context): List<PhoneProblem> {
    val problems = mutableListOf<PhoneProblem>()
    if (!notificationsAllowed(context)) problems += PhoneProblem.NOTIFICATIONS_OFF
    val power = context.getSystemService(PowerManager::class.java)
    if (power.isPowerSaveMode) problems += PhoneProblem.POWER_SAVE
    if (!power.isIgnoringBatteryOptimizations(context.packageName)) {
        problems += PhoneProblem.BATTERY_RESTRICTED
    }
    val activity = context.getSystemService(ActivityManager::class.java)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P && activity.isBackgroundRestricted) {
        problems += PhoneProblem.BACKGROUND_RESTRICTED
    }
    val network = context.getSystemService(ConnectivityManager::class.java)
    val dataBlocked = ConnectivityManager.RESTRICT_BACKGROUND_STATUS_ENABLED
    if (network.restrictBackgroundStatus == dataBlocked) problems += PhoneProblem.BACKGROUND_DATA
    return problems
}

/** Notifications on for the app, and the "Relay is connected" channel not muted. */
fun notificationsAllowed(context: Context): Boolean {
    val manager = NotificationManagerCompat.from(context)
    if (!manager.areNotificationsEnabled()) return false
    val channel = context.getSystemService(NotificationManager::class.java)
        .getNotificationChannel(KeepConnectedService.CHANNEL)
    return channel == null || channel.importance != NotificationManager.IMPORTANCE_NONE
}

/**
 * On every return to the app, the first phone setting that would stop Relay
 * syncing, with a button to the exact screen that fixes it. "Later" hides
 * that one until the app is opened again.
 */
@Composable
fun PhoneCheckPrompt() {
    val context = LocalContext.current
    var problems by remember { mutableStateOf(emptyList<PhoneProblem>()) }
    var later by remember { mutableStateOf(emptySet<PhoneProblem>()) }
    LifecycleResumeEffect(Unit) {
        problems = phoneProblems(context)
        onPauseOrDispose {}
    }
    val askNotifications = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) KeepConnectedService.start(context) else openNotificationSettings(context)
        problems = phoneProblems(context)
    }
    val shown = problems.firstOrNull { it !in later } ?: return
    AlertDialog(
        onDismissRequest = { later = later + shown },
        title = { Text(stringResource(shown.title)) },
        text = { Text(stringResource(shown.body)) },
        confirmButton = {
            TextButton(onClick = {
                if (shown == PhoneProblem.NOTIFICATIONS_OFF && canAsk(context)) {
                    askNotifications.launch(NOTIFY)
                } else {
                    fix(context, shown)
                }
            }) { Text(stringResource(R.string.check_fix)) }
        },
        dismissButton = {
            TextButton(onClick = { later = later + shown }) {
                Text(stringResource(R.string.update_later))
            }
        }
    )
}

// Manifest.permission.POST_NOTIFICATIONS, spelled out: the field is API 33+
// and only used after the version check below.
private const val NOTIFY = "android.permission.POST_NOTIFICATIONS"

/** Android 13+ asks for the notification permission in the app itself. */
private fun canAsk(context: Context): Boolean {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return false
    val state = ContextCompat.checkSelfPermission(context, NOTIFY)
    return state != PackageManager.PERMISSION_GRANTED
}

private fun fix(context: Context, problem: PhoneProblem) {
    val app = "package:${context.packageName}".toUri()
    when (problem) {
        PhoneProblem.NOTIFICATIONS_OFF -> openNotificationSettings(context)
        PhoneProblem.POWER_SAVE -> open(context, Intent(Settings.ACTION_BATTERY_SAVER_SETTINGS))
        PhoneProblem.BATTERY_RESTRICTED -> askToRunInBackground(context)
        PhoneProblem.BACKGROUND_RESTRICTED ->
            open(context, Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, app))
        PhoneProblem.BACKGROUND_DATA -> open(
            context,
            Intent(Settings.ACTION_IGNORE_BACKGROUND_DATA_RESTRICTIONS_SETTINGS, app)
        )
    }
}

private fun openNotificationSettings(context: Context) {
    open(
        context,
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
    )
}

/** The exact settings screen, else the app's own page in Settings. */
private fun open(context: Context, intent: Intent) {
    try {
        context.startActivity(intent)
    } catch (_: ActivityNotFoundException) {
        try {
            context.startActivity(
                Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    "package:${context.packageName}".toUri()
                )
            )
        } catch (_: ActivityNotFoundException) {
            Unit
        }
    }
}
