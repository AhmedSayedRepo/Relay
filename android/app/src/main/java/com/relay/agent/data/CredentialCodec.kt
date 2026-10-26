package com.relay.agent.data

import com.relay.agent.domain.Credentials
import com.relay.agent.domain.SecureStorageFailure
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class CredentialCodec(private val cipher: CredentialCipher) {
    fun encode(credentials: Credentials): String {
        val raw = buildJsonObject {
            put("device_id", credentials.deviceId)
            put("installation_id", credentials.installationId)
            put("device_name", credentials.deviceName)
            put("access_token", credentials.accessToken)
            put("refresh_token", credentials.refreshToken)
            put("refresh_expires_at", credentials.refreshExpiresAt)
            credentials.pendingRenewalId?.let { put("pending_renewal_id", it) }
        }.toString().toByteArray(Charsets.UTF_8)
        return try {
            Base64.getEncoder().encodeToString(cipher.encrypt(raw))
        } finally {
            raw.fill(0)
        }
    }

    fun decode(blob: String): Credentials {
        try {
            val raw = cipher.decrypt(Base64.getDecoder().decode(blob))
            return try {
                parse(Json.parseToJsonElement(raw.toString(Charsets.UTF_8)).jsonObject)
            } finally {
                raw.fill(0)
            }
        } catch (_: Exception) {
            throw SecureStorageFailure()
        }
    }

    companion object {
        fun parse(root: JsonObject): Credentials {
            fun field(name: String): String =
                root[name]?.jsonPrimitive?.content ?: error("Invalid credential response.")
            val deviceId = field("device_id").also { UUID.fromString(it) }
            val installationId = field("installation_id").also { UUID.fromString(it) }
            val access = field("access_token").also {
                require(it.isNotBlank() && it.length <= 8192)
            }
            val refresh = field("refresh_token").also {
                require(it.isNotBlank() && it.length <= 1024)
            }
            val expires = field("refresh_expires_at").also { Instant.parse(it) }
            val pending = root["pending_renewal_id"]?.jsonPrimitive?.content?.also {
                UUID.fromString(it)
            }
            return Credentials(
                deviceId,
                installationId,
                field("device_name"),
                access,
                refresh,
                expires,
                pending
            )
        }
    }
}
