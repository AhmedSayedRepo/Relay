package com.relay.agent.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.relay.agent.domain.BinaryEnqueueResult
import com.relay.agent.domain.BinaryOutbox
import com.relay.agent.domain.BinaryOutboxStats
import com.relay.agent.domain.QueuedImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * A bounded encrypted FIFO plus bounded successful-collection observations.
 * Observations contain installation/source/hash metadata only and survive process restarts.
 */
class EncryptedBinaryOutbox(
    context: Context,
    private val cipher: CredentialCipher,
    private val codec: BinaryImageCodec = BinaryImageCodec(),
    private val nowMillis: () -> Long = System::currentTimeMillis,
    databaseName: String = "relay-binary-outbox.db",
) : SQLiteOpenHelper(
        context,
        File(context.noBackupFilesDir, databaseName).absolutePath,
        null,
        2,
    ),
    BinaryOutbox {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE binary_outbox (
                upload_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                encrypted_size INTEGER NOT NULL CHECK(encrypted_size > 0),
                ciphertext BLOB NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL("CREATE INDEX ix_binary_outbox_fifo ON binary_outbox(created_at, upload_id)")
        db.execSQL(
            "CREATE TABLE binary_outbox_meta (id INTEGER PRIMARY KEY CHECK(id = 1), dropped INTEGER NOT NULL)",
        )
        db.execSQL("INSERT INTO binary_outbox_meta(id, dropped) VALUES(1, 0)")
        createObservationTable(db)
    }

    override fun onUpgrade(
        db: SQLiteDatabase,
        oldVersion: Int,
        newVersion: Int,
    ) {
        if (oldVersion < 2) createObservationTable(db)
    }

    override suspend fun enqueue(value: QueuedImage): BinaryEnqueueResult =
        io {
            val encrypted = cipher.encrypt(codec.encode(value))
            if (encrypted.size > MAX_SINGLE_ENCRYPTED_BYTES) {
                return@io BinaryEnqueueResult(false, false, 0)
            }
            val db = writableDatabase
            var dropped = 0
            db.beginTransaction()
            try {
                purgeExpired(db)
                if (existsHash(db, value.contentHash) || existsObservation(db, value)) {
                    db.setTransactionSuccessful()
                    return@io BinaryEnqueueResult(false, true, 0)
                }
                while (count(db) >= MAX_IMAGES || bytes(db) + encrypted.size > MAX_ENCRYPTED_BYTES) {
                    if (!dropOldest(db)) return@io BinaryEnqueueResult(false, false, dropped)
                    dropped += 1
                }
                val values =
                    ContentValues().apply {
                        put("upload_id", value.uploadId)
                        put("content_hash", value.contentHash)
                        put("created_at", nowMillis())
                        put("encrypted_size", encrypted.size)
                        put("ciphertext", encrypted)
                    }
                val inserted =
                    db.insertWithOnConflict(
                        "binary_outbox",
                        null,
                        values,
                        SQLiteDatabase.CONFLICT_IGNORE,
                    )
                if (dropped > 0) incrementDropped(db, dropped)
                db.setTransactionSuccessful()
                BinaryEnqueueResult(inserted != -1L, false, dropped)
            } finally {
                db.endTransaction()
            }
        }

    override suspend fun next(): QueuedImage? =
        io {
            val db = writableDatabase
            purgeExpired(db)
            db
                .query(
                    "binary_outbox",
                    arrayOf("upload_id", "ciphertext"),
                    null,
                    null,
                    null,
                    null,
                    "created_at ASC, upload_id ASC",
                    "1",
                ).use { cursor ->
                    if (!cursor.moveToFirst()) return@io null
                    val id = cursor.getString(0)
                    try {
                        codec.decode(cipher.decrypt(cursor.getBlob(1)))
                    } catch (_: Exception) {
                        db.delete("binary_outbox", "upload_id = ?", arrayOf(id))
                        incrementDropped(db, 1)
                        null
                    }
                }
        }

    override suspend fun progress(
        uploadId: String,
        sessionId: String,
        uploadedBytes: Int,
    ) = ioUnit {
        val current = record(writableDatabase, uploadId) ?: return@ioUnit
        val updated = current.copy(sessionId = sessionId, uploadedBytes = uploadedBytes)
        val encrypted = cipher.encrypt(codec.encode(updated))
        require(encrypted.size <= MAX_SINGLE_ENCRYPTED_BYTES)
        val values =
            ContentValues().apply {
                put("encrypted_size", encrypted.size)
                put("ciphertext", encrypted)
            }
        val updatedRows =
            writableDatabase.update(
                "binary_outbox",
                values,
                "upload_id = ?",
                arrayOf(uploadId),
            )
        require(updatedRows == 1)
    }

    override suspend fun remove(uploadId: String) =
        ioUnit {
            writableDatabase.delete("binary_outbox", "upload_id = ?", arrayOf(uploadId))
        }

    override suspend fun complete(uploadId: String) =
        ioUnit {
            val db = writableDatabase
            db.beginTransaction()
            try {
                purgeExpired(db)
                val value = record(db, uploadId)
                if (value != null) {
                    val observation =
                        ContentValues().apply {
                            put("installation_id", value.installationId)
                            put("source_id", value.sourceId)
                            put("content_hash", value.contentHash)
                            put("completed_at", nowMillis())
                        }
                    db.insertWithOnConflict(
                        "binary_observations",
                        null,
                        observation,
                        SQLiteDatabase.CONFLICT_REPLACE,
                    )
                    db.delete("binary_outbox", "upload_id = ?", arrayOf(uploadId))
                    trimObservations(db)
                }
                db.setTransactionSuccessful()
            } finally {
                db.endTransaction()
            }
        }

    override suspend fun discardAll() =
        ioUnit {
            val db = writableDatabase
            val removed = db.delete("binary_outbox", null, null)
            if (removed > 0) incrementDropped(db, removed)
        }

    override suspend fun clearObservations() =
        ioUnit {
            writableDatabase.delete("binary_observations", null, null)
        }

    override suspend fun stats(): BinaryOutboxStats =
        io {
            val db = writableDatabase
            purgeExpired(db)
            val dropped =
                db.rawQuery("SELECT dropped FROM binary_outbox_meta WHERE id = 1", null).use {
                    require(it.moveToFirst())
                    it.getLong(0)
                }
            BinaryOutboxStats(count(db), bytes(db), dropped)
        }

    private fun record(
        db: SQLiteDatabase,
        uploadId: String,
    ): QueuedImage? =
        db
            .query(
                "binary_outbox",
                arrayOf("ciphertext"),
                "upload_id = ?",
                arrayOf(uploadId),
                null,
                null,
                null,
                "1",
            ).use { if (it.moveToFirst()) codec.decode(cipher.decrypt(it.getBlob(0))) else null }

    private fun purgeExpired(db: SQLiteDatabase) {
        val removed =
            db.delete(
                "binary_outbox",
                "created_at < ?",
                arrayOf((nowMillis() - MAX_RETENTION_MILLIS).toString()),
            )
        if (removed > 0) incrementDropped(db, removed)
        db.delete(
            "binary_observations",
            "completed_at < ?",
            arrayOf((nowMillis() - MAX_OBSERVATION_RETENTION_MILLIS).toString()),
        )
    }

    private fun dropOldest(db: SQLiteDatabase): Boolean {
        val id =
            db
                .rawQuery(
                    "SELECT upload_id FROM binary_outbox ORDER BY created_at ASC, upload_id ASC LIMIT 1",
                    null,
                ).use { if (it.moveToFirst()) it.getString(0) else null } ?: return false
        return db.delete("binary_outbox", "upload_id = ?", arrayOf(id)) == 1
    }

    private fun existsHash(
        db: SQLiteDatabase,
        hash: String,
    ): Boolean =
        db
            .rawQuery(
                "SELECT 1 FROM binary_outbox WHERE content_hash = ? LIMIT 1",
                arrayOf(hash),
            ).use { it.moveToFirst() }

    private fun existsObservation(
        db: SQLiteDatabase,
        value: QueuedImage,
    ): Boolean =
        db
            .rawQuery(
                """
                SELECT 1 FROM binary_observations
                WHERE installation_id = ? AND source_id = ? AND content_hash = ? LIMIT 1
                """.trimIndent(),
                arrayOf(value.installationId, value.sourceId, value.contentHash),
            ).use { it.moveToFirst() }

    private fun trimObservations(db: SQLiteDatabase) {
        val excess = observationCount(db) - MAX_OBSERVATIONS
        if (excess <= 0) return
        db.execSQL(
            """
            DELETE FROM binary_observations WHERE rowid IN (
                SELECT rowid FROM binary_observations
                ORDER BY completed_at ASC, installation_id ASC, source_id ASC, content_hash ASC
                LIMIT ?
            )
            """.trimIndent(),
            arrayOf(excess),
        )
    }

    private fun observationCount(db: SQLiteDatabase): Int =
        db
            .rawQuery(
                "SELECT COUNT(*) FROM binary_observations",
                null,
            ).use {
                it.moveToFirst()
                it.getInt(0)
            }

    private fun createObservationTable(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS binary_observations (
                installation_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                completed_at INTEGER NOT NULL,
                PRIMARY KEY (installation_id, source_id, content_hash)
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE INDEX IF NOT EXISTS ix_binary_observations_retention
            ON binary_observations(completed_at)
            """.trimIndent(),
        )
    }

    private fun count(db: SQLiteDatabase): Int =
        db
            .rawQuery(
                "SELECT COUNT(*) FROM binary_outbox",
                null,
            ).use {
                it.moveToFirst()
                it.getInt(0)
            }

    private fun bytes(db: SQLiteDatabase): Long =
        db
            .rawQuery(
                "SELECT COALESCE(SUM(encrypted_size), 0) FROM binary_outbox",
                null,
            ).use {
                it.moveToFirst()
                it.getLong(0)
            }

    private fun incrementDropped(
        db: SQLiteDatabase,
        amount: Int,
    ) {
        db.execSQL(
            "UPDATE binary_outbox_meta SET dropped = dropped + ? WHERE id = 1",
            arrayOf(amount),
        )
    }

    private suspend fun <T> io(block: () -> T): T =
        withContext(Dispatchers.IO) {
            synchronized(this@EncryptedBinaryOutbox) { block() }
        }

    private suspend fun ioUnit(block: () -> Unit) = io(block)

    companion object {
        const val MAX_IMAGES = 20
        const val MAX_ENCRYPTED_BYTES = 32L * 1024 * 1024
        const val MAX_SINGLE_ENCRYPTED_BYTES = 8 * 1024 * 1024 + 4096
        const val MAX_RETENTION_MILLIS = 7L * 24 * 60 * 60 * 1000
        const val MAX_OBSERVATIONS = 500
        const val MAX_OBSERVATION_RETENTION_MILLIS = 180L * 24 * 60 * 60 * 1000
    }
}
