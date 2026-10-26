package com.relay.agent.data

import com.relay.agent.domain.GenericEvent
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class EventCodec {
    fun encode(event: GenericEvent): ByteArray = buildJsonObject {
        put("event_id", event.eventId)
        put("installation_id", event.installationId)
        put("collector", event.collector)
        put("schema_version", event.schemaVersion)
        put("config_version", event.configVersion)
        put("occurred_at", event.occurredAt)
        put("captured_at", event.capturedAt)
        put("sender", event.sender)
        put("body", event.body)
        put("rule_id", event.ruleId)
    }.toString().toByteArray(Charsets.UTF_8).also { require(it.size <= MAX_PLAINTEXT_BYTES) }

    fun decode(bytes: ByteArray): GenericEvent {
        require(bytes.size <= MAX_PLAINTEXT_BYTES)
        val value = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)).jsonObject
        require(
            value.keys == setOf(
                "event_id",
                "installation_id",
                "collector",
                "schema_version",
                "config_version",
                "occurred_at",
                "captured_at",
                "sender",
                "body",
                "rule_id"
            )
        )
        return GenericEvent(
            value.getValue("event_id").jsonPrimitive.content,
            value.getValue("installation_id").jsonPrimitive.content,
            value.getValue("collector").jsonPrimitive.content,
            value.getValue("schema_version").jsonPrimitive.content.toInt(),
            value.getValue("config_version").jsonPrimitive.content.toInt(),
            value.getValue("occurred_at").jsonPrimitive.content,
            value.getValue("captured_at").jsonPrimitive.content,
            value.getValue("sender").jsonPrimitive.content,
            value.getValue("body").jsonPrimitive.content,
            value.getValue("rule_id").jsonPrimitive.content
        )
    }

    companion object {
        const val MAX_PLAINTEXT_BYTES = 8192
    }
}
