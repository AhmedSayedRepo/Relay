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

/**
 * Reads a user-selected SAF tree and its subfolders (WhatsApp keeps photos
 * taken with its own camera in "Sent"). It never reaches outside the folders
 * the user chose; hidden folders (thumbnails, trash) are skipped.
 */
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
        recent(source, 0L, limit)

    /**
     * Every photo in the folder and its subfolders changed since [sinceMillis]
     * (and after the folder was approved), newest first. The folder's own
     * order is not by date, so all rows are read (name, type, time and size
     * only) and sorted before the newest [limit] are opened for their
     * dimensions.
     */
    override suspend fun recent(
        source: ApprovedImageSource,
        sinceMillis: Long,
        limit: Int
    ): List<ImageCandidate> = withContext(Dispatchers.IO) {
        require(limit in 1..MAX_CANDIDATES)
        if (!accessGranted(source)) return@withContext emptyList()
        val tree = source.treeUri.toUri()
        val after = maxOf(source.approvedAtMillis, sinceMillis)
        val rows = mutableListOf<Row>()
        // Breadth-first through the subfolders, a few levels deep and with a
        // cap on rows read, so a huge folder cannot stall a sync.
        val folders = ArrayDeque(listOf(DocumentsContract.getTreeDocumentId(tree) to 0))
        var seen = 0
        while (folders.isNotEmpty() && seen < MAX_ROWS) {
            val (folderId, depth) = folders.removeFirst()
            val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, folderId)
            resolver.query(children, PROJECTION, null, null, null)?.use { cursor ->
                while (cursor.moveToNext() && seen < MAX_ROWS) {
                    seen += 1
                    val documentId = cursor.getString(0) ?: continue
                    val name = cursor.getString(1)
                    val mime = cursor.getString(2) ?: continue
                    if (mime == DocumentsContract.Document.MIME_TYPE_DIR) {
                        if (depth < MAX_DEPTH && name?.startsWith(".") != true) {
                            folders.addLast(documentId to depth + 1)
                        }
                        continue
                    }
                    val modified = cursor.getLong(3)
                    val size = cursor.getLong(4)
                    if (modified <= after || mime !in ALLOWED_MIMES) continue
                    if (size !in 1..MAX_IMAGE_BYTES || name?.startsWith(".") == true) continue
                    rows += Row(documentId, name, mime, modified, size)
                }
            }
        }
        val values = mutableListOf<ImageCandidate>()
        for (row in rows.sortedByDescending { it.modified }) {
            if (values.size >= limit) break
            val document = DocumentsContract.buildDocumentUriUsingTree(tree, row.documentId)
            val (width, height) = dimensions(document) ?: continue
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
                row.modified.takeIf { it > 0 },
                row.mime,
                row.name?.takeIf { it.length <= 255 && it.none { char -> char.code < 32 } },
                row.size,
                width,
                height
            )
        }
        values
    }

    private data class Row(
        val documentId: String,
        val name: String?,
        val mime: String,
        val modified: Long,
        val size: Long
    )

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
        const val MAX_CANDIDATES = 300
        const val MAX_DEPTH = 3
        const val MAX_ROWS = 20_000
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
