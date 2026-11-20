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
        "0.8.5" to ReleaseNote(
            en = listOf("Sync no longer closes the app when a photo is large."),
            ar = listOf("المزامنة مبقتش تقفل التطبيق لما تكون الصورة كبيرة.")
        ),
        "0.8.3" to ReleaseNote(
            en = listOf(
                "If the app closes by itself, it tells Relay where, so it can be fixed.",
                "One button to stop battery saving from holding the app back."
            ),
            ar = listOf(
                "لو التطبيق قفل لوحده، بيبلّغ Relay بالسبب علشان يتصلّح.",
                "زرار واحد يمنع توفير البطارية من إنه يقيّد التطبيق."
            )
        ),
        "0.8.2" to ReleaseNote(
            en = listOf(
                "Relay Bank SMS also tells Relay why it was quiet, " +
                    "and if SMS access was removed."
            ),
            ar = listOf(
                "Relay Bank SMS كمان بيبلّغ Relay ليه كان ساكت، " +
                    "ولو إذن الرسايل اتشال."
            )
        ),
        "0.8.1" to ReleaseNote(
            en = listOf(
                "The app tells Relay why it was quiet (phone off, app closed by " +
                    "Android, battery saving), so the office sees the reason."
            ),
            ar = listOf(
                "التطبيق بيبلّغ Relay ليه كان ساكت (الموبايل مقفول، أندرويد قفل " +
                    "التطبيق، توفير البطارية)، فالمكتب يعرف السبب."
            )
        ),
        "0.7.1" to ReleaseNote(
            en = listOf(
                "Photos are sent one after another, so the server no longer says \"busy\".",
                "Sending problems are now explained in words."
            ),
            ar = listOf(
                "الصور بتتبعت ورا بعض، فالسيرفر مش هيقول «مشغول» تاني.",
                "مشاكل الإرسال بقت مكتوبة بكلام مفهوم."
            )
        ),
        "0.7.0" to ReleaseNote(
            en = listOf(
                "All waiting photos are sent in one go, not one every 15 minutes.",
                "Each sync rechecks the last 3 days: photos missed while offline are still sent.",
                "The waiting list keeps up to 300 photos instead of dropping the oldest after 20."
            ),
            ar = listOf(
                "كل الصور المنتظرة بتتبعت مرة واحدة، مش صورة كل ربع ساعة.",
                "كل مزامنة بتراجع آخر 3 أيام، فالصور اللي فاتت وقت انقطاع النت بتتبعت.",
                "قائمة الانتظار بتشيل لحد 300 صورة بدل ما ترمي الأقدم بعد 20."
            )
        ),
        "0.6.7" to ReleaseNote(
            en = listOf(
                "New \"Which folder should I choose?\" button: " +
                    "the WhatsApp and Bluetooth folder paths."
            ),
            ar = listOf(
                "جديد: زرار «أختار أنهي مجلد؟» تحت المجلدات " +
                    "يوضح مسار مجلد الواتساب والبلوتوث."
            )
        ),
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
