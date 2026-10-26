package com.relay.agent.data

import com.relay.agent.domain.QueuedImage
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream

class BinaryImageCodec {
    fun encode(value: QueuedImage): ByteArray {
        require(value.bytes.size == value.byteSize && value.byteSize in 1..MAX_IMAGE_BYTES)
        return ByteArrayOutputStream().use { buffer ->
            DataOutputStream(buffer).use { output ->
                output.writeInt(VERSION)
                output.text(value.uploadId)
                output.text(value.eventId)
                output.text(value.installationId)
                output.text(value.contentHash)
                output.text(value.contentType)
                output.writeInt(value.byteSize)
                output.writeInt(value.width)
                output.writeInt(value.height)
                output.text(value.sourceType)
                output.text(value.sourceId)
                output.nullableText(value.displayName)
                output.writeInt(value.configVersion)
                output.text(value.ruleId)
                output.text(value.occurredAt)
                output.text(value.capturedAt)
                output.nullableText(value.mediaAt)
                output.writeInt(value.uploadedBytes)
                output.nullableText(value.sessionId)
                output.write(value.bytes)
            }
            buffer.toByteArray()
        }
    }

    fun decode(bytes: ByteArray): QueuedImage =
        DataInputStream(ByteArrayInputStream(bytes)).use { input ->
            require(input.readInt() == VERSION)
            val uploadId = input.text()
            val eventId = input.text()
            val installationId = input.text()
            val contentHash = input.text()
            val contentType = input.text()
            val size = input.readInt()
            require(size in 1..MAX_IMAGE_BYTES)
            val width = input.readInt()
            val height = input.readInt()
            val sourceType = input.text()
            val sourceId = input.text()
            val displayName = input.nullableText()
            val configVersion = input.readInt()
            val ruleId = input.text()
            val occurredAt = input.text()
            val capturedAt = input.text()
            val mediaAt = input.nullableText()
            val uploadedBytes = input.readInt()
            val sessionId = input.nullableText()
            require(uploadedBytes in 0..size)
            val content = ByteArray(size)
            input.readFully(content)
            require(input.read() == -1)
            QueuedImage(
                uploadId,
                eventId,
                installationId,
                contentHash,
                contentType,
                size,
                width,
                height,
                sourceType,
                sourceId,
                displayName,
                configVersion,
                ruleId,
                occurredAt,
                capturedAt,
                mediaAt,
                uploadedBytes,
                sessionId,
                content
            )
        }

    private fun DataOutputStream.text(value: String) {
        val bytes = value.toByteArray(Charsets.UTF_8)
        require(bytes.size <= MAX_TEXT_BYTES)
        writeInt(bytes.size)
        write(bytes)
    }

    private fun DataOutputStream.nullableText(value: String?) {
        writeBoolean(value != null)
        if (value != null) text(value)
    }

    private fun DataInputStream.text(): String {
        val length = readInt()
        require(length in 0..MAX_TEXT_BYTES)
        return ByteArray(length).also(::readFully).toString(Charsets.UTF_8)
    }

    private fun DataInputStream.nullableText(): String? = if (readBoolean()) text() else null

    companion object {
        private const val VERSION = 1
        private const val MAX_TEXT_BYTES = 512
        private const val MAX_IMAGE_BYTES = 8 * 1024 * 1024
    }
}
