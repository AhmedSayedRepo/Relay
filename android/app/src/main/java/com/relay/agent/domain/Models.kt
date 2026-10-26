package com.relay.agent.domain

import java.util.Locale
import kotlinx.coroutines.flow.StateFlow

class Credentials(
    val deviceId: String,
    val installationId: String,
    val deviceName: String,
    val accessToken: String,
    val refreshToken: String,
    val refreshExpiresAt: String,
    val pendingRenewalId: String? = null
) {
    fun pending(requestId: String): Credentials = Credentials(
        deviceId,
        installationId,
        deviceName,
        accessToken,
        refreshToken,
        refreshExpiresAt,
        requestId
    )

    override fun toString(): String = "Credentials([REDACTED])"
}

data class SmsRule(
    val id: String,
    val enabled: Boolean,
    val senderMode: String?,
    val senderValue: String?,
    val bodyContains: String?,
    val bodyAnyContains: List<String>,
    val bodyAllContains: List<String>
) {
    fun matches(sender: String, body: String): Boolean {
        if (!enabled) return false
        val normalizedSender = sender.lowercase(Locale.ROOT)
        val senderMatch = when (senderMode) {
            null -> true
            "exact" -> normalizedSender == senderValue?.lowercase(Locale.ROOT)
            "contains" -> normalizedSender.contains(senderValue?.lowercase(Locale.ROOT).orEmpty())
            "prefix" -> normalizedSender.startsWith(senderValue?.lowercase(Locale.ROOT).orEmpty())
            "suffix" -> normalizedSender.endsWith(senderValue?.lowercase(Locale.ROOT).orEmpty())
            else -> false
        }
        val normalizedBody = body.lowercase(Locale.ROOT)
        return senderMatch &&
            (
                bodyContains == null ||
                    normalizedBody.contains(bodyContains.lowercase(Locale.ROOT))
                ) &&
            (
                bodyAnyContains.isEmpty() ||
                    bodyAnyContains.any {
                        normalizedBody.contains(it.lowercase(Locale.ROOT))
                    }
                ) &&
            bodyAllContains.all { normalizedBody.contains(it.lowercase(Locale.ROOT)) }
    }
}

data class SmsConfiguration(val enabled: Boolean = false, val rules: List<SmsRule> = emptyList())

data class ImageSourceConfiguration(val id: String, val type: String)

data class ImageRule(
    val id: String,
    val enabled: Boolean,
    val sourceId: String,
    val mimeTypes: Set<String> = emptySet(),
    val filenameMode: String? = null,
    val filenameValue: String? = null,
    val minBytes: Long? = null,
    val maxBytes: Long? = null,
    val minWidth: Int? = null,
    val maxWidth: Int? = null,
    val minHeight: Int? = null,
    val maxHeight: Int? = null
) {
    fun matches(candidate: ImageCandidate): Boolean {
        if (!enabled || sourceId != candidate.sourceId) return false
        if (mimeTypes.isNotEmpty() && candidate.mimeType !in mimeTypes) return false
        if (minBytes != null && candidate.byteSize < minBytes) return false
        if (maxBytes != null && candidate.byteSize > maxBytes) return false
        if (minWidth != null && candidate.width < minWidth) return false
        if (maxWidth != null && candidate.width > maxWidth) return false
        if (minHeight != null && candidate.height < minHeight) return false
        if (maxHeight != null && candidate.height > maxHeight) return false
        val expected = filenameValue?.lowercase(Locale.ROOT) ?: return true
        val name = candidate.displayName?.lowercase(Locale.ROOT) ?: return false
        return when (filenameMode) {
            "contains" -> name.contains(expected)
            "prefix" -> name.startsWith(expected)
            "suffix" -> name.endsWith(expected)
            else -> false
        }
    }
}

data class ImageConfiguration(
    val enabled: Boolean = false,
    val sources: List<ImageSourceConfiguration> = emptyList(),
    val rules: List<ImageRule> = emptyList()
)

data class Configuration(
    val version: Int,
    val json: String,
    val syncIntervalSeconds: Long = 900,
    val sms: SmsConfiguration = SmsConfiguration(),
    val images: ImageConfiguration = ImageConfiguration()
)

