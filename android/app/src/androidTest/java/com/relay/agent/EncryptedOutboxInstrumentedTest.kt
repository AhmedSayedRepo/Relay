package com.relay.agent

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.relay.agent.data.CredentialCipher
import com.relay.agent.data.EncryptedSqliteOutbox
import com.relay.agent.data.KeystoreCredentialCipher
import com.relay.agent.domain.GenericEvent
import java.io.File
import java.util.UUID
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class EncryptedOutboxInstrumentedTest {
    @Test
    fun encryptedEventSurvivesDatabaseReopenAndRetryStatePersists() = runTest {
        val context = ApplicationProvider.getApplicationContext<RelayApplication>()
        val name = "test-outbox-${UUID.randomUUID()}.db"
        val alias = "relay.test.outbox.${UUID.randomUUID()}"
        val secret = "SMS secret that must never be stored as plaintext"
        val first = EncryptedSqliteOutbox(
            context,
            KeystoreCredentialCipher(alias),
            databaseName = name
        )
        try {
            assertTrue(first.enqueue(event("event-1", secret)).accepted)
            first.incrementRetries(setOf("event-1"))
        } finally {
            first.close()
        }

        val files = context.noBackupFilesDir.listFiles().orEmpty().filter {
            it.name.startsWith(name)
        }
        assertTrue(files.isNotEmpty())
        files.forEach {
            assertFalse(it.readBytes().toString(Charsets.ISO_8859_1).contains(secret))
        }

        val reopened = EncryptedSqliteOutbox(
            context,
            KeystoreCredentialCipher(alias),
            databaseName = name
        )
        try {
            val restored = reopened.batch(20).single()
            assertEquals(secret, restored.event.body)
            assertEquals(1, restored.retryCount)
            reopened.remove(setOf("event-1"))
            assertEquals(0, reopened.stats().queued)
        } finally {
            reopened.close()
            deleteFiles(context.noBackupFilesDir, name)
        }
    }

    @Test
    fun queueDropsOldestAtCountLimitAndExpiresOfflineRecords() = runTest {
        val context = ApplicationProvider.getApplicationContext<RelayApplication>()
        val name = "test-outbox-${UUID.randomUUID()}.db"
        var now = 1_700_000_000_000L
        val outbox = EncryptedSqliteOutbox(
            context,
            ReversingCipher,
            nowMillis = { now },
            databaseName = name
        )
        try {
            repeat(EncryptedSqliteOutbox.MAX_EVENTS) { index ->
                val id = "event-${index.toString().padStart(3, '0')}"
                assertTrue(outbox.enqueue(event(id, "body")).accepted)
            }
            val overflow = outbox.enqueue(event("event-500", "body"))
            assertTrue(overflow.accepted)
            assertEquals(1, overflow.droppedOldest)
            assertEquals(EncryptedSqliteOutbox.MAX_EVENTS, outbox.stats().queued)
            assertEquals("event-001", outbox.batch(1).single().event.eventId)
            assertFalse(outbox.enqueue(event("event-500", "body")).accepted)
            assertEquals(1, outbox.stats().dropped)

            now += EncryptedSqliteOutbox.MAX_RETENTION_MILLIS + 1
            assertEquals(0, outbox.stats().queued)
            assertEquals(501, outbox.stats().dropped)
        } finally {
            outbox.close()
            deleteFiles(context.noBackupFilesDir, name)
        }
    }

    private fun event(id: String, body: String) = GenericEvent(
        id,
        "00000000-0000-0000-0000-000000000002",
        "sms",
        1,
        3,
        "2024-01-01T00:00:00Z",
        "2024-01-01T00:00:01Z",
        "Sender",
        body,
        "rule"
    )

    private fun deleteFiles(directory: File, prefix: String) {
        directory.listFiles().orEmpty().filter { it.name.startsWith(prefix) }.forEach(File::delete)
    }
}

private object ReversingCipher : CredentialCipher {
    override fun encrypt(plaintext: ByteArray): ByteArray = plaintext.reversedArray()
    override fun decrypt(ciphertext: ByteArray): ByteArray = ciphertext.reversedArray()
}
