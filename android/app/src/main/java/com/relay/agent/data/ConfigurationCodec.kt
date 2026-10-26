package com.relay.agent.data

import com.relay.agent.domain.Configuration
import com.relay.agent.domain.ConfigurationParser
import com.relay.agent.domain.ImageConfiguration
import com.relay.agent.domain.ImageRule
import com.relay.agent.domain.ImageSourceConfiguration
import com.relay.agent.domain.InvalidConfiguration
import com.relay.agent.domain.SmsConfiguration
import com.relay.agent.domain.SmsRule
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull

class ConfigurationCodec : ConfigurationParser {
    override fun parse(json: String, current: Configuration?): Configuration {
        try {
            require(json.toByteArray(Charsets.UTF_8).size <= MAX_BYTES)
            val root = Json.parseToJsonElement(json) as? JsonObject ?: throw InvalidConfiguration()
            validateDepth(root, 0)
            val schema = root["schema_version"]?.jsonPrimitive
            val version = root["version"]?.jsonPrimitive
            require(schema?.isString == false && schema.intOrNull == 1)
            require(version?.isString == false)
            val number = version?.intOrNull ?: throw InvalidConfiguration()
            require(number > 0 && number >= (current?.version ?: 0))
            val settings = root["settings"] as? JsonObject ?: throw InvalidConfiguration()
            val intervalValue = settings["sync_interval_seconds"]
            val interval = if (intervalValue == null) {
                900
            } else {
                val primitive = intervalValue as? JsonPrimitive ?: throw InvalidConfiguration()
                require(!primitive.isString)
                primitive.intOrNull ?: throw InvalidConfiguration()
            }
            require(interval in 900..86400)
            if (current != null && number == current.version) {
                require(Json.parseToJsonElement(current.json) == root)
            }
            return Configuration(
                number,
                root.toString(),
                interval.toLong(),
                parseSms(settings),
                parseImages(settings)
            )
        } catch (_: IllegalArgumentException) {
            throw InvalidConfiguration()
        }
    }

    private fun parseSms(settings: JsonObject): SmsConfiguration {
        val collectors = settings["collectors"] ?: return SmsConfiguration()
        val collectorsObject = collectors as? JsonObject ?: throw InvalidConfiguration()
        require(collectorsObject.keys.all { it in setOf("sms", "images") })
        val smsValue = collectorsObject["sms"] ?: return SmsConfiguration()
        val sms = smsValue as? JsonObject ?: throw InvalidConfiguration()
        require(sms.keys == setOf("enabled", "rules"))
        val enabled = sms["enabled"]?.jsonPrimitive?.let {
            require(!it.isString)
            it.content.toBooleanStrict()
        } ?: throw InvalidConfiguration()
        val rules = sms["rules"] as? JsonArray ?: throw InvalidConfiguration()
        require(rules.size <= 20)
        val parsed = rules.map { parseRule(it as? JsonObject ?: throw InvalidConfiguration()) }
        require(parsed.map { it.id }.distinct().size == parsed.size)
        return SmsConfiguration(enabled, parsed)
    }

    private fun parseImages(settings: JsonObject): ImageConfiguration {
        val collectors = settings["collectors"] ?: return ImageConfiguration()
        val collectorsObject = collectors as? JsonObject ?: throw InvalidConfiguration()
        require(collectorsObject.keys.all { it in setOf("sms", "images") })
        val imageValue = collectorsObject["images"] ?: return ImageConfiguration()
        val images = imageValue as? JsonObject ?: throw InvalidConfiguration()
        require(images.keys == setOf("enabled", "sources", "rules"))
        val enabled = boolean(images, "enabled")
        val sourcesArray = images["sources"] as? JsonArray ?: throw InvalidConfiguration()
        require(sourcesArray.size in 1..5)
        val sources = sourcesArray.map {
            val source = it as? JsonObject ?: throw InvalidConfiguration()
            require(source.keys == setOf("id", "type"))
            ImageSourceConfiguration(string(source, "id"), string(source, "type")).also { parsed ->
                require(parsed.id.matches(SAFE_ID) && parsed.type == "saf_tree")
            }
        }
        require(sources.map { it.id }.distinct().size == sources.size)
        val rulesArray = images["rules"] as? JsonArray ?: throw InvalidConfiguration()
        require(rulesArray.size <= 10)
        val rules = rulesArray.map {
            parseImageRule(it as? JsonObject ?: throw InvalidConfiguration())
        }
        require(rules.map { it.id }.distinct().size == rules.size)
        require(rules.all { rule -> sources.any { it.id == rule.sourceId } })
        return ImageConfiguration(enabled, sources, rules)
    }

