package com.relay.agent.application

import com.relay.agent.domain.AgentOperations
import com.relay.agent.domain.AgentStatus
import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.ApiFailure
import com.relay.agent.domain.ApprovedImageSource
import com.relay.agent.domain.BinaryOutbox
import com.relay.agent.domain.ConfigurationParser
import com.relay.agent.domain.ConnectionState
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.EnrollmentState
import com.relay.agent.domain.EventOutbox
import com.relay.agent.domain.RelayApi
import com.relay.agent.domain.StoredState
import com.relay.agent.domain.SyncOutcome
import java.time.Instant
import java.util.UUID
import java.util.logging.Logger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** One application instance is shared by UI and WorkManager; every credential mutation is serialized. */
class AgentController(
    private val store: AgentStore,
    private val api: RelayApi,
    private val parser: ConfigurationParser,
    private val outbox: EventOutbox? = null,
    private val binaryOutbox: BinaryOutbox? = null,
    private val smsPermissionGranted: () -> Boolean = { false },
    private val imageAccessGranted: (ApprovedImageSource) -> Boolean = { false },
    private val now: () -> String = { Instant.now().toString() },
    private val newRequestId: () -> String = { UUID.randomUUID().toString() }
) : AgentOperations {
    private val mutex = Mutex()
    private val mutableStatus = MutableStateFlow(AgentStatus())
    override val status = mutableStatus.asStateFlow()

    override suspend fun initialize() = mutex.withLock {
        try {
            publish(store.read())
        } catch (error: Exception) {
            cancelled(error)
            mutableStatus.value =
                AgentStatus(
                    enrollment = EnrollmentState.REQUIRED,
                    connection = ConnectionState.ERROR,
                    message = "Local storage could not be read. Restart the app and try again."
                )
        }
    }

    override suspend fun enroll(challenge: String): SyncOutcome = mutex.withLock {
        if (challenge.isBlank() || challenge.length > 1024) {
            mutableStatus.value =
                mutableStatus.value.copy(message = "Enter a valid enrollment token.")
            return@withLock SyncOutcome.REJECTED
        }
        mutableStatus.value =
            mutableStatus.value.copy(enrollment = EnrollmentState.ENROLLING, message = null)
        try {
            val current = store.read()
            val credentials = api.enroll(challenge.trim(), current.installationId)
            require(credentials.installationId == current.installationId)
            store.credentials(credentials)
            publish(
                store.read(),
                message = "Enrolled successfully. Sync to retrieve configuration."
            )
            SyncOutcome.SUCCESS
        } catch (error: Exception) {
            cancelled(error)
            val connection =
                if (RetryPolicy.retryable(error)) {
                    ConnectionState.OFFLINE
                } else {
                    ConnectionState.ERROR
                }
            mutableStatus.value =
                mutableStatus.value.copy(
                    enrollment = EnrollmentState.REQUIRED,
                    connection = connection,
                    message =
                    "Enrollment did not complete. Ask your administrator for a new token " +
                        "before trying again."
                )
            // A consumed challenge is never silently resubmitted after an ambiguous failure.
            SyncOutcome.REJECTED
        }
    }

    override suspend fun sync(): SyncOutcome = mutex.withLock {
        mutableStatus.value =
            mutableStatus.value.copy(connection = ConnectionState.SYNCING, message = null)
        try {
            val stored = store.read()
            var credentials = stored.credentials ?: return@withLock requireEnrollment()
            var refreshed = false

            suspend fun renew() {
                val requestId = credentials.pendingRenewalId ?: newRequestId()
                val pending = credentials.pending(requestId)
                // Durable before submission, including process-death recovery.
                store.credentials(pending)
                val replacement = api.renew(pending, requestId)
                require(
                    replacement.installationId == stored.installationId &&
                        replacement.deviceId == credentials.deviceId
                )
                store.credentials(replacement)
                credentials = replacement
                refreshed = true
            }
            if (credentials.pendingRenewalId != null) renew()

            suspend fun <T> authenticated(request: suspend (String) -> T): T {
                try {
                    return request(credentials.accessToken)
                } catch (error: ApiFailure) {
                    if (error.statusCode != 401 || refreshed) throw error
                    renew()
                    return request(credentials.accessToken)
                }
            }
            var configuration = stored.configuration
            val document = authenticated { api.configuration(it, configuration?.version ?: 0) }
            if (document != null) {
                configuration = parser.parse(document, configuration)
                store.configuration(configuration)
            }
            val applied = configuration
            if (applied != null) authenticated { api.acknowledge(it, applied.version) }
            authenticated { api.heartbeat(it, applied?.version ?: 0) }
            store.lastSuccessfulSync(now())
            publish(store.read(), ConnectionState.CONNECTED, "Configuration is up to date.")
            SyncOutcome.SUCCESS
        } catch (error: Exception) {
            cancelled(error)
            if (error is ApiFailure &&
                error.statusCode == 401
            ) {
                return@withLock requireEnrollment()
            }
            val retryable = RetryPolicy.retryable(error)
            mutableStatus.value =
                mutableStatus.value.copy(
                    connection = if (retryable) ConnectionState.OFFLINE else ConnectionState.ERROR,
                    message =
                    if (retryable) {
                        "Connection unavailable. Last valid configuration remains available."
                    } else {
                        "Sync was rejected. Last valid configuration remains available."
                    }
                )
            if (retryable) SyncOutcome.RETRYABLE else SyncOutcome.REJECTED
        }
    }

    suspend fun upload(): SyncOutcome = mutex.withLock {
        val queue = outbox ?: return@withLock SyncOutcome.SUCCESS
        val stored = store.read()
        var credentials = stored.credentials ?: return@withLock requireEnrollment()
        val available = queue.batch(20)
        if (available.isEmpty()) {
            publish(stored)
            return@withLock SyncOutcome.SUCCESS
        }
        var estimatedBytes = 32
        val selected =
            available
                .takeWhile {
                    // JSON escaping can expand one UTF-8 input byte to six ASCII bytes (for example, \\u0000).
                    estimatedBytes += (
                        it.event.body
                            .toByteArray()
                            .size * 6
                        ) +
                        (
                            it.event.sender
                                .toByteArray()
                                .size * 6
                            ) + 512
                    estimatedBytes <= 14 * 1024
                }.ifEmpty { listOf(available.first()) }
        val ids = selected.map { it.event.eventId }.toSet()
        try {
            suspend fun renew() {
                val requestId = credentials.pendingRenewalId ?: newRequestId()
                val pending = credentials.pending(requestId)
                store.credentials(pending)
                val replacement = api.renew(pending, requestId)
                require(
                    replacement.installationId == stored.installationId &&
                        replacement.deviceId == credentials.deviceId
                )
                store.credentials(replacement)
                credentials = replacement
            }
            if (credentials.pendingRenewalId != null) renew()
            val results =
                try {
                    api.uploadEvents(credentials.accessToken, selected.map { it.event })
                } catch (error: ApiFailure) {
                    if (error.statusCode != 401) throw error
                    renew()
                    api.uploadEvents(credentials.accessToken, selected.map { it.event })
                }
            require(results.size == ids.size)
            require(results.map { it.eventId }.toSet() == ids)
            require(results.map { it.eventId }.distinct().size == results.size)
            queue.remove(ids)
            val rejected = results.count { it.status == "rejected" }
            store.uploadStatus(now(), if (rejected > 0) "permanent_rejection" else null)
            logger.info(
                "events.upload_completed count=${selected.size} rejected_count=$rejected"
            )
            publish(store.read(), ConnectionState.CONNECTED, "Event upload completed.")
            SyncOutcome.SUCCESS
        } catch (error: Exception) {
            cancelled(error)
            if (error is ApiFailure &&
                error.statusCode == 401
            ) {
                return@withLock requireEnrollment()
            }
            if (RetryPolicy.retryable(error)) {
                queue.incrementRetries(ids)
                val category =
                    if (error is ApiFailure && error.statusCode == 429) {
                        "rate_limited"
                    } else {
                        "retryable_transport"
                    }
                store.uploadStatus(null, category)
                logger.info("events.upload_retryable count=${selected.size} category=$category")
                publish(store.read(), ConnectionState.OFFLINE, "Event upload will retry.")
                return@withLock SyncOutcome.RETRYABLE
            }
            queue.remove(ids)
            store.uploadStatus(null, "terminal_delivery")
            logger.info("events.upload_discarded count=${selected.size}")
            publish(
                store.read(),
                ConnectionState.ERROR,
                "Invalid queued events were discarded."
            )
            SyncOutcome.REJECTED
        }
    }

    suspend fun setLocalSmsConsent(value: Boolean) = mutex.withLock {
        store.localSmsConsent(value)
        publish(
            store.read(),
            message = if (value) "Local SMS consent enabled." else "Local SMS consent disabled."
        )
    }

    suspend fun setLocalImageConsent(value: Boolean) = mutex.withLock {
        store.localImageConsent(value)
        if (!value) {
            binaryOutbox?.discardAll()
            binaryOutbox?.clearObservations()
        }
        publish(
            store.read(),
            message =
            if (value) {
                "Local image consent enabled. Select a folder to approve access."
            } else {
                "Local image consent disabled and queued images removed."
            }
        )
    }

    suspend fun approveImageSource(sourceId: String, treeUri: String) = mutex.withLock {
        val state = store.read()
        require(state.localImageConsent)
        require(
            state.configuration?.images?.sources?.any {
                it.id == sourceId && it.type == "saf_tree"
            } == true
        )
        binaryOutbox?.discardAll()
        binaryOutbox?.clearObservations()
        store.approvedImageSource(
            ApprovedImageSource(sourceId, treeUri, System.currentTimeMillis())
        )
        publish(store.read(), message = "Image source approved for new images only.")
    }

    suspend fun clearImageSource() = mutex.withLock {
        binaryOutbox?.discardAll()
        binaryOutbox?.clearObservations()
        store.approvedImageSource(null)
        publish(store.read(), message = "Image source access removed locally.")
    }

    suspend fun discardQueuedEvents() = mutex.withLock {
        outbox?.discardAll()
        publish(store.read(), message = "Queued events discarded locally.")
    }

    suspend fun refreshPrivacyState() = mutex.withLock {
        publish(store.read())
    }

    private suspend fun requireEnrollment(): SyncOutcome {
        store.credentials(null)
        publish(
            store.read(),
            ConnectionState.ERROR,
            "Enrollment is required. Contact your administrator."
        )
        return SyncOutcome.ENROLLMENT_REQUIRED
    }

    private suspend fun publish(
        stored: StoredState,
        connection: ConnectionState = ConnectionState.IDLE,
        message: String? = null
    ) {
        val permission = smsPermissionGranted()
        val queue = outbox?.stats()
        val imageQueue = binaryOutbox?.stats()
        val approvedSource = stored.approvedImageSource
        val sourceAccess = approvedSource?.let(imageAccessGranted) ?: false
        mutableStatus.value =
            AgentStatus(
                installationId = stored.installationId,
                deviceId = stored.credentials?.deviceId,
                deviceName = stored.credentials?.deviceName,
                enrollment =
                if (stored.credentials ==
                    null
                ) {
                    EnrollmentState.REQUIRED
                } else {
                    EnrollmentState.ENROLLED
                },
                connection = connection,
                configurationVersion = stored.configuration?.version ?: 0,
                lastSuccessfulSync = stored.lastSuccessfulSync,
                localSmsConsent = stored.localSmsConsent,
                smsPermissionGranted = permission,
                serverSmsEnabled = stored.configuration?.sms?.enabled ?: false,
                effectiveSmsCollection =
                stored.localSmsConsent &&
                    permission &&
                    stored.configuration?.sms?.enabled == true &&
                    stored.credentials != null,
                queuedEventCount = queue?.queued ?: 0,
                droppedEventCount = queue?.dropped ?: 0,
                lastSuccessfulUpload = stored.lastSuccessfulUpload,
                lastUploadError = stored.lastUploadError,
                localImageConsent = stored.localImageConsent,
                imageAccessGranted = sourceAccess,
                serverImagesEnabled = stored.configuration?.images?.enabled ?: false,
                effectiveImageCollection =
                stored.localImageConsent &&
                    sourceAccess &&
                    stored.configuration?.images?.enabled == true &&
                    stored.credentials != null,
                approvedImageSourceId = approvedSource?.sourceId,
                queuedImageCount = imageQueue?.queued ?: 0,
                queuedImageBytes = imageQueue?.encryptedBytes ?: 0,
                droppedImageCount = imageQueue?.dropped ?: 0,
                lastSuccessfulImageUpload = stored.lastSuccessfulImageUpload,
                lastImageUploadError = stored.lastImageUploadError,
                message = message
            )
    }

    private fun cancelled(error: Exception) {
        if (error is CancellationException) throw error
    }

    companion object {
        private val logger: Logger = Logger.getLogger(AgentController::class.java.name)
    }
}
