package com.relay.operator

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import androidx.core.net.toUri
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

private const val LATEST =
    "https://api.github.com/repos/AhmedSayedRepo/relay-releases/releases/latest"
private const val DOWNLOADS =
    "https://github.com/AhmedSayedRepo/relay-releases/releases/download/"

/** "0.6.10" > "0.6.9"; anything unreadable is never newer. */
internal fun isNewer(latest: String, current: String): Boolean {
    fun parts(value: String) = value.substringBefore('-').split('.').map { it.toIntOrNull() }
    val a = parts(latest)
    val b = parts(current)
    if (a.size != 3 || b.size != 3 || a.any { it == null } || b.any { it == null }) return false
    for (index in 0..2) {
        if (a[index] != b[index]) return a[index]!! > b[index]!!
    }
    return false
}

/**
 * Once per start: when a newer Relay release has a Relay Operator APK, offer
 * it. The download opens in the browser; Android then installs the update.
 */
object UpdateCheck {
    fun run(activity: Activity) {
        Thread {
            val version = try {
                val connection = URL(LATEST).openConnection() as HttpURLConnection
                connection.connectTimeout = 8000
                connection.readTimeout = 8000
                connection.setRequestProperty("Accept", "application/vnd.github+json")
                val body = connection.inputStream.bufferedReader().use { it.readText() }
                connection.disconnect()
                val release = JSONObject(body)
                val tag = release.getString("tag_name").removePrefix("agent-v")
                val assets = release.getJSONArray("assets")
                val hasApp = (0 until assets.length()).any {
                    assets.getJSONObject(it).getString("name") == "relay-operator-$tag.apk"
                }
                tag.takeIf { hasApp && isNewer(it, BuildConfig.VERSION_NAME) }
            } catch (_: Exception) {
                null
            } ?: return@Thread
            activity.runOnUiThread { offer(activity, version) }
        }.start()
    }

    private fun offer(activity: Activity, version: String) {
        if (activity.isFinishing || activity.isDestroyed) return
        AlertDialog.Builder(activity)
            .setTitle(activity.getString(R.string.update_title, version))
            .setMessage(R.string.update_body)
            .setPositiveButton(R.string.update_now) { _, _ ->
                val url = "${DOWNLOADS}agent-v$version/relay-operator-$version.apk"
                try {
                    activity.startActivity(Intent(Intent.ACTION_VIEW, url.toUri()))
                } catch (_: ActivityNotFoundException) {
                    Unit
                }
            }
            .setNegativeButton(R.string.update_later, null)
            .show()
    }
}
