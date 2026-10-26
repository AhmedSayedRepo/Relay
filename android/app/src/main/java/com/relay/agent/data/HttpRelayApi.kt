package com.relay.agent.data

import com.relay.agent.domain.ApiFailure
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.GenericEvent
import com.relay.agent.domain.IngestionResult
import com.relay.agent.domain.QueuedImage
import com.relay.agent.domain.RelayApi
import com.relay.agent.domain.UploadInitialization
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

class HttpRelayApi(baseUrl: String, private val appVersion: String, debug: Boolean) : RelayApi {
    private val base = baseUrl.toHttpUrl().also {
        require(
            it.username.isEmpty() && it.password.isEmpty() && it.query == null &&
                it.fragment == null
        )
        require(it.encodedPath == "/")
        require(it.isHttps || (debug && it.host in setOf("localhost", "127.0.0.1", "10.0.2.2")))
    }
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    override suspend fun enroll(challenge: String, installationId: String): Credentials =
        tokenRequest(
            "api/v1/device/enroll",
            buildJsonObject {
                put("challenge", challenge)
                put("installation_id", installationId)
                put("name", "Relay Android agent")
                put("platform", "android")
                put("app_version", appVersion)
            }
        )

    override suspend fun renew(credentials: Credentials, requestId: String): Credentials =
        tokenRequest(
            "api/v1/device/renew",
            buildJsonObject {
                put("refresh_token", credentials.refreshToken)
                put("installation_id", credentials.installationId)
                put("request_id", requestId)
            }
        )

    override suspend fun configuration(accessToken: String, knownVersion: Int): String? = execute(
        Request.Builder()
            .url(
                base.newBuilder().addPathSegments(
                    "api/v1/device/configuration"
                ).addQueryParameter("known_version", "$knownVersion").build()
            )
            .header("Authorization", "Bearer $accessToken").get().build(),
        allowNotModified = knownVersion > 0
    )

    override suspend fun acknowledge(accessToken: String, version: Int) {
        post(
            "api/v1/device/configuration/ack",
            buildJsonObject {
                put("version", version)
            },
            accessToken
        )
    }

    override suspend fun heartbeat(accessToken: String, version: Int) {
        post(
            "api/v1/device/heartbeat",
            buildJsonObject {
                put("app_version", appVersion)
                put("current_config_version", version)
            },
            accessToken
        )
    }

    override suspend fun uploadEvents(
        accessToken: String,
        events: List<GenericEvent>
    ): List<IngestionResult> {
        val body = buildJsonObject {
            put(
                "events",
                buildJsonArray {
                    events.forEach { event ->
                        add(
                            buildJsonObject {
                                put("event_id", event.eventId)
                                put("installation_id", event.installationId)
                                put("collector", event.collector)
                                put("schema_version", event.schemaVersion)
                                put("config_version", event.configVersion)
                                put("occurred_at", event.occurredAt)
                                put("captured_at", event.capturedAt)
                                put(
                                    "payload",
                                    buildJsonObject {
                                        put("sender", event.sender)
                                        put("body", event.body)
                                    }
                                )
                                put("metadata", buildJsonObject { put("rule_id", event.ruleId) })
                            }
                        )
                    }
                }
            )
        }
        require(body.toString().toByteArray(Charsets.UTF_8).size <= MAX_BATCH_REQUEST_BYTES)
        val document = post("api/v1/device/events/batch", body, accessToken)
            ?: throw ApiFailure(502)
        return try {
            Json.parseToJsonElement(document).jsonObject.getValue("results").jsonArray.map {
                val result = it.jsonObject
                val status = result.getValue("status").jsonPrimitive.content
                require(status in setOf("accepted", "duplicate", "rejected"))
                IngestionResult(
                    result.getValue("event_id").jsonPrimitive.content,
                    status,
                    result["reason"]?.jsonPrimitive?.contentOrNull
                )
            }
        } catch (_: Exception) {
            throw ApiFailure(502)
        }
    }