data class ApprovedImageSource(
    val sourceId: String,
    val treeUri: String,
    val approvedAtMillis: Long
) {
    override fun toString(): String = "ApprovedImageSource(sourceId=$sourceId,[URI REDACTED])"
}

class StoredState(
    val installationId: String,
    val credentials: Credentials? = null,
    val configuration: Configuration? = null,
    val lastSuccessfulSync: String? = null,
    val localSmsConsent: Boolean = false,
    val lastSuccessfulUpload: String? = null,
    val lastUploadError: String? = null,
    val localImageConsent: Boolean = false,
    val approvedImageSource: ApprovedImageSource? = null,
    val lastSuccessfulImageUpload: String? = null,
    val lastImageUploadError: String? = null
)

enum class EnrollmentState { LOADING, REQUIRED, ENROLLING, ENROLLED }
enum class ConnectionState { IDLE, SYNCING, CONNECTED, OFFLINE, ERROR }

data class AgentStatus(
    val installationId: String = "",
    val deviceId: String? = null,
    val deviceName: String? = null,
    val enrollment: EnrollmentState = EnrollmentState.LOADING,
    val connection: ConnectionState = ConnectionState.IDLE,
    val configurationVersion: Int = 0,
    val lastSuccessfulSync: String? = null,
    val localSmsConsent: Boolean = false,
    val smsPermissionGranted: Boolean = false,
    val serverSmsEnabled: Boolean = false,
    val effectiveSmsCollection: Boolean = false,
    val queuedEventCount: Int = 0,
    val droppedEventCount: Long = 0,
    val lastSuccessfulUpload: String? = null,
    val lastUploadError: String? = null,
    val localImageConsent: Boolean = false,
    val imageAccessGranted: Boolean = false,
    val serverImagesEnabled: Boolean = false,
    val effectiveImageCollection: Boolean = false,
    val approvedImageSourceId: String? = null,
    val queuedImageCount: Int = 0,
    val queuedImageBytes: Long = 0,
    val droppedImageCount: Long = 0,
    val lastSuccessfulImageUpload: String? = null,
    val lastImageUploadError: String? = null,
    val message: String? = null
)

enum class SyncOutcome { SUCCESS, RETRYABLE, REJECTED, ENROLLMENT_REQUIRED }

class ApiFailure(val statusCode: Int) : Exception("Relay request failed.")
class InvalidConfiguration : Exception("Configuration was not accepted.")
class SecureStorageFailure : Exception("Secure storage is unavailable.")

interface RelayApi {
    suspend fun enroll(challenge: String, installationId: String): Credentials
    suspend fun renew(credentials: Credentials, requestId: String): Credentials
    suspend fun configuration(accessToken: String, knownVersion: Int): String?
    suspend fun acknowledge(accessToken: String, version: Int)
    suspend fun heartbeat(accessToken: String, version: Int)
    suspend fun uploadEvents(accessToken: String, events: List<GenericEvent>): List<IngestionResult>
    suspend fun initializeImage(accessToken: String, image: QueuedImage): UploadInitialization =
        throw ApiFailure(501)
    suspend fun uploadImageChunk(
        accessToken: String,
        sessionId: String,
        offset: Int,
        bytes: ByteArray
    ): Int = throw ApiFailure(501)
    suspend fun finalizeImage(accessToken: String, sessionId: String): String =
        throw ApiFailure(501)
}

interface AgentStore {
    suspend fun read(): StoredState
    suspend fun credentials(value: Credentials?)
    suspend fun configuration(value: Configuration)
    suspend fun lastSuccessfulSync(value: String)
    suspend fun localSmsConsent(value: Boolean)
    suspend fun uploadStatus(successAt: String?, error: String?)
    suspend fun localImageConsent(value: Boolean) = Unit
    suspend fun approvedImageSource(value: ApprovedImageSource?) = Unit
    suspend fun imageUploadStatus(successAt: String?, error: String?) = Unit
}

interface ConfigurationParser {
    fun parse(json: String, current: Configuration?): Configuration
}

interface AgentOperations {
    val status: StateFlow<AgentStatus>
    suspend fun initialize()
    suspend fun enroll(challenge: String): SyncOutcome
    suspend fun sync(): SyncOutcome
}
