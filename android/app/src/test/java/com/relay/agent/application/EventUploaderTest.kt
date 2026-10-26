package com.relay.agent.application

import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.ApiFailure
import com.relay.agent.domain.Configuration
import com.relay.agent.domain.ConfigurationParser
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.EnqueueResult
import com.relay.agent.domain.EventOutbox
import com.relay.agent.domain.GenericEvent
import com.relay.agent.domain.IngestionResult
import com.relay.agent.domain.OutboxEntry
import com.relay.agent.domain.OutboxStats
import com.relay.agent.domain.RelayApi
import com.relay.agent.domain.StoredState
import com.relay.agent.domain.SyncOutcome
import java.io.IOException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EventUploaderTest {
    @Test
    fun acceptedDuplicateAndPermanentRejectionAreAcknowledgedPerEvent() = runTest {
        val outbox = MemoryOutbox((1..3).map(::event))
        val store = UploadStore()
        val api = UploadApi().apply {
            responses += listOf(
                IngestionResult("event-1", "accepted"),
                IngestionResult("event-2", "duplicate"),
                IngestionResult("event-3", "rejected", "unsupported_schema")
            )
        }
        val controller = controller(store, api, outbox)

        assertEquals(SyncOutcome.SUCCESS, controller.upload())
        assertTrue(outbox.entries.isEmpty())
        assertEquals("now", store.state.lastSuccessfulUpload)
        assertEquals("permanent_rejection", store.state.lastUploadError)
    }

    @Test
    fun transportFailureRetainsBatchAndIncrementsRetryState() = runTest {
        val outbox = MemoryOutbox(listOf(event(1), event(2)))
        val store = UploadStore()
        val api = UploadApi().apply { failures += IOException("offline") }
        val controller = controller(store, api, outbox)

        assertEquals(SyncOutcome.RETRYABLE, controller.upload())
        assertEquals(listOf("event-1", "event-2"), outbox.entries.map { it.event.eventId })
        assertEquals(listOf(1, 1), outbox.entries.map { it.retryCount })
        assertEquals("retryable_transport", store.state.lastUploadError)
    }

    @Test
    fun lostResponseResubmitsSameIdsAndDuplicateResultsRemoveThem() = runTest {
        val outbox = MemoryOutbox(listOf(event(1)))
        val store = UploadStore()
        val api = UploadApi().apply {
            failures += IOException("response lost")
            responses += listOf(IngestionResult("event-1", "duplicate"))
        }
        val controller = controller(store, api, outbox)

        assertEquals(SyncOutcome.RETRYABLE, controller.upload())
        assertEquals(SyncOutcome.SUCCESS, controller.upload())
        assertEquals(listOf(listOf("event-1"), listOf("event-1")), api.submittedIds)
        assertTrue(outbox.entries.isEmpty())
    }

    @Test
    fun unauthorizedUploadRenewsOnceWithDurableRequestId() = runTest {
        val outbox = MemoryOutbox(listOf(event(1)))
        val store = UploadStore()
        val api = UploadApi().apply {
            failures += ApiFailure(401)
            responses += listOf(IngestionResult("event-1", "accepted"))
            renewed = credentials(access = "new-access", refresh = "new-refresh")
        }
        val controller = AgentController(
            store,
            api,
            NoConfigurationParser,
            outbox,
            newRequestId = { "stable-renewal" },
            now = { "now" }
        )

        assertEquals(SyncOutcome.SUCCESS, controller.upload())
        assertEquals(listOf("old-access", "new-access"), api.uploadTokens)
        assertEquals(listOf("stable-renewal"), api.renewRequestIds)
        assertEquals("stable-renewal", store.savedCredentials.first().pendingRenewalId)
        assertNull(store.savedCredentials.last().pendingRenewalId)
        assertTrue(outbox.entries.isEmpty())
    }

    @Test
    fun revokedDeviceClearsCredentialsButPreservesEncryptedQueueForUserDiscard() = runTest {
        val outbox = MemoryOutbox(listOf(event(1)))
        val store = UploadStore()
        val api = UploadApi().apply {
            failures += ApiFailure(401)
            renewalFailure = ApiFailure(401)
        }
        val controller = controller(store, api, outbox)

        assertEquals(SyncOutcome.ENROLLMENT_REQUIRED, controller.upload())
        assertNull(store.state.credentials)
        assertEquals(1, outbox.entries.size)
        controller.discardQueuedEvents()
        assertTrue(outbox.entries.isEmpty())
        assertEquals(1, outbox.dropped)
    }

    @Test
    fun batchIsBoundedAndInvalidServerResultIsTerminallyDiscarded() = runTest {
        val outbox = MemoryOutbox((1..25).map(::event))
        val store = UploadStore()
        val api = UploadApi().apply {
            responses += (1..20).map { IngestionResult("event-$it", "accepted") }
            responses += listOf(IngestionResult("wrong-id", "accepted"))
        }
        val controller = controller(store, api, outbox)

        assertEquals(SyncOutcome.SUCCESS, controller.upload())
        assertEquals(20, api.submittedIds.first().size)
        assertEquals(5, outbox.entries.size)
        assertEquals(SyncOutcome.REJECTED, controller.upload())
        assertTrue(outbox.entries.isEmpty())
        assertEquals("terminal_delivery", store.state.lastUploadError)
    }

    private fun controller(store: UploadStore, api: UploadApi, outbox: MemoryOutbox) =
        AgentController(
            store,
            api,
            NoConfigurationParser,
            outbox,
            now = { "now" },
            newRequestId = { "stable-renewal" }
        )

    private fun event(number: Int) = GenericEvent(
        "event-$number",
        "installation",
        "sms",
        1,
        3,
        "2024-01-01T00:00:00Z",
        "2024-01-01T00:00:01Z",
        "Sender",
        "Body $number",
        "rule"
    )
}

