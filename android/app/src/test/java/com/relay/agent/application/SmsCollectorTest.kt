package com.relay.agent.application

import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.CollectionOutcome
import com.relay.agent.domain.Configuration
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.EnqueueResult
import com.relay.agent.domain.EventOutbox
import com.relay.agent.domain.GenericEvent
import com.relay.agent.domain.IncomingSms
import com.relay.agent.domain.OutboxEntry
import com.relay.agent.domain.OutboxStats
import com.relay.agent.domain.SmsConfiguration
import com.relay.agent.domain.SmsRule
import com.relay.agent.domain.StoredState
import java.time.Instant
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SmsCollectorTest {
    private val credentials =
        Credentials(
            "device",
            "installation",
            "Managed",
            "access",
            "refresh",
            "2099-01-01T00:00:00Z"
        )
    private val rule = SmsRule(
        "receipt",
        true,
        "suffix",
        "Bank",
        null,
        listOf("credited", "received"),
        listOf("amount", "reference")
    )
    private val enabled = Configuration(
        7,
        "{}",
        sms = SmsConfiguration(true, listOf(rule))
    )
    private val message = IncomingSms(
        "ExampleBank",
        "Amount credited with reference 123",
        1_700_000_000_000
    )

    @Test
    fun everyPrivacyAndAuthorityGateMustBeEnabled() = runTest {
        val states = listOf(
            StoredState("installation", credentials, enabled, localSmsConsent = false),
            StoredState("installation", credentials, enabled, localSmsConsent = true),
            StoredState(
                "installation",
                credentials,
                enabled.copy(sms = SmsConfiguration(false, listOf(rule))),
                localSmsConsent = true
            ),
            StoredState("installation", null, enabled, localSmsConsent = true),
            StoredState("installation", credentials, null, localSmsConsent = true)
        )
        states.forEachIndexed { index, state ->
            val outbox = RecordingOutbox()
            val permission = index != 1
            val collector = collector(state, outbox, permission)
            assertEquals(CollectionOutcome.BLOCKED, collector.collect(message))
            assertTrue(outbox.events.isEmpty())
        }
    }

    @Test
    fun matchingMessageCreatesVersionedGenericEventAndNonMatchIsIgnored() = runTest {
        val outbox = RecordingOutbox()
        val state = StoredState("installation", credentials, enabled, localSmsConsent = true)
        val collector = collector(state, outbox, true)

        assertEquals(
            CollectionOutcome.FILTERED,
            collector.collect(message.copy(body = "Unrelated"))
        )
        assertTrue(outbox.events.isEmpty())

        assertEquals(CollectionOutcome.CAPTURED, collector.collect(message))
        val event = outbox.events.single()
        assertEquals("event-id", event.eventId)
        assertEquals("installation", event.installationId)
        assertEquals("sms", event.collector)
        assertEquals(1, event.schemaVersion)
        assertEquals(7, event.configVersion)
        assertEquals("receipt", event.ruleId)
        assertEquals(message.sender, event.sender)
        assertEquals(message.body, event.body)
        assertEquals("2023-11-14T22:13:20Z", event.occurredAt)
        assertEquals("2024-01-01T00:00:00Z", event.capturedAt)
        assertFalse(event.toString().contains(message.body))
    }

    @Test
    fun permissionWithdrawalStopsSubsequentCollectionImmediately() = runTest {
        var permission = true
        val outbox = RecordingOutbox()
        val state = StoredState("installation", credentials, enabled, localSmsConsent = true)
        val collector = SmsCollector(
            StaticStore(state),
            outbox,
            { permission },
            { Instant.parse("2024-01-01T00:00:00Z") },
            { "event-id" },
            {}
        )
        assertEquals(CollectionOutcome.CAPTURED, collector.collect(message))
        permission = false
        assertEquals(CollectionOutcome.BLOCKED, collector.collect(message))
        assertEquals(1, outbox.events.size)
    }

    @Test
    fun rulePrimitivesAreCaseInsensitiveDeterministicAndDisabledRulesNeverMatch() {
        val body = "Payment RECEIVED for Invoice and Reference"
        fun match(mode: String, value: String) = SmsRule(
            mode,
            true,
            mode,
            value,
            "payment",
            listOf("missing", "received"),
            listOf("invoice", "reference")
        ).matches("ExampleBank", body)

        assertTrue(match("exact", "examplebank"))
        assertTrue(match("contains", "PLEB"))
        assertTrue(match("prefix", "EXAMPLE"))
        assertTrue(match("suffix", "BANK"))
        assertFalse(match("exact", "other"))
        assertFalse(rule.copy(enabled = false).matches(message.sender, message.body))
        assertFalse(rule.matches(message.sender, "Amount credited without the required token"))
    }

    @Test
    fun malformedOrOversizedSmsIsRejectedBeforePersistenceAndLogsStayMetadataOnly() = runTest {
        val logs = mutableListOf<String>()
        val outbox = RecordingOutbox()
        val state = StoredState("installation", credentials, enabled, localSmsConsent = true)
        val collector = SmsCollector(
            StaticStore(state),
            outbox,
            { true },
            { Instant.parse("2024-01-01T00:00:00Z") },
            { "event-id" },
            logs::add
        )
        val secret = "sensitive-message"
        assertEquals(
            CollectionOutcome.INVALID,
            collector.collect(message.copy(body = secret.repeat(300)))
        )
        assertTrue(outbox.events.isEmpty())
        assertFalse(logs.joinToString().contains(secret))
    }

    private fun collector(state: StoredState, outbox: RecordingOutbox, permission: Boolean) =
        SmsCollector(
            StaticStore(state),
            outbox,
            { permission },
            { Instant.parse("2024-01-01T00:00:00Z") },
            { "event-id" },
            {}
        )
}

