package com.relay.agent.data

import android.content.Context
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.DocumentsContract
import androidx.core.net.toUri
import com.relay.agent.domain.ApprovedImageSource
import com.relay.agent.domain.ImageCandidate
import com.relay.agent.domain.ImageSource
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Reads only direct children of a user-selected SAF tree. It never crawls device storage. */
class SafTreeImageSource(private val context: Context) : ImageSource {
    private val resolver = context.contentResolver

    override fun accessGranted(source: ApprovedImageSource): Boolean = try {
        val expected = source.treeUri.toUri()
        resolver.persistedUriPermissions.any { permission ->
            permission.isReadPermission && permission.uri == expected
        }
    } catch (_: Exception) {
        false
    }

    override suspend fun candidates(source: ApprovedImageSource, limit: Int): List<ImageCandidate> =
        withContext(Dispatchers.IO) {
            require(limit in 1..MAX_CANDIDATES)
            if (!accessGranted(source)) return@withContext emptyList()
            val tree = source.treeUri.toUri()
            val children = DocumentsContract.buildChildDocumentsUriUsingTree(
                tree,
                DocumentsContract.getTreeDocumentId(tree)
            )
            val values = mutableListOf<ImageCandidate>()
            resolver.query(children, PROJECTION, null, null, null)?.use { cursor ->
                while (cursor.moveToNext() && values.size < limit) {
                    val documentId = cursor.getString(0) ?: continue
                    val name = cursor.getString(1)
                    val mime = cursor.getString(2) ?: continue
                    val modified = cursor.getLong(3)
                    val size = cursor.getLong(4)
                    if (
                        modified <= source.approvedAtMillis ||
                        mime !in ALLOWED_MIMES ||
                        size !in 1..MAX_IMAGE_BYTES
                    ) {
                        continue
                    }
                    val document = DocumentsContract.buildDocumentUriUsingTree(tree, documentId)
                    val dimensions = dimensions(document) ?: continue
                    val (width, height) = dimensions
                    if (
                        width !in 1..MAX_DIMENSION ||
                        height !in 1..MAX_DIMENSION ||
                        width.toLong() * height > MAX_PIXELS
                    ) {
                        continue
                    }
                    values += ImageCandidate(
                        source.sourceId,
                        "saf_tree",
                        document.toString(),
                        System.currentTimeMillis(),
                        modified.takeIf { it > 0 },
                        mime,
                        name?.takeIf { it.length <= 255 && it.none { char -> char.code < 32 } },
                        size,
                        width,
                        height
                    )
                }
            }
            values
        }

    override suspend fun read(candidate: ImageCandidate, maximumBytes: Int): ByteArray =
        withContext(Dispatchers.IO) {
            require(maximumBytes in 1..MAX_IMAGE_BYTES.toInt())
            resolver.openInputStream(candidate.uri.toUri())?.use { stream ->
                val buffer = ByteArray(16 * 1024)
                val sink = ByteArrayOutputStream(minOf(maximumBytes, 256 * 1024))
                while (sink.size() <= maximumBytes) {
                    val remaining = maximumBytes + 1 - sink.size()
                    val count = stream.read(
                        buffer,
                        0,
                        minOf(buffer.size, remaining)
                    )
                    if (count < 0) break
                    sink.write(buffer, 0, count)
                }
                val output = sink.toByteArray()
                require(output.size <= maximumBytes && output.size.toLong() == candidate.byteSize)
                output
            } ?: throw IllegalStateException("Selected image is no longer available.")
        }

    private fun dimensions(uri: Uri): Pair<Int, Int>? = try {
        resolver.openFileDescriptor(uri, "r")?.use { descriptor ->
            val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFileDescriptor(descriptor.fileDescriptor, null, options)
            if (options.outMimeType !in ALLOWED_MIMES) return null
            options.outWidth to options.outHeight
        }
    } catch (_: Exception) {
        null
    }

    companion object {
        const val MAX_CANDIDATES = 50
        const val MAX_IMAGE_BYTES = 8L * 1024 * 1024
        const val MAX_DIMENSION = 12_000
        const val MAX_PIXELS = 40_000_000L
        val ALLOWED_MIMES = setOf("image/jpeg", "image/png", "image/webp")
        private val PROJECTION = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
            DocumentsContract.Document.COLUMN_SIZE
        )
    }
}
