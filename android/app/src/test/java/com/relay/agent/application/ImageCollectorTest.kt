package com.relay.agent.application

import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.ApprovedImageSource
import com.relay.agent.domain.BinaryEnqueueResult
import com.relay.agent.domain.BinaryOutbox
import com.relay.agent.domain.BinaryOutboxStats
import com.relay.agent.domain.Configuration
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.ImageCandidate
import com.relay.agent.domain.ImageConfiguration
import com.relay.agent.domain.ImageRule
import com.relay.agent.domain.ImageSource
import com.relay.agent.domain.ImageSourceConfiguration
import com.relay.agent.domain.QueuedImage
import com.relay.agent.domain.StoredState
import java.time.Instant
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class ImageCollectorTest {
    private val approved = ApprovedImageSource("folder", "content://tree/redacted", 1000)
    private val bytes = byteArrayOf(1, 2, 3, 4)
    private val rule =
        ImageRule(
            "png",
            true,
            "folder",
            setOf("image/png"),
            "suffix",
            ".png",
            maxBytes = 100,
            maxWidth = 100,
            maxHeight = 100
        )
    private val configuration =
        Configuration(
            2,
            "{}",
            images =
            ImageConfiguration(
                true,
                listOf(ImageSourceConfiguration("folder", "saf_tree")),
                listOf(rule)
            )
        )

    @Test
    fun requiresEveryConsentAccessConfigurationAndEnrollmentGate() = runTest {
        val source = FakeImageSource(access = true)
        val outbox = MemoryBinaryOutbox()
        val blocked =
            listOf(
                state(consent = false),
                state(credentials = null),
                state(configuration = Configuration(2, "{}")),
                state(source = null)
            )
        blocked.forEach { value ->
            assertEquals(
                0,
                ImageCollector(StaticImageStore(value), source, outbox, log = {
                }).collect().captured
            )
        }
        assertEquals(
            0,
            ImageCollector(StaticImageStore(state()), FakeImageSource(false), outbox, log = {})
                .collect()
                .captured
        )
        assertEquals(0, outbox.values.size)
    }

    @Test
    fun filtersBeforeReadHashesBytesAndDeduplicatesContent() = runTest {
        val source = FakeImageSource(access = true)
        val outbox = MemoryBinaryOutbox()
        val collector =
            ImageCollector(
                StaticImageStore(state()),
                source,
                outbox,
                now = { Instant.parse("2026-01-01T00:00:00Z") },
                newId = { "00000000-0000-0000-0000-000000000001" },
                log = {}
            )
        val result = collector.collect()
        assertEquals(1, result.captured)
        assertEquals(1, result.filtered)
        assertEquals(1, source.readCount)
        assertEquals(
            "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a",
            outbox.values.single().contentHash
        )
        assertEquals(0, collector.collect().captured)
        assertEquals(1, outbox.values.size)
    }

    @Test
    fun sourceDisappearingBeforeEncryptedCopyIsRejected() = runTest {
        val outbox = MemoryBinaryOutbox()
        val result =
            ImageCollector(
                StaticImageStore(state()),
                FakeImageSource(access = true, readFails = true),
                outbox,
                log = {}
            ).collect()

        assertEquals(0, result.captured)
        assertEquals(1, result.rejected)
        assertEquals(0, outbox.values.size)
    }

    @Test
    fun successfullyHandledImageIsNotRecollectedAcrossCollectorRestart() = runTest {
        val source = FakeImageSource(access = true)
        val outbox = MemoryBinaryOutbox()
        val first = ImageCollector(StaticImageStore(state()), source, outbox, log = {})

        assertEquals(1, first.collect().captured)
        outbox.complete(outbox.values.single().uploadId)

        val restarted = ImageCollector(StaticImageStore(state()), source, outbox, log = {})
        assertEquals(0, restarted.collect().captured)
        assertEquals(0, outbox.values.size)
    }

    @Test
    fun changedImageBytesAtSameSourceAreCollectedAgain() = runTest {
        val source = FakeImageSource(access = true)
        val outbox = MemoryBinaryOutbox()
        val collector = ImageCollector(StaticImageStore(state()), source, outbox, log = {})

        assertEquals(1, collector.collect().captured)
        outbox.complete(outbox.values.single().uploadId)
        source.imageBytes = byteArrayOf(4, 3, 2, 1)

        assertEquals(1, collector.collect().captured)
        assertEquals(1, outbox.values.size)
    }

    @Test
    fun successfulUploadWatermarkPreventsLegacyRecollectionWithoutReadingBytes() = runTest {
        val source = FakeImageSource(access = true)
        val outbox = MemoryBinaryOutbox()
        val collector =
            ImageCollector(
                StaticImageStore(state(lastImageUpload = "2026-01-01T00:00:03Z")),
                source,
                outbox,
                log = {}
            )

        val result = collector.collect()

        assertEquals(0, result.captured)
        assertEquals(2, result.filtered)
        assertEquals(0, source.readCount)
        assertEquals(0, outbox.values.size)
    }

    private fun state(
        consent: Boolean = true,
        credentials: Credentials? =
            Credentials(
                "device",
                "installation",
                "name",
                "access",
                "refresh",
                "2099-01-01T00:00:00Z"
            ),
        configuration: Configuration? = this.configuration,
        source: ApprovedImageSource? = approved,
        lastImageUpload: String? = null
    ) = StoredState(
        "installation",
        credentials,
        configuration,
        localImageConsent = consent,
        approvedImageSource = source,
        lastSuccessfulImageUpload = lastImageUpload
    )

    private inner class FakeImageSource(
        private val access: Boolean,
        private val readFails: Boolean = false
    ) : ImageSource {
        var readCount = 0
        var imageBytes = bytes

        override fun accessGranted(source: ApprovedImageSource) = access

        override suspend fun candidates(source: ApprovedImageSource, limit: Int) = listOf(
            ImageCandidate(
                "folder",
                "saf_tree",
                "content://one",
                2000,
                2000,
                "image/png",
                "match.png",
                imageBytes.size.toLong(),
                2,
                2
            ),
            ImageCandidate(
                "folder",
                "saf_tree",
                "content://two",
                2000,
                2000,
                "image/jpeg",
                "filtered.jpg",
                4,
                2,
                2
            )
        )

        override suspend fun read(candidate: ImageCandidate, maximumBytes: Int): ByteArray {
            readCount += 1
            if (readFails) throw IllegalStateException("source disappeared")
            return imageBytes
        }
    }
}