private class StaticStore(private var state: StoredState) : AgentStore {
    override suspend fun read(): StoredState = state
    override suspend fun credentials(value: Credentials?) {
        state = state.updated(credentials = value)
    }
    override suspend fun configuration(value: Configuration) {
        state = state.updated(configuration = value)
    }
    override suspend fun lastSuccessfulSync(value: String) {
        state = state.updated(lastSuccessfulSync = value)
    }
    override suspend fun localSmsConsent(value: Boolean) {
        state = state.updated(localSmsConsent = value)
    }
    override suspend fun uploadStatus(successAt: String?, error: String?) {
        state = state.updated(lastSuccessfulUpload = successAt, lastUploadError = error)
    }
}

private fun StoredState.updated(
    credentials: Credentials? = this.credentials,
    configuration: Configuration? = this.configuration,
    lastSuccessfulSync: String? = this.lastSuccessfulSync,
    localSmsConsent: Boolean = this.localSmsConsent,
    lastSuccessfulUpload: String? = this.lastSuccessfulUpload,
    lastUploadError: String? = this.lastUploadError
) = StoredState(
    installationId,
    credentials,
    configuration,
    lastSuccessfulSync,
    localSmsConsent,
    lastSuccessfulUpload,
    lastUploadError
)

private class RecordingOutbox : EventOutbox {
    val events = mutableListOf<GenericEvent>()
    override suspend fun enqueue(event: GenericEvent): EnqueueResult {
        events += event
        return EnqueueResult(true, 0)
    }
    override suspend fun batch(limit: Int): List<OutboxEntry> =
        events.take(limit).map { OutboxEntry(it, 0) }
    override suspend fun remove(eventIds: Set<String>) {
        events.removeAll { it.eventId in eventIds }
    }
    override suspend fun incrementRetries(eventIds: Set<String>) = Unit
    override suspend fun discardAll() = events.clear()
    override suspend fun stats(): OutboxStats = OutboxStats(events.size, 0, 0)
}
