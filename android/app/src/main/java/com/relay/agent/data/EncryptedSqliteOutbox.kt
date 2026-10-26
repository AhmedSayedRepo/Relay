package com.relay.agent.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.relay.agent.domain.EnqueueResult
import com.relay.agent.domain.EventOutbox
import com.relay.agent.domain.GenericEvent
import com.relay.agent.domain.OutboxEntry
import com.relay.agent.domain.OutboxStats
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class EncryptedSqliteOutbox(
    context: Context,
    private val cipher: CredentialCipher,
    private val codec: EventCodec = EventCodec(),
    private val nowMillis: () -> Long = System::currentTimeMillis,
    databaseName: String = "relay-outbox.db"
) : SQLiteOpenHelper(
    context,
    File(context.noBackupFilesDir, databaseName).absolutePath,
    null,
    1
),
    EventOutbox {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE outbox (
                event_id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                retry_count INTEGER NOT NULL CHECK(retry_count >= 0),
                encrypted_size INTEGER NOT NULL CHECK(encrypted_size > 0),
                ciphertext BLOB NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX ix_outbox_fifo ON outbox(created_at, event_id)")
        db.execSQL(
            "CREATE TABLE outbox_meta (id INTEGER PRIMARY KEY CHECK(id = 1), dropped INTEGER NOT NULL)"
        )
        db.execSQL("INSERT INTO outbox_meta(id, dropped) VALUES(1, 0)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    override suspend fun enqueue(event: GenericEvent): EnqueueResult = io {
        val encrypted = cipher.encrypt(codec.encode(event))
        if (encrypted.size > MAX_SINGLE_ENCRYPTED_BYTES) return@io EnqueueResult(false, 0)
        val db = writableDatabase
        var dropped = 0
        db.beginTransaction()
        try {
            purgeExpired(db)
            if (exists(db, event.eventId)) {
                db.setTransactionSuccessful()
                return@io EnqueueResult(false, 0)
            }
            while (count(db) >= MAX_EVENTS || bytes(db) + encrypted.size > MAX_ENCRYPTED_BYTES) {
                if (!dropOldest(db)) return@io EnqueueResult(false, dropped)
                dropped += 1
            }
            val values = ContentValues().apply {
                put("event_id", event.eventId)
                put("created_at", nowMillis())
                put("retry_count", 0)
                put("encrypted_size", encrypted.size)
                put("ciphertext", encrypted)
            }
            val inserted = db.insertWithOnConflict(
                "outbox",
                null,
                values,
                SQLiteDatabase.CONFLICT_IGNORE
            )
            if (dropped > 0) incrementDropped(db, dropped)
            db.setTransactionSuccessful()
            EnqueueResult(inserted != -1L, dropped)
        } finally {
            db.endTransaction()
        }
    }

    override suspend fun batch(limit: Int): List<OutboxEntry> = io {
        require(limit in 1..MAX_BATCH)
        val db = writableDatabase
        purgeExpired(db)
        val entries = mutableListOf<OutboxEntry>()
        val corrupted = mutableListOf<String>()
        db.query(
            "outbox",
            arrayOf("event_id", "retry_count", "ciphertext"),
            null,
            null,
            null,
            null,
            "created_at ASC, event_id ASC",
            limit.toString()
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val id = cursor.getString(0)
                try {
                    entries +=
                        OutboxEntry(
                            codec.decode(cipher.decrypt(cursor.getBlob(2))),
                            cursor.getInt(1)
                        )
                } catch (_: Exception) {
                    corrupted += id
                }
            }
        }
        if (corrupted.isNotEmpty()) {
            deleteIds(db, corrupted.toSet())
            incrementDropped(db, corrupted.size)
        }
        entries
    }

    override suspend fun remove(eventIds: Set<String>) = ioUnit {
        deleteIds(writableDatabase, eventIds)
    }

    override suspend fun incrementRetries(eventIds: Set<String>) = ioUnit {
        if (eventIds.isNotEmpty()) {
            val placeholders = eventIds.joinToString(",") { "?" }
            writableDatabase.execSQL(
                "UPDATE outbox SET retry_count = retry_count + 1 WHERE event_id IN ($placeholders)",
                eventIds.toTypedArray()
            )
        }
    }

    override suspend fun discardAll() = ioUnit {
        val db = writableDatabase
        val removed = db.delete("outbox", null, null)
        if (removed > 0) incrementDropped(db, removed)
    }

    override suspend fun stats(): OutboxStats = io {
        val db = writableDatabase
        purgeExpired(db)
        val dropped = db.rawQuery("SELECT dropped FROM outbox_meta WHERE id = 1", null).use {
            require(it.moveToFirst())
            it.getLong(0)
        }
        OutboxStats(count(db), bytes(db), dropped)
    }

    private fun purgeExpired(db: SQLiteDatabase) {
        val removed = db.delete(
            "outbox",
            "created_at < ?",
            arrayOf((nowMillis() - MAX_RETENTION_MILLIS).toString())
        )
        if (removed > 0) incrementDropped(db, removed)
    }

    private fun dropOldest(db: SQLiteDatabase): Boolean {
        val id = db.rawQuery(
            "SELECT event_id FROM outbox ORDER BY created_at ASC, event_id ASC LIMIT 1",
            null
        ).use { if (it.moveToFirst()) it.getString(0) else null } ?: return false
        return db.delete("outbox", "event_id = ?", arrayOf(id)) == 1
    }

    private fun deleteIds(db: SQLiteDatabase, ids: Set<String>) {
        if (ids.isEmpty()) return
        val placeholders = ids.joinToString(",") { "?" }
        db.delete("outbox", "event_id IN ($placeholders)", ids.toTypedArray())
    }

    private fun count(db: SQLiteDatabase): Int = db.rawQuery(
        "SELECT COUNT(*) FROM outbox",
        null
    ).use {
        it.moveToFirst()
        it.getInt(0)
    }

    private fun bytes(db: SQLiteDatabase): Long = db.rawQuery(
        "SELECT COALESCE(SUM(encrypted_size), 0) FROM outbox",
        null
    ).use {
        it.moveToFirst()
        it.getLong(0)
    }

    private fun exists(db: SQLiteDatabase, eventId: String): Boolean = db.rawQuery(
        "SELECT 1 FROM outbox WHERE event_id = ? LIMIT 1",
        arrayOf(eventId)
    ).use { it.moveToFirst() }

    private fun incrementDropped(db: SQLiteDatabase, amount: Int) {
        db.execSQL("UPDATE outbox_meta SET dropped = dropped + ? WHERE id = 1", arrayOf(amount))
    }

    private suspend fun <T> io(block: () -> T): T = withContext(Dispatchers.IO) {
        synchronized(this@EncryptedSqliteOutbox) { block() }
    }

    private suspend fun ioUnit(block: () -> Unit) = io(block)

    companion object {
        const val MAX_EVENTS = 500
        const val MAX_ENCRYPTED_BYTES = 2L * 1024 * 1024
        const val MAX_SINGLE_ENCRYPTED_BYTES = 12 * 1024
        const val MAX_BATCH = 20
        const val MAX_RETENTION_MILLIS = 7L * 24 * 60 * 60 * 1000
    }
}
