package com.relay.agent.domain

data class IncomingSms(val sender: String, val body: String, val receivedAtMillis: Long)

data class GenericEvent(
    val eventId: String,
    val installationId: String,
    val collector: String,
    val schemaVersion: Int,
    val configVersion: Int,
    val occurredAt: String,
    val capturedAt: String,
    val sender: String,
    val body: String,
    val ruleId: String
) {
    override fun toString(): String = "GenericEvent(eventId=$eventId,collector=$collector)"
}

data class OutboxEntry(val event: GenericEvent, val retryCount: Int)
data class OutboxStats(val queued: Int, val encryptedBytes: Long, val dropped: Long)
data class EnqueueResult(val accepted: Boolean, val droppedOldest: Int)
data class IngestionResult(val eventId: String, val status: String, val reason: String? = null)

interface EventOutbox {
    suspend fun enqueue(event: GenericEvent): EnqueueResult
    suspend fun batch(limit: Int): List<OutboxEntry>
    suspend fun remove(eventIds: Set<String>)
    suspend fun incrementRetries(eventIds: Set<String>)
    suspend fun discardAll()
    suspend fun stats(): OutboxStats
}

enum class CollectionOutcome { CAPTURED, FILTERED, BLOCKED, CAPACITY, INVALID }
