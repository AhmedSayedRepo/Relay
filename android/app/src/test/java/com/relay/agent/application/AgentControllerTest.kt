package com.relay.agent.application

import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.ApiFailure
import com.relay.agent.domain.BinaryEnqueueResult
import com.relay.agent.domain.BinaryOutbox
import com.relay.agent.domain.BinaryOutboxStats
import com.relay.agent.domain.Configuration
import com.relay.agent.domain.ConfigurationParser
import com.relay.agent.domain.ConnectionState
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.EnrollmentState
import com.relay.agent.domain.GenericEvent
import com.relay.agent.domain.IngestionResult
import com.relay.agent.domain.InvalidConfiguration
import com.relay.agent.domain.QueuedImage
import com.relay.agent.domain.RelayApi
import com.relay.agent.domain.StoredState
import com.relay.agent.domain.SyncOutcome
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test
import java.io.IOException

class AgentControllerTest {
    @Test
    fun enrollmentPersistsOnlyMatchingInstallation() =
        runTest {
            val store = FakeStore()
            val api = FakeApi().apply { enrollment = credentials(store.installationId) }
            val controller = AgentController(store, api, PassParser)
            controller.initialize()
            assertEquals(EnrollmentState.REQUIRED, controller.status.value.enrollment)
            assertEquals(SyncOutcome.SUCCESS, controller.enroll("challenge"))
            assertEquals(EnrollmentState.ENROLLED, controller.status.value.enrollment)
            assertSame(api.enrollment, store.savedCredentials.last())
        }

    @Test
    fun ambiguousEnrollmentIsNeverRetriedAutomatically() =
        runTest {
            val store = FakeStore()
            val api = FakeApi().apply { enrollmentFailure = IOException("lost response") }
            val controller = AgentController(store, api, PassParser)
            assertEquals(SyncOutcome.REJECTED, controller.enroll("challenge"))
            assertEquals(1, api.enrollmentCalls)
            assertEquals(ConnectionState.OFFLINE, controller.status.value.connection)
        }

    @Test
    fun syncStoresThenAcknowledgesLastGoodConfiguration() =
        runTest {
            val initial = credentials("installation")
            val store = FakeStore(installationId = "installation", initialCredentials = initial)
            val api =
                FakeApi().apply {
                    configurationDocument = """{"schema_version":1,"version":2,"settings":{}}"""
                }
            val controller = AgentController(store, api, PassParser, now = { "now" })
            assertEquals(SyncOutcome.SUCCESS, controller.sync())
            assertEquals(listOf(2), api.acknowledgements)
            assertEquals(listOf(2), api.heartbeats)
            assertEquals(2, store.state.configuration?.version)
            assertEquals("now", store.state.lastSuccessfulSync)
        }

    @Test
    fun unauthorizedSyncRotatesOnceWithDurableStableRequestId() =
        runTest {
            val initial = credentials("installation")
            val store = FakeStore(installationId = "installation", initialCredentials = initial)
            val api =
                FakeApi().apply {
                    configurationFailures.add(ApiFailure(401))
                    renewed = credentials("installation", access = "new-access", refresh = "new-refresh")
                }
            val controller =
                AgentController(store, api, PassParser, newRequestId = { "renew-operation" })
            assertEquals(SyncOutcome.SUCCESS, controller.sync())
            assertEquals("renew-operation", api.renewRequestIds.single())
            assertEquals("renew-operation", store.savedCredentials.first().pendingRenewalId)
            assertNull(store.savedCredentials.last().pendingRenewalId)
            assertEquals(listOf("old-access", "new-access"), api.configurationTokens)
        }

    @Test
    fun failedRenewalKeepsPendingOperationForNextRun() =
        runTest {
            val store =
                FakeStore(
                    installationId = "installation",
                    initialCredentials = credentials("installation", pending = "stable-operation"),
                )
            val api = FakeApi().apply { renewalFailure = IOException("offline") }
            val controller =
                AgentController(store, api, PassParser, newRequestId = { "must-not-be-used" })
            assertEquals(SyncOutcome.RETRYABLE, controller.sync())
            assertEquals(listOf("stable-operation"), api.renewRequestIds)
            assertEquals("stable-operation", store.state.credentials?.pendingRenewalId)
        }

    @Test
    fun rejectedConfigurationPreservesLastGoodState() =
        runTest {
            val good = Configuration(1, "good")
            val store = FakeStore("installation", credentials("installation"), good)
            val api = FakeApi().apply { configurationDocument = "bad" }
            val controller = AgentController(store, api, RejectingParser)
            assertEquals(SyncOutcome.REJECTED, controller.sync())
            assertSame(good, store.state.configuration)
            assertEquals(emptyList<Int>(), api.acknowledgements)
        }

