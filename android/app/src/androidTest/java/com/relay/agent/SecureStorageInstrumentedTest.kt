package com.relay.agent

import android.Manifest
import android.content.pm.ApplicationInfo
import android.security.NetworkSecurityPolicy
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.relay.agent.data.CredentialCodec
import com.relay.agent.data.DataStoreAgentStore
import com.relay.agent.data.KeystoreCredentialCipher
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.SecureStorageFailure
import java.io.File
import java.util.UUID
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SecureStorageInstrumentedTest {
    @Test
    fun keystoreCipherUsesRandomizedAuthenticatedEncryption() {
        val cipher = KeystoreCredentialCipher("relay.test.${UUID.randomUUID()}")
        val plaintext = "device-secret".toByteArray()
        val first = cipher.encrypt(plaintext)
        val second = cipher.encrypt(plaintext)
        assertNotEquals(first.toList(), second.toList())
        assertArrayEquals(plaintext, cipher.decrypt(first))
        first[first.lastIndex] = (first.last() + 1).toByte()
        assertThrows(SecureStorageFailure::class.java) { cipher.decrypt(first) }
    }

    @Test
    fun installationAndEncryptedCredentialsPersistInNoBackupStorage() = runTest {
        val context = ApplicationProvider.getApplicationContext<RelayApplication>()
        val file = File(context.noBackupFilesDir, "test-${UUID.randomUUID()}.preferences_pb")
        assertTrue(file.canonicalPath.startsWith(context.noBackupFilesDir.canonicalPath))
        val dataStore = PreferenceDataStoreFactory.create { file }
        val store = DataStoreAgentStore(
            dataStore,
            CredentialCodec(KeystoreCredentialCipher("relay.test.${UUID.randomUUID()}"))
        )
        val installation = store.read().installationId
        val credentials = Credentials(
            UUID.randomUUID().toString(),
            installation,
            "Managed",
            "access-secret",
            "refresh-secret",
            "2099-01-01T00:00:00Z"
        )
        store.credentials(credentials)
        store.localSmsConsent(true)
        store.uploadStatus("2024-01-01T00:00:00Z", null)
        store.uploadStatus(null, "retryable_transport")
        val restored = store.read()
        assertEquals(installation, restored.installationId)
        assertEquals("refresh-secret", restored.credentials?.refreshToken)
        assertTrue(restored.localSmsConsent)
        assertEquals("2024-01-01T00:00:00Z", restored.lastSuccessfulUpload)
        assertEquals("retryable_transport", restored.lastUploadError)
        assertFalse(file.readText().contains("refresh-secret"))
    }

    @Test
    fun packageRequestsOnlyNarrowSmsPersonalContentPermissionAndDisablesBackup() {
        val context = ApplicationProvider.getApplicationContext<RelayApplication>()
        val packageInfo = context.packageManager.getPackageInfo(context.packageName, 4096)
        val requested = packageInfo.requestedPermissions?.toSet().orEmpty()
        val forbidden = setOf(
            Manifest.permission.READ_SMS,
            Manifest.permission.READ_CONTACTS,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.READ_PHONE_STATE,
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO
        )
        assertTrue(requested.intersect(forbidden).isEmpty())
        assertTrue(Manifest.permission.RECEIVE_SMS in requested)
        assertTrue(Manifest.permission.INTERNET in requested)
        assertFalse((context.applicationInfo.flags and ApplicationInfo.FLAG_ALLOW_BACKUP) != 0)
        assertFalse(NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted("example.com"))
    }
}
