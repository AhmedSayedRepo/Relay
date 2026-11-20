package com.relay.agent.data

import android.content.Context
import android.provider.Telephony
import com.relay.agent.domain.IncomingSms

/**
 * Reads received SMS back from the phone's inbox, for catching up on bank SMS
 * that arrived while Relay Bank SMS was not installed or not running. Needs
 * READ_SMS, which the person grants only when they ask to catch up.
 */
class SmsInbox(private val context: Context) {
    fun since(millis: Long): List<IncomingSms> {
        val found = mutableListOf<IncomingSms>()
        val columns = arrayOf(
            Telephony.Sms.ADDRESS,
            Telephony.Sms.BODY,
            Telephony.Sms.DATE_SENT,
            Telephony.Sms.DATE
        )
        context.contentResolver.query(
            Telephony.Sms.Inbox.CONTENT_URI,
            columns,
            "${Telephony.Sms.DATE} >= ?",
            arrayOf(millis.toString()),
            "${Telephony.Sms.DATE} ASC"
        )?.use { cursor ->
            while (cursor.moveToNext()) {
                val sender = cursor.getString(0) ?: continue
                val body = cursor.getString(1) ?: continue
                // The time the live receiver records is the service centre's
                // (DATE_SENT); the same time here lets the server see a repeat.
                val sent = cursor.getLong(2)
                found += IncomingSms(sender, body, if (sent > 0) sent else cursor.getLong(3))
            }
        }
        return found
    }
}
