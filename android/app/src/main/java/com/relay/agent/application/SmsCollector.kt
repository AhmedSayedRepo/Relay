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
    suspend fun collect(message: IncomingSms): CollectionOutcome {
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
        val rule = configuration.sms.rules.firstOrNull { it.matches(message.sender, message.body) }
        if (rule == null) {
            log("sms.filtered rule_count=${configuration.sms.rules.size}")
            return CollectionOutcome.FILTERED
        }
        val capturedAt = now().toString()
        val value = GenericEvent(
            newEventId(),
            state.installationId,
            "sms",
            1,
            configuration.version,
            Instant.ofEpochMilli(message.receivedAtMillis).toString(),
            capturedAt,
            message.sender,
            message.body,
            rule.id
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
    }
}