private class StaticImageStore(private val value: StoredState) : AgentStore {
    override suspend fun read() = value

    override suspend fun credentials(value: Credentials?) = Unit

    override suspend fun configuration(value: Configuration) = Unit

    override suspend fun lastSuccessfulSync(value: String) = Unit

    override suspend fun localSmsConsent(value: Boolean) = Unit

    override suspend fun uploadStatus(successAt: String?, error: String?) = Unit
}

private class MemoryBinaryOutbox : BinaryOutbox {
    val values = mutableListOf<QueuedImage>()
    private val handled = mutableSetOf<Triple<String, String, String>>()

    override suspend fun enqueue(value: QueuedImage): BinaryEnqueueResult {
        val observation = Triple(value.installationId, value.sourceId, value.contentHash)
        if (values.any { it.contentHash == value.contentHash } || observation in handled) {
            return BinaryEnqueueResult(false, true, 0)
        }
        values += value
        return BinaryEnqueueResult(true, false, 0)
    }

    override suspend fun next() = values.firstOrNull()

    override suspend fun progress(uploadId: String, sessionId: String, uploadedBytes: Int) = Unit

    override suspend fun remove(uploadId: String) {
        values.removeAll { it.uploadId == uploadId }
    }

    override suspend fun complete(uploadId: String) {
        val value = values.single { it.uploadId == uploadId }
        handled += Triple(value.installationId, value.sourceId, value.contentHash)
        remove(uploadId)
    }

    override suspend fun discardAll() = values.clear()

    override suspend fun clearObservations() = handled.clear()

    override suspend fun stats() = BinaryOutboxStats(values.size, 0, 0)
}
