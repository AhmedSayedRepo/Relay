package com.relay.agent.application

import android.util.Log
import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.BinaryOutbox
import com.relay.agent.domain.ImageSource
import com.relay.agent.domain.QueuedImage
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException

data class ImageCollectionSummary(
    val captured: Int = 0,
    val filtered: Int = 0,
    val rejected: Int = 0
)

class ImageCollector(
    private val store: AgentStore,
    private val source: ImageSource,
    private val outbox: BinaryOutbox,
    private val now: () -> Instant = Instant::now,
    private val newId: () -> String = { UUID.randomUUID().toString() },
    private val log: (String) -> Unit = { Log.i(TAG, it) }
) {
    suspend fun collect(): ImageCollectionSummary {
        val state = store.read()
        val approved = state.approvedImageSource
        val configuration = state.configuration
        if (
            !state.localImageConsent ||
            state.credentials == null ||
            approved == null ||
            configuration == null ||
            !configuration.images.enabled ||
            configuration.images.sources.none {
                it.id == approved.sourceId && it.type == "saf_tree"
            } ||
            !source.accessGranted(approved)
        ) {
            log("images.collection_blocked")
            return ImageCollectionSummary()
        }
        var captured = 0
        var filtered = 0
        var rejected = 0
        val successfullyHandledThroughMillis =
            state.lastSuccessfulImageUpload
                ?.let { value -> runCatching { Instant.parse(value).toEpochMilli() }.getOrNull() }
        val candidates =
            try {
                source.candidates(approved, MAX_SCAN_CANDIDATES)
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                log("images.source_unavailable")
                return ImageCollectionSummary(rejected = 1)
            }
        for (candidate in candidates) {
            if (
                successfullyHandledThroughMillis != null &&
                candidate.mediaAtMillis != null &&
                candidate.mediaAtMillis <= successfullyHandledThroughMillis
            ) {
                filtered += 1
                continue
            }
            val rule = configuration.images.rules.firstOrNull { it.matches(candidate) }
            if (rule == null) {
                filtered += 1
                continue
            }
            try {
                val bytes = source.read(candidate, MAX_IMAGE_BYTES)
                val capturedAt = now()
                val result =
                    outbox.enqueue(
                        QueuedImage(
                            newId(),
                            newId(),
                            state.installationId,
                            sha256(bytes),
                            candidate.mimeType,
                            bytes.size,
                            candidate.width,
                            candidate.height,
                            candidate.sourceType,
                            candidate.sourceId,
                            candidate.displayName,
                            configuration.version,
                            rule.id,
                            Instant
                                .ofEpochMilli(
                                    candidate.mediaAtMillis ?: candidate.observedAtMillis
                                ).toString(),
                            capturedAt.toString(),
                            candidate.mediaAtMillis?.let { Instant.ofEpochMilli(it).toString() },
                            0,
                            null,
                            bytes
                        )
                    )
                if (result.accepted) {
                    captured += 1
                } else if (!result.duplicate) {
                    rejected += 1
                }
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                rejected += 1
            }
        }
        log(
            "images.collection_completed captured=$captured filtered=$filtered rejected=$rejected"
        )
        return ImageCollectionSummary(captured, filtered, rejected)
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest
        .getInstance("SHA-256")
        .digest(bytes)
        .joinToString("") { "%02x".format(it.toInt() and 0xff) }

    companion object {
        const val MAX_SCAN_CANDIDATES = 50
        const val MAX_IMAGE_BYTES = 8 * 1024 * 1024
        private const val TAG = "RelayImageCollection"
    }
}
