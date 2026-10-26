package com.relay.agent

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.relay.agent.data.CredentialCipher
import com.relay.agent.data.EncryptedBinaryOutbox
import com.relay.agent.data.KeystoreCredentialCipher
import com.relay.agent.domain.QueuedImage
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class EncryptedBinaryOutboxInstrumentedTest {
    @Test
    fun imageBytesAreEncryptedAndResumableStateSurvivesReopen() =
        runTest {
            val context = ApplicationProvider.getApplicationContext<RelayApplication>()
            val name = "test-binary-${UUID.randomUUID()}.db"
            val alias = "relay.test.binary.${UUID.randomUUID()}"
            val secret = "private-image-binary-marker"
            val marker = secret.toByteArray()
            val first =
                EncryptedBinaryOutbox(
                    context,
                    KeystoreCredentialCipher(alias),
                    databaseName = name,
                )
            try {
                assertTrue(first.enqueue(image(marker)).accepted)
                first.progress("upload", "session", 3)
            } finally {
                first.close()
            }
            context.noBackupFilesDir
                .listFiles()
                .orEmpty()
                .filter { it.name.startsWith(name) }
                .forEach {
                    assertFalse(it.readBytes().toString(Charsets.ISO_8859_1).contains(secret))
                }
            val reopened =
                EncryptedBinaryOutbox(
                    context,
                    KeystoreCredentialCipher(alias),
                    databaseName = name,
                )
            try {
                val restored = requireNotNull(reopened.next())
                assertArrayEquals(marker, restored.bytes)
                assertEquals("session", restored.sessionId)
                assertEquals(3, restored.uploadedBytes)
            } finally {
                reopened.close()
                context.noBackupFilesDir
                    .listFiles()
                    .orEmpty()
                    .filter { it.name.startsWith(name) }
                    .forEach(File::delete)
            }
        }

    @Test
    fun completedObservationSurvivesReopenAndIsScopedToInstallationAndSource() =
        runTest {
            val context = ApplicationProvider.getApplicationContext<RelayApplication>()
            val name = "test-binary-${UUID.randomUUID()}.db"
            val hash = "1".repeat(64)
            val first =
                EncryptedBinaryOutbox(
                    context,
                    BinaryReversingCipher,
                    databaseName = name,
                )
            try {
                assertTrue(first.enqueue(image(byteArrayOf(1), contentHash = hash)).accepted)
                first.complete("upload")
            } finally {
                first.close()
            }

            val reopened =
                EncryptedBinaryOutbox(
                    context,
                    BinaryReversingCipher,
                    databaseName = name,
                )
            try {
                val repeated =
                    reopened.enqueue(
                        image(byteArrayOf(1), uploadId = "repeat", contentHash = hash),
                    )
                assertFalse(repeated.accepted)
                assertTrue(repeated.duplicate)

                assertTrue(
                    reopened
                        .enqueue(
                            image(
                                byteArrayOf(1),
                                uploadId = "new-installation",
                                installationId = "installation-2",
                                contentHash = hash,
                            ),
                        ).accepted,
                )
                reopened.remove("new-installation")
                assertTrue(
                    reopened
                        .enqueue(
                            image(
                                byteArrayOf(1),
                                uploadId = "new-source",
                                sourceId = "folder-2",
                                contentHash = hash,
                            ),
                        ).accepted,
                )
            } finally {
                reopened.close()
                deleteFiles(context.noBackupFilesDir, name)
            }
        }

    @Test
    fun completedObservationsAreBoundedExpireAndCanBeCleared() =
        runTest {
            val context = ApplicationProvider.getApplicationContext<RelayApplication>()
            val name = "test-binary-${UUID.randomUUID()}.db"
            var now = 1_700_000_000_000L
            val outbox =
                EncryptedBinaryOutbox(
                    context,
                    BinaryReversingCipher,
                    nowMillis = { now },
                    databaseName = name,
                )
            try {
                repeat(EncryptedBinaryOutbox.MAX_OBSERVATIONS + 1) { index ->
                    val uploadId = "upload-$index"
                    val hash = index.toString(16).padStart(64, '0')
                    assertTrue(
                        outbox
                            .enqueue(
                                image(byteArrayOf(1), uploadId = uploadId, contentHash = hash),
                            ).accepted,
                    )
                    outbox.complete(uploadId)
                    now += 1
                }
                val oldestHash = "0".padStart(64, '0')
                assertTrue(
                    outbox
                        .enqueue(
                            image(byteArrayOf(1), uploadId = "oldest-again", contentHash = oldestHash),
                        ).accepted,
                )
                outbox.remove("oldest-again")

                val newestHash =
                    EncryptedBinaryOutbox.MAX_OBSERVATIONS
                        .toString(16)
                        .padStart(64, '0')
                assertTrue(
                    outbox
                        .enqueue(
                            image(byteArrayOf(1), uploadId = "newest-again", contentHash = newestHash),
                        ).duplicate,
                )

                now += EncryptedBinaryOutbox.MAX_OBSERVATION_RETENTION_MILLIS + 1
                assertTrue(
                    outbox
                        .enqueue(
                            image(byteArrayOf(1), uploadId = "after-expiry", contentHash = newestHash),
                        ).accepted,
                )
                outbox.remove("after-expiry")
                outbox.clearObservations()
                assertTrue(
                    outbox
                        .enqueue(
                            image(byteArrayOf(1), uploadId = "after-clear", contentHash = newestHash),
                        ).accepted,
                )
            } finally {
                outbox.close()
                deleteFiles(context.noBackupFilesDir, name)
            }
        }

    private fun image(
        bytes: ByteArray,
        uploadId: String = "upload",
        installationId: String = "installation",
        sourceId: String = "folder",
        contentHash: String = "0".repeat(64),
    ) = QueuedImage(
        uploadId,
        "event",
        installationId,
        contentHash,
        "image/png",
        bytes.size,
        2,
        2,
        "saf_tree",
        sourceId,
        "candidate.png",
        2,
        "rule",
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:01Z",
        null,
        0,
        null,
        bytes,
    )

    private fun deleteFiles(
        directory: File,
        prefix: String,
    ) {
        directory
            .listFiles()
            .orEmpty()
            .filter { it.name.startsWith(prefix) }
            .forEach(File::delete)
    }
}

private object BinaryReversingCipher : CredentialCipher {
    override fun encrypt(plaintext: ByteArray): ByteArray = plaintext.reversedArray()

    override fun decrypt(ciphertext: ByteArray): ByteArray = ciphertext.reversedArray()
}