    override suspend fun initializeImage(
        accessToken: String,
        image: QueuedImage
    ): UploadInitialization {
        val body = buildJsonObject {
            put("upload_id", image.uploadId)
            put("event_id", image.eventId)
            put("installation_id", image.installationId)
            put("content_hash", image.contentHash)
            put("content_type", image.contentType)
            put("byte_size", image.byteSize)
            put("width", image.width)
            put("height", image.height)
            put("source_type", image.sourceType)
            put("source_id", image.sourceId)
            image.displayName?.let { put("display_name", it) }
                ?: put("display_name", JsonNull)
            put("config_version", image.configVersion)
            put("rule_id", image.ruleId)
            put("occurred_at", image.occurredAt)
            put("captured_at", image.capturedAt)
            image.mediaAt?.let { put("media_at", it) } ?: put("media_at", JsonNull)
        }
        val document = post("api/v1/device/objects/uploads", body, accessToken)
            ?: throw ApiFailure(502)
        return try {
            val root = Json.parseToJsonElement(document).jsonObject
            val status = root.getValue("status").jsonPrimitive.content
            require(status in setOf("upload_required", "finalized"))
            UploadInitialization(
                root.getValue("session_id").jsonPrimitive.content,
                status,
                root.getValue("uploaded_bytes").jsonPrimitive.int,
                root["object_id"]?.jsonPrimitive?.contentOrNull
            )
        } catch (_: Exception) {
            throw ApiFailure(502)
        }
    }

    override suspend fun uploadImageChunk(
        accessToken: String,
        sessionId: String,
        offset: Int,
        bytes: ByteArray
    ): Int {
        require(bytes.isNotEmpty() && bytes.size <= MAX_CHUNK_BYTES)
        val url = base.newBuilder()
            .addPathSegments("api/v1/device/objects/uploads/$sessionId/chunks")
            .addQueryParameter("offset", offset.toString())
            .build()
        val document = execute(
            Request.Builder()
                .url(url)
                .header("Authorization", "Bearer $accessToken")
                .put(bytes.toRequestBody("application/octet-stream".toMediaType()))
                .build()
        ) ?: throw ApiFailure(502)
        return try {
            Json.parseToJsonElement(document).jsonObject
                .getValue("uploaded_bytes").jsonPrimitive.int
        } catch (_: Exception) {
            throw ApiFailure(502)
        }
    }

    override suspend fun finalizeImage(accessToken: String, sessionId: String): String {
        val document = post(
            "api/v1/device/objects/uploads/$sessionId/finalize",
            buildJsonObject {},
            accessToken
        ) ?: throw ApiFailure(502)
        return try {
            Json.parseToJsonElement(document).jsonObject
                .getValue("object").jsonObject.getValue("id").jsonPrimitive.content
        } catch (_: Exception) {
            throw ApiFailure(502)
        }
    }

    private suspend fun tokenRequest(path: String, body: JsonObject): Credentials {
        val document = post(path, body) ?: throw ApiFailure(502)
        return try {
            val root = Json.parseToJsonElement(document).jsonObject
            require(root["token_type"]?.jsonPrimitive?.content == "bearer")
            CredentialCodec.parse(root)
        } catch (_: Exception) {
            throw ApiFailure(502)
        }
    }

    private suspend fun post(path: String, body: JsonObject, accessToken: String? = null): String? {
        val request = Request.Builder().url(base.newBuilder().addPathSegments(path).build())
            .post(body.toString().toRequestBody("application/json".toMediaType()))
        if (accessToken != null) request.header("Authorization", "Bearer $accessToken")
        return execute(request.build())
    }

    private suspend fun execute(request: Request, allowNotModified: Boolean = false): String? =
        withContext(Dispatchers.IO) {
            client.newCall(request).execute().use { response ->
                if (response.code == 304 && allowNotModified) return@withContext null
                if (!response.isSuccessful) throw ApiFailure(response.code)
                val body = response.body ?: throw ApiFailure(502)
                val source = body.source()
                if (source.request(ConfigurationCodec.MAX_BYTES.toLong() + 1)) throw ApiFailure(502)
                source.readUtf8()
            }
        }

    companion object {
        const val MAX_BATCH_REQUEST_BYTES = 14 * 1024
        const val MAX_CHUNK_BYTES = 256 * 1024
    }
}