    private fun parseImageRule(value: JsonObject): ImageRule {
        val allowed = setOf(
            "id",
            "enabled",
            "source_id",
            "mime_types",
            "filename",
            "min_bytes",
            "max_bytes",
            "min_width",
            "max_width",
            "min_height",
            "max_height"
        )
        require(value.keys.all { it in allowed })
        require(setOf("id", "enabled", "source_id").all(value::containsKey))
        val id = string(value, "id")
        val sourceId = string(value, "source_id")
        require(id.matches(SAFE_ID) && sourceId.matches(SAFE_ID))
        val mimes = (value["mime_types"] as? JsonArray)?.map {
            val mime = it.jsonPrimitive
            require(mime.isString && mime.content in ALLOWED_MIMES)
            mime.content
        }?.toSet().orEmpty()
        require(mimes.size <= 3)
        val filename = value["filename"] as? JsonObject
        if (value["filename"] != null) {
            require(filename != null && filename.keys == setOf("mode", "value"))
        }
        val filenameMode = filename?.let { string(it, "mode") }
        val filenameValue = filename?.let { string(it, "value") }
        require(filenameMode == null || filenameMode in setOf("contains", "prefix", "suffix"))
        require(filenameValue == null || filenameValue.length in 1..128)
        val minBytes = optionalLong(value, "min_bytes", 1, MAX_IMAGE_BYTES)
        val maxBytes = optionalLong(value, "max_bytes", 1, MAX_IMAGE_BYTES)
        val minWidth = optionalInt(value, "min_width", 1, MAX_DIMENSION)
        val maxWidth = optionalInt(value, "max_width", 1, MAX_DIMENSION)
        val minHeight = optionalInt(value, "min_height", 1, MAX_DIMENSION)
        val maxHeight = optionalInt(value, "max_height", 1, MAX_DIMENSION)
        require(minBytes == null || maxBytes == null || minBytes <= maxBytes)
        require(minWidth == null || maxWidth == null || minWidth <= maxWidth)
        require(minHeight == null || maxHeight == null || minHeight <= maxHeight)
        return ImageRule(
            id,
            boolean(value, "enabled"),
            sourceId,
            mimes,
            filenameMode,
            filenameValue,
            minBytes,
            maxBytes,
            minWidth,
            maxWidth,
            minHeight,
            maxHeight
        )
    }

    private fun boolean(value: JsonObject, key: String): Boolean {
        val primitive = value[key]?.jsonPrimitive ?: throw InvalidConfiguration()
        require(!primitive.isString)
        return primitive.content.toBooleanStrict()
    }

    private fun optionalLong(value: JsonObject, key: String, min: Long, max: Long): Long? {
        val primitive = value[key]?.jsonPrimitive ?: return null
        require(!primitive.isString)
        return (primitive.longOrNull ?: throw InvalidConfiguration()).also {
            require(it in min..max)
        }
    }

    private fun optionalInt(value: JsonObject, key: String, min: Int, max: Int): Int? =
        optionalLong(value, key, min.toLong(), max.toLong())?.toInt()

    private fun parseRule(value: JsonObject): SmsRule {
        require(setOf("id", "enabled").all(value::containsKey))
        require(value.keys.all { it in setOf("id", "enabled", "sender", "body") })
        require("sender" in value || "body" in value)
        val id = string(value, "id")
        require(id.matches(Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,63}")))
        val enabled = value["enabled"]?.jsonPrimitive?.let {
            require(!it.isString)
            it.content.toBooleanStrict()
        } ?: throw InvalidConfiguration()
        val sender = value["sender"] as? JsonObject
        if (value["sender"] != null) {
            require(sender != null && sender.keys == setOf("mode", "value"))
        }
        val senderMode = sender?.let { string(it, "mode") }
        val senderValue = sender?.let { string(it, "value") }
        require(senderMode == null || senderMode in setOf("exact", "contains", "prefix", "suffix"))
        require(senderValue == null || senderValue.length in 1..128)
        val body = value["body"] as? JsonObject
        if (value["body"] != null) require(body != null && body.isNotEmpty())
        require(
            body == null ||
                body.keys.all { it in setOf("contains", "any_contains", "all_contains") }
        )
        val contains = body?.get("contains")?.let { string(body, "contains") }
        require(contains == null || contains.length in 1..128)
        return SmsRule(
            id,
            enabled,
            senderMode,
            senderValue,
            contains,
            terms(body, "any_contains"),
            terms(body, "all_contains")
        )
    }

    private fun terms(body: JsonObject?, key: String): List<String> {
        val value = body?.get(key) ?: return emptyList()
        val array = value as? JsonArray ?: throw InvalidConfiguration()
        require(array.size in 1..10)
        return array.map {
            val primitive = it.jsonPrimitive
            require(primitive.isString)
            val term = primitive.content
            require(term.length in 1..128)
            term
        }
    }

    private fun string(value: JsonObject, key: String): String {
        val primitive = value[key]?.jsonPrimitive ?: throw InvalidConfiguration()
        require(primitive.isString)
        return primitive.content
    }

    private fun validateDepth(value: JsonElement, depth: Int) {
        require(depth <= 12)
        when (value) {
            is JsonObject -> value.values.forEach { validateDepth(it, depth + 1) }
            is JsonArray -> value.forEach { validateDepth(it, depth + 1) }
            else -> Unit
        }
    }

    companion object {
        const val MAX_BYTES = 65536
        const val MAX_IMAGE_BYTES = 8L * 1024 * 1024
        const val MAX_DIMENSION = 12_000
        private val SAFE_ID = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
        private val ALLOWED_MIMES = setOf("image/jpeg", "image/png", "image/webp")
    }
}
