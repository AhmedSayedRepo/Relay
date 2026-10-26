package com.relay.agent.data

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import com.relay.agent.RelayApplication
import com.relay.agent.domain.CollectionOutcome
import com.relay.agent.domain.IncomingSms
import kotlinx.coroutines.launch

/** Receives only new SMS broadcasts. It never queries or backfills the SMS provider. */
class SmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent)
        if (messages.isEmpty()) return
        val sender = messages.first().displayOriginatingAddress ?: return
        val body = messages.joinToString(separator = "") { it.displayMessageBody.orEmpty() }
        if (body.isEmpty()) return
        val receivedAt = messages.minOf { it.timestampMillis }
        val pending = goAsync()
        val app = RelayApplication.from(context)
        app.applicationScope.launch {
            try {
                if (
                    app.smsCollector.collect(IncomingSms(sender, body, receivedAt)) ==
                    CollectionOutcome.CAPTURED
                ) {
                    app.uploadScheduler.enqueue()
                    app.controller.refreshPrivacyState()
                }
            } finally {
                pending.finish()
            }
        }
    }
}
