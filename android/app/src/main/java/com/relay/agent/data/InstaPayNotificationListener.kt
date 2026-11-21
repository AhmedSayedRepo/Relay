package com.relay.agent.data

import android.app.Notification
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationManagerCompat
import com.relay.agent.RelayApplication
import com.relay.agent.domain.CollectionOutcome
import kotlinx.coroutines.launch

/**
 * Reads InstaPay's "money received" notifications on the bank SMS phone and
 * sends them to Relay as a second source next to the bank SMS. Only InstaPay's
 * own notifications are read, and only those about money received (English or
 * Arabic); codes and every other app's notifications are left alone.
 */
class InstaPayNotificationListener : NotificationListenerService() {
    override fun onListenerConnected() {
        // Notifications still shown from before the app was running.
        runCatching { activeNotifications }.getOrNull()?.forEach(::read)
    }

    override fun onNotificationPosted(notification: StatusBarNotification) = read(notification)

    private fun read(notification: StatusBarNotification) {
        val text = moneyReceived(notification) ?: return
        val app = RelayApplication.from(applicationContext)
        app.applicationScope.launch {
            val outcome = app.smsCollector.collectInstaPay(text, notification.postTime)
            if (outcome == CollectionOutcome.CAPTURED) app.uploadScheduler.enqueue()
        }
    }

    companion object {
        private val RECEIVED = Regex(
            "received|credited|استلام|استلمت|إستلام|وصلك|تم إضافة|تم اضافة|وارد",
            RegexOption.IGNORE_CASE
        )
        private val MONEY = Regex("EGP|جنيه|ج\\.م", RegexOption.IGNORE_CASE)
        private val SECRET = Regex("OTP|code|رمز|كود|password|كلمة", RegexOption.IGNORE_CASE)
        private val SPACES = Regex("\\s+")

        /** The text of an InstaPay "money received" notification, or null. */
        fun moneyReceived(notification: StatusBarNotification): String? {
            if (!notification.packageName.contains("instapay", ignoreCase = true)) return null
            val extras = notification.notification.extras
            val raw = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
                ?: extras.getCharSequence(Notification.EXTRA_TEXT)
                ?: return null
            val text = raw.toString().replace(SPACES, " ").trim()
            return text.takeIf { worthSending(it) }
        }

        fun worthSending(text: String): Boolean = text.isNotEmpty() &&
            text.length <= 1000 &&
            RECEIVED.containsMatchIn(text) &&
            MONEY.containsMatchIn(text) &&
            text.any { it.isDigit() } &&
            !SECRET.containsMatchIn(text)

        /** Whether the person allowed Relay to read notifications. */
        fun allowed(context: Context): Boolean =
            NotificationManagerCompat.getEnabledListenerPackages(context)
                .contains(context.packageName)
    }
}
