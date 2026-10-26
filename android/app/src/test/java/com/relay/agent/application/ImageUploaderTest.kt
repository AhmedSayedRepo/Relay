package com.relay.agent.application

import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.ApiFailure
import com.relay.agent.domain.BinaryEnqueueResult
import com.relay.agent.domain.BinaryOutbox
import com.relay.agent.domain.BinaryOutboxStats
import com.relay.agent.domain.Configuration
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.GenericEvent
import com.relay.agent.domain.IngestionResult
import com.relay.agent.domain.QueuedImage
import com.relay.agent.domain.RelayApi
import com.relay.agent.domain.StoredState
import com.relay.agent.domain.SyncOutcome
import com.relay.agent.domain.UploadInitialization
import java.io.IOException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ImageUploaderTest {
    @Test
    fun lostInitializationResponseIsRetriedAndServerOffsetResumesUpload() = runTest {
        val store = ImageUploadStore()
        val outbox = ImageUploadOutbox(image())
        val api =
            ImageUploadApi().apply {
                initializeFailures += IOException("response lost")
                initializeResponses +=
                    UploadInitialization("session", "upload_required", 2, null)
            }
        val uploader = ImageUploader(store, api, outbox, now = { "now" }, log = {})

        assertEquals(SyncOutcome.RETRYABLE, uploader.upload())
        assertTrue(outbox.value != null)
        assertEquals(SyncOutcome.SUCCESS, uploader.upload())
        assertNull(outbox.value)
        assertEquals(1, outbox.completed)
        assertEquals(listOf(2), api.chunkOffsets)
        assertEquals(listOf(3), api.chunkSizes)
        assertEquals(listOf(2, 5), outbox.uploadedProgress)
        assertEquals("now", store.lastSuccess)
    }

    @Test
    fun unauthorizedInitializationRenewsOnceAndFinalizedDedupeClearsQueue() = runTest {
        val store = ImageUploadStore()
        val outbox = ImageUploadOutbox(image())
        val api =
            ImageUploadApi().apply {
                initializeFailures += ApiFailure(401)
                initializeResponses += UploadInitialization("session", "finalized", 5, "object")
            }
        val uploader =
            ImageUploader(
                store,
                api,
                outbox,
                now = { "now" },
                newRequestId = { "renewal-id" },
                log = {}
            )

        assertEquals(SyncOutcome.SUCCESS, uploader.upload())
        assertEquals(listOf("old-access", "new-access"), api.initializeTokens)
        assertEquals(listOf("renewal-id"), api.renewalIds)
        assertEquals("renewal-id", store.savedCredentials.first().pendingRenewalId)
        assertNull(store.savedCredentials.last().pendingRenewalId)
        assertNull(outbox.value)
        assertEquals(1, outbox.completed)
    }

    private fun image() = QueuedImage(
        "upload",
        "event",
        "installation",
        "hash",
        "image/png",
        5,
        2,
        2,
        "saf_tree",
        "folder",
        "image.png",
        2,
        "rule",
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:01Z",
        null,
        0,
        null,
        byteArrayOf(1, 2, 3, 4, 5)
    )
}

private fun imageCredentials(access: String = "old-access") = Credentials(
    "device",
    "installation",
    "Managed",
    access,
    "refresh",
    "2099-01-01T00:00:00Z"
)

private class ImageUploadStore : AgentStore {
    private var currentCredentials: Credentials? = imageCredentials()
    val savedCredentials = mutableListOf<Credentials>()
    var lastSuccess: String? = null
    var lastError: String? = null

    override suspend fun read() = StoredState("installation", currentCredentials)

    override suspend fun credentials(value: Credentials?) {
        currentCredentials = value
        value?.let(savedCredentials::add)
    }

    override suspend fun configuration(value: Configuration) = Unit

    override suspend fun lastSuccessfulSync(value: String) = Unit

    override suspend fun localSmsConsent(value: Boolean) = Unit

    override suspend fun uploadStatus(successAt: String?, error: String?) = Unit

    override suspend fun imageUploadStatus(successAt: String?, error: String?) {
        lastSuccess = successAt ?: lastSuccess
        lastError = error
    }
}

private class ImageUploadOutbox(var value: QueuedImage?) : BinaryOutbox {
    val uploadedProgress = mutableListOf<Int>()
    var completed = 0

    override suspend fun enqueue(value: QueuedImage) = BinaryEnqueueResult(false, true, 0)

    override suspend fun next() = value

    override suspend fun progress(uploadId: String, sessionId: String, uploadedBytes: Int) {
        uploadedProgress += uploadedBytes
    }

    override suspend fun remove(uploadId: String) {
        value = null
    }

    override suspend fun complete(uploadId: String) {
        completed += 1
        value = null
    }

    override suspend fun discardAll() {
        value = null
    }

    override suspend fun stats() = BinaryOutboxStats(if (value == null) 0 else 1, 0, 0)
}

private class ImageUploadApi : RelayApi {
    val initializeFailures = ArrayDeque<Exception>()
    val initializeResponses = ArrayDeque<UploadInitialization>()
    val initializeTokens = mutableListOf<String>()
    val chunkOffsets = mutableListOf<Int>()
    val chunkSizes = mutableListOf<Int>()
    val renewalIds = mutableListOf<String>()

    override suspend fun initializeImage(
        accessToken: String,
        image: QueuedImage
    ): UploadInitialization {
        initializeTokens += accessToken
        initializeFailures.removeFirstOrNull()?.let { throw it }
        return initializeResponses.removeFirst()
    }

    override suspend fun uploadImageChunk(
        accessToken: String,
        sessionId: String,
        offset: Int,
        bytes: ByteArray
    ): Int {
        chunkOffsets += offset
        chunkSizes += bytes.size
        return offset + bytes.size
    }

    override suspend fun finalizeImage(accessToken: String, sessionId: String) = "object"

    override suspend fun renew(credentials: Credentials, requestId: String): Credentials {
        renewalIds += requestId
        return imageCredentials("new-access")
    }

    override suspend fun enroll(challenge: String, installationId: String): Credentials =
        error("not used")

    override suspend fun configuration(accessToken: String, knownVersion: Int): String? =
        error("not used")

    override suspend fun acknowledge(accessToken: String, version: Int) = error("not used")

    override suspend fun heartbeat(accessToken: String, version: Int) = error("not used")

    override suspend fun uploadEvents(
        accessToken: String,
        events: List<GenericEvent>
    ): List<IngestionResult> = error("not used")
}