private fun credentials(access: String = "old-access", refresh: String = "old-refresh") =
    Credentials(
        "device",
        "installation",
        "Managed",
        access,
        refresh,
        "2099-01-01T00:00:00Z"
    )

private class UploadStore : AgentStore {
    var state = StoredState("installation", credentials())
    val savedCredentials = mutableListOf<Credentials>()

    override suspend fun read(): StoredState = state
    override suspend fun credentials(value: Credentials?) {
        value?.let(savedCredentials::add)
        state = copy(credentials = value)
    }
    override suspend fun configuration(value: Configuration) {
        state = copy(configuration = value)
    }
    override suspend fun lastSuccessfulSync(value: String) {
        state = copy(lastSuccessfulSync = value)
    }
    override suspend fun localSmsConsent(value: Boolean) {
        state = copy(localSmsConsent = value)
    }
    override suspend fun uploadStatus(successAt: String?, error: String?) {
        state = copy(
            lastSuccessfulUpload = successAt ?: state.lastSuccessfulUpload,
            lastUploadError = error
        )
    }

    private fun copy(
        credentials: Credentials? = state.credentials,
        configuration: Configuration? = state.configuration,
        lastSuccessfulSync: String? = state.lastSuccessfulSync,
        localSmsConsent: Boolean = state.localSmsConsent,
        lastSuccessfulUpload: String? = state.lastSuccessfulUpload,
        lastUploadError: String? = state.lastUploadError
    ) = StoredState(
        state.installationId,
        credentials,
        configuration,
        lastSuccessfulSync,
        localSmsConsent,
        lastSuccessfulUpload,
        lastUploadError
    )
}

private class UploadApi : RelayApi {
    val failures = ArrayDeque<Exception>()
    val responses = ArrayDeque<List<IngestionResult>>()
    val submittedIds = mutableListOf<List<String>>()
    val uploadTokens = mutableListOf<String>()
    val renewRequestIds = mutableListOf<String>()
    var renewed = credentials(access = "renewed")
    var renewalFailure: Exception? = null

    override suspend fun uploadEvents(
        accessToken: String,
        events: List<GenericEvent>
    ): List<IngestionResult> {
        uploadTokens += accessToken
        submittedIds += events.map { it.eventId }
        failures.removeFirstOrNull()?.let { throw it }
        return responses.removeFirst()
    }

    override suspend fun renew(credentials: Credentials, requestId: String): Credentials {
        renewRequestIds += requestId
        renewalFailure?.let { throw it }
        return renewed
    }

    override suspend fun enroll(challenge: String, installationId: String): Credentials =
        error("not used")
    override suspend fun configuration(accessToken: String, knownVersion: Int): String? =
        error("not used")
    override suspend fun acknowledge(accessToken: String, version: Int) = error("not used")
    override suspend fun heartbeat(accessToken: String, version: Int) = error("not used")
}

private class MemoryOutbox(events: List<GenericEvent>) : EventOutbox {
    val entries = events.map { OutboxEntry(it, 0) }.toMutableList()
    var dropped = 0L

    override suspend fun enqueue(event: GenericEvent): EnqueueResult {
        entries += OutboxEntry(event, 0)
        return EnqueueResult(true, 0)
    }
    override suspend fun batch(limit: Int): List<OutboxEntry> = entries.take(limit)
    override suspend fun remove(eventIds: Set<String>) {
        entries.removeAll { it.event.eventId in eventIds }
    }
    override suspend fun incrementRetries(eventIds: Set<String>) {
        val replacements = entries.map {
            if (it.event.eventId in eventIds) it.copy(retryCount = it.retryCount + 1) else it
        }
        entries.clear()
        entries += replacements
    }
    override suspend fun discardAll() {
        dropped += entries.size
        entries.clear()
    }
    override suspend fun stats(): OutboxStats = OutboxStats(entries.size, 0, dropped)
}

private object NoConfigurationParser : ConfigurationParser {
    override fun parse(json: String, current: Configuration?): Configuration = error("not used")
}