    @Test
    fun terminalUnauthorizedClearsCredentialsButKeepsConfiguration() =
        runTest {
            val good = Configuration(1, "good")
            val store = FakeStore("installation", credentials("installation"), good)
            val api =
                FakeApi().apply {
                    configurationFailures.add(ApiFailure(401))
                    renewalFailure = ApiFailure(401)
                }
            val controller = AgentController(store, api, PassParser)
            assertEquals(SyncOutcome.ENROLLMENT_REQUIRED, controller.sync())
            assertNull(store.state.credentials)
            assertSame(good, store.state.configuration)
        }

    @Test
    fun imageConsentWithdrawalAndSourceRemovalClearCollectionObservations() =
        runTest {
            val observations = PrivacyBoundaryBinaryOutbox()
            val controller =
                AgentController(
                    FakeStore(),
                    FakeApi(),
                    PassParser,
                    binaryOutbox = observations,
                )

            controller.setLocalImageConsent(false)
            controller.clearImageSource()

            assertEquals(2, observations.discards)
            assertEquals(2, observations.observationClears)
        }
}

private fun credentials(
    installation: String,
    access: String = "old-access",
    refresh: String = "old-refresh",
    pending: String? = null,
) = Credentials("device", installation, "Managed", access, refresh, "2099-01-01T00:00:00Z", pending)

private class FakeStore(
    val installationId: String = "installation",
    initialCredentials: Credentials? = null,
    initialConfiguration: Configuration? = null,
) : AgentStore {
    var state = StoredState(installationId, initialCredentials, initialConfiguration)
    val savedCredentials = mutableListOf<Credentials>()

    override suspend fun read(): StoredState = state

    override suspend fun credentials(value: Credentials?) {
        value?.let(savedCredentials::add)
        state = StoredState(installationId, value, state.configuration, state.lastSuccessfulSync)
    }

    override suspend fun configuration(value: Configuration) {
        state = StoredState(installationId, state.credentials, value, state.lastSuccessfulSync)
    }

    override suspend fun lastSuccessfulSync(value: String) {
        state = StoredState(installationId, state.credentials, state.configuration, value)
    }

    override suspend fun localSmsConsent(value: Boolean) {
        state =
            StoredState(
                installationId,
                state.credentials,
                state.configuration,
                state.lastSuccessfulSync,
                value,
                state.lastSuccessfulUpload,
                state.lastUploadError,
            )
    }

    override suspend fun uploadStatus(
        successAt: String?,
        error: String?,
    ) {
        state =
            StoredState(
                installationId,
                state.credentials,
                state.configuration,
                state.lastSuccessfulSync,
                state.localSmsConsent,
                successAt ?: state.lastSuccessfulUpload,
                error,
            )
    }
}

private class FakeApi : RelayApi {
    lateinit var enrollment: Credentials
    lateinit var renewed: Credentials
    var enrollmentFailure: Exception? = null
    var renewalFailure: Exception? = null
    var configurationDocument: String? = null
    val configurationFailures = ArrayDeque<Exception>()
    var enrollmentCalls = 0
    val renewRequestIds = mutableListOf<String>()
    val configurationTokens = mutableListOf<String>()
    val acknowledgements = mutableListOf<Int>()
    val heartbeats = mutableListOf<Int>()

    override suspend fun enroll(
        challenge: String,
        installationId: String,
    ): Credentials {
        enrollmentCalls += 1
        enrollmentFailure?.let { throw it }
        return enrollment
    }

    override suspend fun renew(
        credentials: Credentials,
        requestId: String,
    ): Credentials {
        renewRequestIds += requestId
        renewalFailure?.let { throw it }
        return renewed
    }

    override suspend fun configuration(
        accessToken: String,
        knownVersion: Int,
    ): String? {
        configurationTokens += accessToken
        configurationFailures.removeFirstOrNull()?.let { throw it }
        return configurationDocument
    }

    override suspend fun acknowledge(
        accessToken: String,
        version: Int,
    ) {
        acknowledgements += version
    }

    override suspend fun heartbeat(
        accessToken: String,
        version: Int,
    ) {
        heartbeats += version
    }

    override suspend fun uploadEvents(
        accessToken: String,
        events: List<GenericEvent>,
    ): List<IngestionResult> = events.map { IngestionResult(it.eventId, "accepted", null) }
}

private object PassParser : ConfigurationParser {
    override fun parse(
        json: String,
        current: Configuration?,
    ): Configuration = Configuration(2, json)
}

private object RejectingParser : ConfigurationParser {
    override fun parse(
        json: String,
        current: Configuration?,
    ): Configuration = throw InvalidConfiguration()
}

private class PrivacyBoundaryBinaryOutbox : BinaryOutbox {
    var discards = 0
    var observationClears = 0

    override suspend fun enqueue(value: QueuedImage) = BinaryEnqueueResult(false, false, 0)

    override suspend fun next(): QueuedImage? = null

    override suspend fun progress(
        uploadId: String,
        sessionId: String,
        uploadedBytes: Int,
    ) = Unit

    override suspend fun remove(uploadId: String) = Unit

    override suspend fun discardAll() {
        discards += 1
    }

    override suspend fun clearObservations() {
        observationClears += 1
    }

    override suspend fun stats() = BinaryOutboxStats(0, 0, 0)
}
