package com.relay.operator

import android.app.Activity
import android.content.ContentValues
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Base64
import android.webkit.JavascriptInterface
import android.widget.Toast
import java.io.File

/**
 * WebViews cannot download the portal's Excel exports (they are made in the
 * page), so the page hands the file here and it is saved to Downloads.
 */
class FileBridge(private val activity: Activity) {
    @JavascriptInterface
    fun saveFile(name: String, mime: String, base64: String): Boolean {
        val safeName = name.replace(Regex("[^A-Za-z0-9._-]"), "_")
            .take(80)
            .ifBlank { "relay-export.xlsx" }
        val saved = try {
            val bytes = Base64.decode(base64, Base64.DEFAULT)
            require(bytes.size <= MAX_BYTES)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, safeName)
                    put(MediaStore.Downloads.MIME_TYPE, mime.take(100))
                }
                val resolver = activity.contentResolver
                val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: error("no download entry")
                resolver.openOutputStream(uri)?.use { it.write(bytes) } ?: error("no stream")
            } else {
                val folder = activity.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
                    ?: error("no folder")
                File(folder, safeName).writeBytes(bytes)
            }
            true
        } catch (_: Exception) {
            false
        }
        activity.runOnUiThread {
            val text = if (saved) {
                activity.getString(R.string.saved_download, safeName)
            } else {
                activity.getString(R.string.save_failed)
            }
            Toast.makeText(activity, text, Toast.LENGTH_LONG).show()
        }
        return saved
    }

    private companion object {
        const val MAX_BYTES = 20 * 1024 * 1024
    }
}
