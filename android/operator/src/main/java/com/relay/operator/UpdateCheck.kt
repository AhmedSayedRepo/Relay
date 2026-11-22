package com.relay.operator

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.core.content.FileProvider
import androidx.core.net.toUri
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import org.json.JSONArray

// Recent releases, not just the latest: a release may leave Relay Operator out.
private const val RECENT =
    "https://api.github.com/repos/AhmedSayedRepo/relay-releases/releases?per_page=15"
private const val DOWNLOADS =
    "https://github.com/AhmedSayedRepo/relay-releases/releases/download/"
private const val ASSET = "relay-operator.apk"

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

private data class Release(val version: String, val url: String)

/**
 * Once per start, the same way as the other Relay apps: when a newer release
 * has a Relay Operator file, offer it, download it inside the app with its
 * progress, then hand it to Android's installer. Says so when the update
 * server can't be reached, instead of staying silent.
 */
object UpdateCheck {
    fun run(activity: Activity) {
        Thread {
            val result = runCatching { newest() }
            activity.runOnUiThread {
                if (activity.isFinishing || activity.isDestroyed) return@runOnUiThread
                result
                    .onSuccess { release ->
                        if (release != null) offer(activity, release)
                    }
                    .onFailure { checkFailed(activity) }
            }
        }.start()
    }

    private fun newest(): Release? {
        val connection = URL(RECENT).openConnection() as HttpURLConnection
        connection.connectTimeout = 8000
        connection.readTimeout = 8000
        connection.setRequestProperty("Accept", "application/vnd.github+json")
        val body = try {
            check(connection.responseCode in 200..299) { "HTTP ${connection.responseCode}" }
            connection.inputStream.bufferedReader().use { it.readText() }
        } finally {
            connection.disconnect()
        }
        val releases = JSONArray(body)
        var best: Release? = null
        for (index in 0 until releases.length()) {
            val release = releases.getJSONObject(index)
            val tag = release.optString("tag_name")
            if (!tag.startsWith("agent-v")) continue
            val version = tag.removePrefix("agent-v").substringBefore('-')
            val assets = release.optJSONArray("assets") ?: continue
            val url = (0 until assets.length())
                .map { assets.getJSONObject(it) }
                .firstOrNull { it.optString("name") == ASSET }
                ?.optString("browser_download_url")
                ?.takeIf { it.startsWith(DOWNLOADS) } ?: continue
            if (best == null || isNewer(version, best.version)) best = Release(version, url)
        }
        return best?.takeIf { isNewer(it.version, BuildConfig.VERSION_NAME) }
    }

    private fun checkFailed(activity: Activity) {
        AlertDialog.Builder(activity)
            .setTitle(R.string.update_check_failed_title)
            .setMessage(R.string.update_check_failed_body)
            .setPositiveButton(R.string.retry) { _, _ -> run(activity) }
            .setNegativeButton(R.string.update_later, null)
            .show()
    }

    private fun offer(activity: Activity, release: Release) {
        val density = activity.resources.displayMetrics.density
        fun dp(value: Int) = (value * density).toInt()
        val icon = ImageView(activity).apply {
            setImageDrawable(activity.applicationInfo.loadIcon(activity.packageManager))
            layoutParams = LinearLayout.LayoutParams(dp(48), dp(48)).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                bottomMargin = dp(12)
            }
        }
        val message = TextView(activity).apply {
            text = activity.getString(
                R.string.update_body,
                release.version,
                BuildConfig.VERSION_NAME
            )
            textSize = 15f
        }
        val bar = ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(16) }
        }
        val status = TextView(activity).apply {
            textSize = 13f
            visibility = View.GONE
        }
        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(20), dp(24), dp(4))
            addView(icon)
            addView(message)
            addView(bar)
            addView(status)
        }
        val dialog = AlertDialog.Builder(activity)
            .setTitle(R.string.update_title)
            .setView(content)
            .setPositiveButton(R.string.update_now, null)
            .setNegativeButton(R.string.update_later, null)
            .setCancelable(false)
            .show()
        var cancelled = false
        dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setOnClickListener {
            cancelled = true
            dialog.dismiss()
        }
        val update = dialog.getButton(AlertDialog.BUTTON_POSITIVE)
        update.setOnClickListener {
            if (!activity.packageManager.canRequestPackageInstalls()) {
                message.setText(R.string.update_allow_installs)
                open(
                    activity,
                    Intent(
                        Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        "package:${activity.packageName}".toUri()
                    )
                )
                return@setOnClickListener
            }
            update.isEnabled = false
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setText(R.string.update_cancel)
            bar.visibility = View.VISIBLE
            bar.isIndeterminate = true
            status.visibility = View.VISIBLE
            Thread {
                val apk = runCatching {
                    download(activity, release) { done, total ->
                        activity.runOnUiThread {
                            if (total > 0) {
                                bar.isIndeterminate = false
                                bar.progress = (done * 100 / total).toInt()
                            }
                            status.text = activity.getString(
                                R.string.update_downloading,
                                if (total > 0) (done * 100 / total).toInt() else 0,
                                megabytes(done),
                                megabytes(total.coerceAtLeast(done))
                            )
                        }
                        !cancelled
                    }
                }.getOrNull()
                activity.runOnUiThread {
                    if (cancelled || activity.isFinishing) return@runOnUiThread
                    if (apk == null) {
                        status.setText(R.string.update_failed)
                        bar.visibility = View.GONE
                        update.isEnabled = true
                        dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setText(R.string.update_later)
                        return@runOnUiThread
                    }
                    status.setText(R.string.update_installing)
                    bar.isIndeterminate = true
                    install(activity, apk)
                    dialog.dismiss()
                }
            }.start()
        }
    }

    private fun megabytes(bytes: Long): String =
        String.format(Locale.getDefault(), "%.1f", bytes / 1_048_576.0)

    /** Saves the APK; [progress] returns false to stop. Null when it didn't finish. */
    private fun download(
        activity: Activity,
        release: Release,
        progress: (Long, Long) -> Boolean
    ): File? {
        val folder = File(activity.cacheDir, "updates").apply { mkdirs() }
        val target = File(folder, "relay-operator-update.apk")
        val connection = URL(release.url).openConnection() as HttpURLConnection
        connection.connectTimeout = 15000
        connection.readTimeout = 30000
        try {
            if (connection.responseCode !in 200..299) return null
            val total = connection.contentLengthLong
            var done = 0L
            connection.inputStream.use { input ->
                target.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        done += read
                        if (!progress(done, total)) return null
                    }
                }
            }
            return target.takeIf { total <= 0 || done == total }
        } finally {
            connection.disconnect()
        }
    }

    private fun install(activity: Activity, apk: File) {
        val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.updates", apk)
        open(
            activity,
            Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    private fun open(activity: Activity, intent: Intent) {
        try {
            activity.startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            Unit
        }
    }
}
