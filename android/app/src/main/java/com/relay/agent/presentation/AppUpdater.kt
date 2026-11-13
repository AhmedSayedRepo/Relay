@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation

import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.content.FileProvider
import androidx.core.net.toUri
import com.relay.agent.BuildConfig
import com.relay.agent.R
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request

private const val LATEST_RELEASE =
    "https://api.github.com/repos/AhmedSayedRepo/relay-releases/releases/latest"

private const val RELEASE_DOWNLOADS =
    "https://github.com/AhmedSayedRepo/relay-releases/releases/download/"

data class AppRelease(val version: String, val downloadUrl: String)

/** True when [latest] (e.g. "0.6.3") is a newer x.y.z than [current]. */
fun isNewer(current: String, latest: String): Boolean {
    fun parts(value: String): List<Int> {
        val pieces = value.trim().removePrefix("v").split(".", "-").take(3)
        val numbers = pieces.map { it.toIntOrNull() ?: 0 }
        return numbers + List(3 - numbers.size) { 0 }
    }
    val a = parts(current)
    val b = parts(latest)
    for (index in 0 until 3) {
        if (b[index] != a[index]) return b[index] > a[index]
    }
    return false
}

/** This build's file in a release: each app updates only to its own kind. */
fun updateAssetName(smsCapture: Boolean): String =
    if (smsCapture) "relay-bank-sms.apk" else "relay-receipts.apk"

fun parseRelease(body: String, assetName: String): AppRelease? {
    val root = Json.parseToJsonElement(body).jsonObject
    val tag = root["tag_name"]?.jsonPrimitive?.contentOrNull ?: return null
    val version = tag.substring(tag.indexOfFirst { it.isDigit() }.coerceAtLeast(0))
    val asset = root["assets"]?.jsonArray
        ?.map { it.jsonObject }
        ?.firstOrNull { it["name"]?.jsonPrimitive?.contentOrNull == assetName }
        ?: return null
    val url = asset["browser_download_url"]?.jsonPrimitive?.contentOrNull
    // Only ever download from Relay's own release page.
    val trusted = url != null && url.startsWith(RELEASE_DOWNLOADS)
    return if (url != null && trusted) AppRelease(version, url) else null
}

private val http = OkHttpClient()

private suspend fun latestRelease(): AppRelease? = withContext(Dispatchers.IO) {
    runCatching {
        val request = Request.Builder()
            .url(LATEST_RELEASE)
            .header("Accept", "application/vnd.github+json")
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return@use null
            val body = response.body?.string().orEmpty()
            parseRelease(body, updateAssetName(BuildConfig.SMS_CAPTURE))
        }
    }.getOrNull()
}

private suspend fun download(
    context: Context,
    release: AppRelease,
    progress: (Float) -> Unit
): File? = withContext(Dispatchers.IO) {
    runCatching {
        val folder = File(context.cacheDir, "updates").apply { mkdirs() }
        val target = File(folder, "relay-update.apk")
        val request = Request.Builder().url(release.downloadUrl).build()
        http.newCall(request).execute().use { response ->
            val body = response.body
            if (!response.isSuccessful || body == null) return@use null
            val total = body.contentLength().takeIf { it > 0 } ?: -1L
            body.byteStream().use { input ->
                target.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var done = 0L
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        done += read
                        if (total > 0) progress(done.toFloat() / total)
                    }
                }
            }
            target
        }
    }.getOrNull()
}

private fun canInstall(context: Context): Boolean =
    context.packageManager.canRequestPackageInstalls()

private fun install(context: Context, apk: File) {
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.updates", apk)
    val intent = Intent(Intent.ACTION_VIEW)
        .setDataAndType(uri, "application/vnd.android.package-archive")
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    context.startActivity(intent)
}

/**
 * On launch, checks for a newer Relay of this kind. If there is one, asks to
 * update, downloads it and opens Android's installer (Android always asks the
 * person to tap Install for apps from outside Play). The old version closes
 * during the install; a notification then opens the new one.
 */
@Composable
fun UpdatePrompt(currentVersion: String) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var release by remember { mutableStateOf<AppRelease?>(null) }
    var progress by remember { mutableStateOf<Float?>(null) }
    var failed by remember { mutableStateOf(false) }
    var dismissed by remember { mutableStateOf(false) }
    LaunchedEffect(currentVersion) {
        latestRelease()?.takeIf { isNewer(currentVersion, it.version) }?.let { release = it }
    }
    val found = release
    if (found == null || dismissed) return
    AlertDialog(
        onDismissRequest = { if (progress == null) dismissed = true },
        title = { Text(stringResource(R.string.update_title)) },
        text = {
            when {
                progress != null -> LinearProgressIndicator(progress = { progress ?: 0f })
                failed -> Text(stringResource(R.string.update_failed))
                !canInstall(context) -> Text(stringResource(R.string.update_allow_installs))
                else -> Text(stringResource(R.string.update_body, found.version, currentVersion))
            }
        },
        confirmButton = {
            TextButton(
                enabled = progress == null,
                onClick = {
                    if (!canInstall(context)) {
                        context.startActivity(
                            Intent(
                                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                "package:${context.packageName}".toUri()
                            )
                        )
                        return@TextButton
                    }
                    failed = false
                    progress = 0f
                    scope.launch {
                        val apk = download(context, found) { value -> progress = value }
                        progress = null
                        if (apk == null) failed = true else install(context, apk)
                    }
                }
            ) { Text(stringResource(R.string.update_now)) }
        },
        dismissButton = {
            if (progress == null) {
                TextButton(onClick = { dismissed = true }) {
                    Text(stringResource(R.string.update_later))
                }
            }
        }
    )
}
