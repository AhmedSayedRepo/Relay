package com.relay.agent.application

import android.util.Log
import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.CollectionOutcome
import com.relay.agent.domain.EventOutbox
import com.relay.agent.domain.GenericEvent
import com.relay.agent.domain.IncomingSms
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException

class SmsCollector(
    private val store: AgentStore,
    private val outbox: EventOutbox,
    private val permissionGranted: () -> Boolean,
    private val now: () -> Instant = Instant::now,
    private val newEventId: () -> String = { UUID.randomUUID().toString() },
    private val log: (String) -> Unit = { Log.i(TAG, it) }
) {
    /**
     * A bank SMS caught up from the phone's inbox (after the app was missing
     * or stopped). Its event id comes from the message itself, so catching up
     * twice sends it once; the server also skips an SMS it already has.
     */
    suspend fun collectMissed(message: IncomingSms): CollectionOutcome {
        val installation = store.read().installationId
        val key = "$installation|sms|${message.sender}|${message.receivedAtMillis}|${message.body}"
        return collect(message, UUID.nameUUIDFromBytes(key.toByteArray()).toString())
    }

    /**
     * An InstaPay app notification ("You have received 820.00 EGP from ..."):
     * a second source next to the bank SMS, for when the bank sends no SMS.
     * Named after its content, so a notification shown again is sent once.
     */
    suspend fun collectInstaPay(text: String, postedAtMillis: Long): CollectionOutcome {
        val installation = store.read().installationId
        val key = "$installation|instapay|$postedAtMillis|$text"
        return collect(
            IncomingSms(INSTAPAY_SENDER, text, postedAtMillis),
            UUID.nameUUIDFromBytes(key.toByteArray()).toString(),
            INSTAPAY_RULE
        )
    }

    suspend fun collect(
        message: IncomingSms,
        eventId: String? = null,
        fixedRule: String? = null
    ): CollectionOutcome {
        val state = store.read()
        val configuration = state.configuration
        if (
            !state.localSmsConsent ||
            !permissionGranted() ||
            state.credentials == null ||
            configuration == null ||
            !configuration.sms.enabled
        ) {
            log("sms.collection_blocked")
            return CollectionOutcome.BLOCKED
        }
        if (!valid(message)) {
            log("sms.invalid")
            return CollectionOutcome.INVALID
        }
        val ruleId = fixedRule
            ?: configuration.sms.rules.firstOrNull { it.matches(message.sender, message.body) }?.id
        if (ruleId == null) {
            log("sms.filtered rule_count=${configuration.sms.rules.size}")
            return CollectionOutcome.FILTERED
        }
        val capturedAt = now().toString()
        val value = GenericEvent(
            eventId ?: newEventId(),
            state.installationId,
            "sms",
            1,
            configuration.version,
            Instant.ofEpochMilli(message.receivedAtMillis).toString(),
            capturedAt,
            message.sender,
            message.body,
            ruleId
        )
        val result = try {
            outbox.enqueue(value)
        } catch (error: Exception) {
            if (error is CancellationException) throw error
            log("sms.outbox_unavailable event_id=${value.eventId}")
            return CollectionOutcome.CAPACITY
        }
        log(
            "sms.capture_result event_id=${value.eventId} accepted=${result.accepted} " +
                "dropped_oldest=${result.droppedOldest}"
        )
        return if (result.accepted) CollectionOutcome.CAPTURED else CollectionOutcome.CAPACITY
    }

    private fun valid(message: IncomingSms): Boolean = message.sender.isNotBlank() &&
        message.sender == message.sender.trim() &&
        message.sender.length <= 160 &&
        message.sender.none { it.code < 32 } &&
        message.body.isNotEmpty() &&
        message.body.toByteArray(Charsets.UTF_8).size <= 4096 &&
        message.body.none { it.code < 32 && it !in "\t\n\r" }

    companion object {
        private const val TAG = "RelayCollection"
        const val INSTAPAY_SENDER = "InstaPay"
        const val INSTAPAY_RULE = "instapay-app"
    }
}
