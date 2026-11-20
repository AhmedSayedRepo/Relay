package com.relay.agent.data

import android.content.Context
import androidx.core.content.edit
import java.time.Instant
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Remembers the last time the app crashed and where, so the next health
 * report tells Relay why the app closed. Only the error type, its first
 * words and the code lines: no receipts, SMS or personal data.
 */
object CrashLog {
    private const val PREFS = "relay_crash"
    private const val MAX_FRAMES = 6

    fun install(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            // Written at once: the process ends right after.
            runCatching {
                prefs.edit(commit = true) {
                    putString("at", Instant.now().toString())
                    putString("error", describe(error))
                }
            }
            previous?.uncaughtException(thread, error)
        }
    }

    fun last(context: Context): JsonObject? {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val at = prefs.getString("at", null) ?: return null
        val error = prefs.getString("error", null) ?: return null
        return buildJsonObject {
            put("at", at)
            put("error", error)
        }
    }

    fun describe(error: Throwable): String {
        val root = generateSequence(error) { it.cause }.last()
        val frames = root.stackTrace
            .filter { it.className.startsWith("com.relay") }
            .ifEmpty { root.stackTrace.take(2) }
            .take(MAX_FRAMES)
            .joinToString(" < ") { frame ->
                "${frame.className.substringAfterLast('.')}.${frame.methodName}:${frame.lineNumber}"
            }
        val message = root.message.orEmpty().take(120)
        return "${root.javaClass.simpleName}: $message | $frames".take(500)
    }
}
