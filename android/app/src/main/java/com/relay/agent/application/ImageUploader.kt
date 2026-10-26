package com.relay.agent.application

import android.util.Log
import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.ApiFailure
import com.relay.agent.domain.BinaryOutbox
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.RelayApi
import com.relay.agent.domain.SyncOutcome
import kotlinx.coroutines.CancellationException
import java.time.Instant
import java.util.UUID

class ImageUploader(
    private val store: AgentStore,
    private val api: RelayApi,
    private val outbox: BinaryOutbox,
    private val now: () -> String = { Instant.now().toString() },
    private val newRequestId: () -> String = { UUID.randomUUID().toString() },
    private val log: (String) -> Unit = { Log.i(TAG, it) },
) {
    suspend fun upload(): SyncOutcome {
        val state = store.read()
        var credentials = state.credentials ?: return SyncOutcome.ENROLLMENT_REQUIRED
        val image = outbox.next() ?: return SyncOutcome.SUCCESS
        if (image.installationId != state.installationId) {
            outbox.remove(image.uploadId)
            store.imageUploadStatus(null, "installation_changed")
            return SyncOutcome.REJECTED
        }
        var renewed = false

        suspend fun renew() {
            val requestId = credentials.pendingRenewalId ?: newRequestId()
            val pending = credentials.pending(requestId)
            store.credentials(pending)
            val replacement = api.renew(pending, requestId)
            require(
                replacement.installationId == state.installationId &&
                    replacement.deviceId == credentials.deviceId,
            )
            store.credentials(replacement)
            credentials = replacement
            renewed = true
        }

        suspend fun <T> authenticated(operation: suspend (String) -> T): T =
            try {
                operation(credentials.accessToken)
            } catch (error: ApiFailure) {
                if (error.statusCode != 401 || renewed) throw error
                renew()
                operation(credentials.accessToken)
            }
        return try {
            if (credentials.pendingRenewalId != null) renew()
            val initialized = authenticated { api.initializeImage(it, image) }
            if (initialized.status == "finalized") {
                outbox.complete(image.uploadId)
                store.imageUploadStatus(now(), null)
                return SyncOutcome.SUCCESS
            }
            var offset = initialized.uploadedBytes
            require(offset in 0..image.byteSize)
            outbox.progress(image.uploadId, initialized.sessionId, offset)
            while (offset < image.byteSize) {
                val end = minOf(offset + MAX_CHUNK_BYTES, image.byteSize)
                val next =
                    authenticated {
                        api.uploadImageChunk(
                            it,
                            initialized.sessionId,
                            offset,
                            image.bytes.copyOfRange(offset, end),
                        )
                    }
                require(next == end)
                offset = next
                outbox.progress(image.uploadId, initialized.sessionId, offset)
            }
            authenticated { api.finalizeImage(it, initialized.sessionId) }
            outbox.complete(image.uploadId)
            store.imageUploadStatus(now(), null)
            log("images.upload_completed upload_id=${image.uploadId} byte_size=${image.byteSize}")
            SyncOutcome.SUCCESS
        } catch (error: Exception) {
            if (error is CancellationException) throw error
            if (error is ApiFailure && error.statusCode == 401) {
                store.credentials(null)
                store.imageUploadStatus(null, "enrollment_required")
                return SyncOutcome.ENROLLMENT_REQUIRED
            }
            if (RetryPolicy.retryable(error)) {
                val category =
                    if (error is ApiFailure && error.statusCode == 429) {
                        "rate_limited"
                    } else {
                        "retryable_transport"
                    }
                store.imageUploadStatus(null, category)
                log("images.upload_retryable upload_id=${image.uploadId} category=$category")
                SyncOutcome.RETRYABLE
            } else {
                outbox.remove(image.uploadId)
                store.imageUploadStatus(null, "terminal_delivery")
                log("images.upload_discarded upload_id=${image.uploadId}")
                SyncOutcome.REJECTED
            }
        }
    }

    companion object {
        private const val TAG = "RelayImageUpload"
        private const val MAX_CHUNK_BYTES = 256 * 1024
    }
}
