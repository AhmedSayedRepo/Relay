@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.content.edit
import com.relay.agent.R

/** One app version's changes, in English and Arabic. */
data class ReleaseNote(val en: List<String>, val ar: List<String>)

/** What changed in each app version. Add an entry before each Android release. */
object ReleaseNotes {
    val byVersion: Map<String, ReleaseNote> = mapOf(
        "0.6.5" to ReleaseNote(
            en = listOf(
                "Watch more than one folder: add Bluetooth or any other folder next to WhatsApp.",
                "New receipt images are sent within seconds instead of every 15 minutes.",
                "This 'What's new' window after each update."
            ),
            ar = listOf(
                "مراقبة أكثر من مجلد: أضف البلوتوث أو أي مجلد آخر بجانب الواتساب.",
                "صور الإيصالات الجديدة تُرسل خلال ثوانٍ بدلاً من كل 15 دقيقة.",
                "نافذة «الجديد» هذه بعد كل تحديث."
            )
        )
    )

    /** "0.6.5-receipts" and "0.6.5" share one entry. */
    fun forVersion(versionName: String): ReleaseNote? = byVersion[versionName.substringBefore('-')]
}

private const val PREFS = "relay_whats_new"
private const val SEEN = "seen_version"

/** Shown once after the app is updated to a version that has notes. */
@Composable
fun WhatsNewPrompt(versionName: String) {
    val context = LocalContext.current
    val note = ReleaseNotes.forVersion(versionName) ?: return
    val prefs = remember { context.getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    var open by remember { mutableStateOf(prefs.getString(SEEN, null) != versionName) }
    if (!open) return
    val close = {
        prefs.edit { putString(SEEN, versionName) }
        open = false
    }
    val items = if (AppLanguage.current(context) == "ar") note.ar else note.en
    AlertDialog(
        onDismissRequest = close,
        title = {
            Text(stringResource(R.string.whats_new_title, versionName.substringBefore('-')))
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items.forEach { Text("• $it") }
            }
        },
        confirmButton = {
            TextButton(onClick = close) { Text(stringResource(R.string.whats_new_ok)) }
        }
    )
}
