package com.relay.agent.data

import android.util.Base64
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.ApprovedImageSource
import com.relay.agent.domain.Configuration
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.SecureStorageFailure
import com.relay.agent.domain.StoredState
import java.util.UUID
import kotlinx.coroutines.flow.first

/** One Preferences DataStore is provided application-wide and its entire file is in noBackupFilesDir. */
class DataStoreAgentStore(
    private val dataStore: DataStore<Preferences>,
    private val codec: CredentialCodec,
    private val configurationCodec: ConfigurationCodec = ConfigurationCodec(),
    private val sourceCipher: CredentialCipher = KeystoreCredentialCipher("relay.agent.source.v1")
) : AgentStore {
    override suspend fun read(): StoredState {
        dataStore.edit {
            if (it[INSTALLATION] ==
                null
            ) {
                it[INSTALLATION] = UUID.randomUUID().toString()
            }
        }
        val preferences = dataStore.data.first()
        val installationId = requireNotNull(preferences[INSTALLATION])
        val configuration = preferences[CONFIGURATION]?.let {
            try {
                configurationCodec.parse(it, null)
            } catch (_: Exception) {
                null
            }
        }
        val credentials = preferences[CREDENTIALS]?.let {
            try {
                codec.decode(it).also { value -> require(value.installationId == installationId) }
            } catch (_: SecureStorageFailure) {
                dataStore.edit { values -> values.remove(CREDENTIALS) }
                null
            } catch (_: IllegalArgumentException) {
                dataStore.edit { values -> values.remove(CREDENTIALS) }
                null
            }
        }
        val approvedSource = preferences[IMAGE_SOURCE]?.let {
            try {
                val plaintext = sourceCipher.decrypt(Base64.decode(it, Base64.NO_WRAP))
                    .toString(Charsets.UTF_8)
                val parts = plaintext.split('\n', limit = 3)
                require(parts.size == 3)
                ApprovedImageSource(parts[0], parts[2], parts[1].toLong())
            } catch (_: Exception) {
                dataStore.edit { values -> values.remove(IMAGE_SOURCE) }
                null
            }
        }
        return StoredState(
            installationId,
            credentials,
            configuration,
            preferences[LAST_SYNC],
            preferences[SMS_CONSENT] ?: false,
            preferences[LAST_UPLOAD],
            preferences[LAST_UPLOAD_ERROR],
            preferences[IMAGE_CONSENT] ?: false,
            approvedSource,
            preferences[LAST_IMAGE_UPLOAD],
            preferences[LAST_IMAGE_UPLOAD_ERROR]
        )
    }

    override suspend fun credentials(value: Credentials?) {
        val encrypted = value?.let(codec::encode)
        dataStore.edit {
            if (encrypted == null) it.remove(CREDENTIALS) else it[CREDENTIALS] = encrypted
        }
    }

    override suspend fun configuration(value: Configuration) {
        dataStore.edit { it[CONFIGURATION] = value.json }
    }

    override suspend fun lastSuccessfulSync(value: String) {
        dataStore.edit { it[LAST_SYNC] = value }
    }

    override suspend fun localSmsConsent(value: Boolean) {
        dataStore.edit { it[SMS_CONSENT] = value }
    }

    override suspend fun uploadStatus(successAt: String?, error: String?) {
        dataStore.edit {
            if (successAt != null) it[LAST_UPLOAD] = successAt
            if (error == null) it.remove(LAST_UPLOAD_ERROR) else it[LAST_UPLOAD_ERROR] = error
        }
    }

    override suspend fun localImageConsent(value: Boolean) {
        dataStore.edit { it[IMAGE_CONSENT] = value }
    }

    override suspend fun approvedImageSource(value: ApprovedImageSource?) {
        val encoded = value?.let {
            val plaintext = "${it.sourceId}\n${it.approvedAtMillis}\n${it.treeUri}"
            Base64.encodeToString(
                sourceCipher.encrypt(plaintext.toByteArray(Charsets.UTF_8)),
                Base64.NO_WRAP
            )
        }
        dataStore.edit {
            if (encoded == null) it.remove(IMAGE_SOURCE) else it[IMAGE_SOURCE] = encoded
        }
    }

    override suspend fun imageUploadStatus(successAt: String?, error: String?) {
        dataStore.edit {
            if (successAt != null) it[LAST_IMAGE_UPLOAD] = successAt
            if (error == null) {
                it.remove(LAST_IMAGE_UPLOAD_ERROR)
            } else {
                it[LAST_IMAGE_UPLOAD_ERROR] = error
            }
        }
    }

    companion object {
        private val INSTALLATION = stringPreferencesKey("installation_id")
        private val CREDENTIALS = stringPreferencesKey("encrypted_credentials")
        private val CONFIGURATION = stringPreferencesKey("last_valid_configuration")
        private val LAST_SYNC = stringPreferencesKey("last_successful_sync")
        private val SMS_CONSENT = booleanPreferencesKey("local_sms_consent")
        private val LAST_UPLOAD = stringPreferencesKey("last_successful_upload")
        private val LAST_UPLOAD_ERROR = stringPreferencesKey("last_upload_error")
        private val IMAGE_CONSENT = booleanPreferencesKey("local_image_consent")
        private val IMAGE_SOURCE = stringPreferencesKey("encrypted_image_source")
        private val LAST_IMAGE_UPLOAD = stringPreferencesKey("last_successful_image_upload")
        private val LAST_IMAGE_UPLOAD_ERROR = stringPreferencesKey("last_image_upload_error")
    }
}
