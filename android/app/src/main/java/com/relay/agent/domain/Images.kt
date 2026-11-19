package com.relay.agent.domain

data class ImageCandidate(
    val sourceId: String,
    val sourceType: String,
    val uri: String,
    val observedAtMillis: Long,
    val mediaAtMillis: Long?,
    val mimeType: String,
    val displayName: String?,
    val byteSize: Long,
    val width: Int,
    val height: Int
) {
    override fun toString(): String =
        "ImageCandidate(sourceId=$sourceId,mimeType=$mimeType,byteSize=$byteSize)"
}

data class QueuedImage(
    val uploadId: String,
    val eventId: String,
    val installationId: String,
    val contentHash: String,
    val contentType: String,
    val byteSize: Int,
    val width: Int,
    val height: Int,
    val sourceType: String,
    val sourceId: String,
    val displayName: String?,
    val configVersion: Int,
    val ruleId: String,
    val occurredAt: String,
    val capturedAt: String,
    val mediaAt: String?,
    val uploadedBytes: Int,
    val sessionId: String?,
    val bytes: ByteArray
) {
    override fun equals(other: Any?): Boolean = other is QueuedImage && uploadId == other.uploadId

    override fun hashCode(): Int = uploadId.hashCode()

    override fun toString(): String = "QueuedImage(uploadId=$uploadId,byteSize=$byteSize)"
}

data class BinaryOutboxStats(val queued: Int, val encryptedBytes: Long, val dropped: Long)

data class BinaryEnqueueResult(
    val accepted: Boolean,
    val duplicate: Boolean,
    val droppedOldest: Int
)

data class UploadInitialization(
    val sessionId: String,
    val status: String,
    val uploadedBytes: Int,
    val objectId: String?
)

interface BinaryOutbox {
    suspend fun enqueue(value: QueuedImage): BinaryEnqueueResult

    suspend fun next(): QueuedImage?

    suspend fun progress(uploadId: String, sessionId: String, uploadedBytes: Int)

    suspend fun remove(uploadId: String)

    suspend fun complete(uploadId: String) = remove(uploadId)

    suspend fun discardAll()

    suspend fun clearObservations() = Unit

    suspend fun stats(): BinaryOutboxStats

    /** A photo already sent (or a copy of one sent): skip it without reading it. */
    suspend fun seen(key: String): Boolean = false

    suspend fun markSeen(key: String) = Unit
}

/** Identifies one photo file without reading it: folder, name, time and size. */
fun documentKey(sourceId: String, displayName: String?, mediaAt: String?, byteSize: Long): String =
    "$sourceId|${displayName.orEmpty()}|${mediaAt.orEmpty()}|$byteSize"

interface ImageSource {
    fun accessGranted(source: ApprovedImageSource): Boolean

    suspend fun candidates(source: ApprovedImageSource, limit: Int): List<ImageCandidate>

    /** Photos changed since [sinceMillis], newest first, at most [limit]. */
    suspend fun recent(
        source: ApprovedImageSource,
        sinceMillis: Long,
        limit: Int
    ): List<ImageCandidate> = candidates(source, limit)

    suspend fun read(candidate: ImageCandidate, maximumBytes: Int): ByteArray
}
