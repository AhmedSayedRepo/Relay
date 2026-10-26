package com.relay.agent

import android.Manifest
import android.app.Application
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.relay.agent.application.AgentController
import com.relay.agent.application.ImageCollector
import com.relay.agent.application.ImageUploader
import com.relay.agent.application.SmsCollector
import com.relay.agent.data.ConfigurationCodec
import com.relay.agent.data.CredentialCodec
import com.relay.agent.data.DataStoreAgentStore
import com.relay.agent.data.EncryptedBinaryOutbox
import com.relay.agent.data.EncryptedSqliteOutbox
import com.relay.agent.data.EventUploadScheduler
import com.relay.agent.data.HttpRelayApi
import com.relay.agent.data.KeystoreCredentialCipher
import com.relay.agent.data.SafTreeImageSource
import com.relay.agent.data.SyncScheduler
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class RelayApplication : Application() {
    val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val store by lazy {
        val dataStore = PreferenceDataStoreFactory.create(scope = applicationScope) {
            File(noBackupFilesDir, "relay.preferences_pb")
        }
        DataStoreAgentStore(dataStore, CredentialCodec(KeystoreCredentialCipher()))
    }
    val outbox by lazy {
        EncryptedSqliteOutbox(this, KeystoreCredentialCipher("relay.agent.outbox.v1"))
    }
    val binaryOutbox by lazy {
        EncryptedBinaryOutbox(
            this,
            KeystoreCredentialCipher("relay.agent.binary-outbox.v1")
        )
    }
    val imageSource by lazy { SafTreeImageSource(this) }
    val api by lazy {
        HttpRelayApi(BuildConfig.RELAY_BASE_URL, BuildConfig.VERSION_NAME, BuildConfig.DEBUG)
    }
    private fun smsPermissionGranted(): Boolean = ContextCompat.checkSelfPermission(
        this,
        Manifest.permission.RECEIVE_SMS
    ) == PackageManager.PERMISSION_GRANTED
    val controller by lazy {
        AgentController(
            store,
            api,
            ConfigurationCodec(),
            outbox,
            binaryOutbox,
            ::smsPermissionGranted,
            imageSource::accessGranted
        )
    }
    val scheduler by lazy { SyncScheduler(this, store) }
    val uploadScheduler by lazy { EventUploadScheduler(this) }
    val smsCollector by lazy { SmsCollector(store, outbox, ::smsPermissionGranted) }
    val imageCollector by lazy { ImageCollector(store, imageSource, binaryOutbox) }
    val imageUploader by lazy { ImageUploader(store, api, binaryOutbox) }

    override fun onCreate() {
        super.onCreate()
        applicationScope.launch {
            controller.initialize()
            scheduler.schedule()
        }
    }

    companion object {
        fun from(context: Context): RelayApplication =
            context.applicationContext as RelayApplication
    }
}
