package com.relay.agent

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.relay.agent.application.AgentController
import com.relay.agent.application.SmsCollector
import com.relay.agent.data.ConfigurationCodec
import com.relay.agent.data.CredentialCodec
import com.relay.agent.data.DataStoreAgentStore
import com.relay.agent.data.EncryptedSqliteOutbox
import com.relay.agent.data.HttpRelayApi
import com.relay.agent.data.KeystoreCredentialCipher
import com.relay.agent.domain.CollectionOutcome
import com.relay.agent.domain.EnrollmentState
import com.relay.agent.domain.IncomingSms
import com.relay.agent.domain.SyncOutcome
import java.io.File
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class BackendRoundTripInstrumentedTest {
    @Test
    fun enrollsAndSynchronizesAgainstConfiguredBackend() = runBlocking {
        val arguments = InstrumentationRegistry.getArguments()
        val baseUrl = arguments.getString("relayE2eBaseUrl")
        val challenge = arguments.getString("relayE2eChallenge")
        val expectedDeviceId = arguments.getString("relayE2eDeviceId")
        assumeTrue(baseUrl != null && challenge != null && expectedDeviceId != null)

        val context = ApplicationProvider.getApplicationContext<RelayApplication>()
        val preferences = File(
            context.noBackupFilesDir,
            "e2e-${UUID.randomUUID()}.preferences_pb"
        )
        val store = DataStoreAgentStore(
            PreferenceDataStoreFactory.create { preferences },
            CredentialCodec(KeystoreCredentialCipher("relay.e2e.${UUID.randomUUID()}"))
        )
        val outbox = EncryptedSqliteOutbox(
            context,
            KeystoreCredentialCipher("relay.e2e.outbox.${UUID.randomUUID()}"),
            databaseName = "e2e-${UUID.randomUUID()}.db"
        )
        val controller = AgentController(
            store,
            HttpRelayApi(requireNotNull(baseUrl), BuildConfig.VERSION_NAME, true),
            ConfigurationCodec(),
            outbox,
            smsPermissionGranted = { true }
        )
        try {
            controller.initialize()
            assertEquals(SyncOutcome.SUCCESS, controller.enroll(requireNotNull(challenge)))
            assertEquals(SyncOutcome.SUCCESS, controller.sync())

            val state = store.read()
            assertEquals(expectedDeviceId, state.credentials?.deviceId)
            assertNotNull(state.credentials)
            assertEquals(2, state.configuration?.version)
            assertNotNull(state.lastSuccessfulSync)
            assertEquals(EnrollmentState.ENROLLED, controller.status.value.enrollment)

            store.localSmsConsent(true)
            val collector = SmsCollector(store, outbox, { true }, log = {})
            assertEquals(
                CollectionOutcome.CAPTURED,
                collector.collect(
                    IncomingSms(
                        "E2E-SENDER",
                        "generic Android end-to-end notice",
                        System.currentTimeMillis()
                    )
                )
            )
            assertEquals(1, outbox.stats().queued)
            assertEquals(SyncOutcome.SUCCESS, controller.upload())
            assertEquals(0, outbox.stats().queued)
            assertNotNull(store.read().lastSuccessfulUpload)
        } finally {
            outbox.close()
        }
    }
}
