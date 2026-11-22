@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.core.net.toUri
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import com.relay.agent.BuildConfig
import com.relay.agent.R
import com.relay.agent.RelayApplication
import com.relay.agent.data.KeepConnectedService
import com.relay.agent.presentation.theme.RelayTheme

class MainActivity : ComponentActivity() {
    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(AppLanguage.wrap(newBase))
    }

    override fun onResume() {
        super.onResume()
        // Allowed while the app is on screen: keeps it connected afterwards.
        KeepConnectedService.start(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The header is deep teal in both themes, so status-bar icons stay light.
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT))
        setContent {
            RelayTheme {
                AgentScreen(
                    switchLanguage = {
                        AppLanguage.toggle(this)
                        recreate()
                    }
                )
                UpdatePrompt(BuildConfig.VERSION_NAME)
                PhoneCheckPrompt()
                WhatsNewPrompt(BuildConfig.VERSION_NAME)
            }
        }
    }
}

@Composable
private fun AgentScreen(switchLanguage: () -> Unit, model: AgentViewModel = viewModel()) {
    val status by model.status.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val power = context.getSystemService(PowerManager::class.java)
    var batteryRestricted by remember { mutableStateOf(false) }
    LifecycleResumeEffect(model) {
        model.permissionChanged()
        // Checked again on return from Android's battery question.
        batteryRestricted = !power.isIgnoringBatteryOptimizations(context.packageName)
        onPauseOrDispose {}
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { model.permissionChanged() }
    val app = RelayApplication.from(context)
    val caughtUp: (Int) -> Unit = { count ->
        val text = if (count > 0) {
            context.getString(R.string.catch_up_done, count)
        } else {
            context.getString(R.string.catch_up_none)
        }
        Toast.makeText(context, text, Toast.LENGTH_LONG).show()
    }
    val readSmsLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            model.catchUpSms(caughtUp)
        } else {
            Toast.makeText(context, R.string.catch_up_denied, Toast.LENGTH_LONG).show()
        }
    }
    val folderLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree()
    ) { uri ->
        if (uri != null) {
            try {
                context.contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
                model.approveImageSource(uri.toString())
            } catch (_: SecurityException) {
                model.permissionChanged()
            }
        }
    }
    val extraFolderLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree()
    ) { uri ->
        if (uri != null) {
            try {
                context.contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
                model.addImageFolder(uri.toString())
            } catch (_: SecurityException) {
                model.permissionChanged()
            }
        }
    }
    AgentContent(
        status = status,
        appVersion = BuildConfig.VERSION_NAME,
        switchLanguage = switchLanguage,
        batteryRestricted = batteryRestricted,
        actions = AgentActions(
            enroll = model::enroll,
            sync = model::sync,
            setSmsConsent = model::setSmsConsent,
            requestSmsPermission = { permissionLauncher.launch(Manifest.permission.RECEIVE_SMS) },
            discardSms = model::discardQueuedEvents,
            allowBackground = { askToRunInBackground(context) },
            allowInstaPay = {
                try {
                    context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                } catch (_: ActivityNotFoundException) {
                    Unit // no such settings screen on this phone
                }
            },
            catchUpSms = {
                if (app.readSmsGranted()) {
                    model.catchUpSms(caughtUp)
                } else {
                    readSmsLauncher.launch(Manifest.permission.READ_SMS)
                }
            },
            setImageConsent = model::setImageConsent,
            chooseFolder = { folderLauncher.launch(null) },
            removeFolder = {
                context.contentResolver.persistedUriPermissions.forEach {
                    try {
                        context.contentResolver.releasePersistableUriPermission(
                            it.uri,
                            Intent.FLAG_GRANT_READ_URI_PERMISSION
                        )
                    } catch (_: SecurityException) {
                        Unit
                    }
                }
                model.clearImageSource()
            },
            addFolder = { extraFolderLauncher.launch(null) },
            removeExtraFolder = { treeUri ->
                try {
                    context.contentResolver.releasePersistableUriPermission(
                        treeUri.toUri(),
                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                    )
                } catch (_: SecurityException) {
                    Unit
                }
                model.removeImageFolder(treeUri)
            },
            discardImages = model::discardQueuedImages,
            scanCode = {
                GmsBarcodeScanning.getClient(
                    context,
                    GmsBarcodeScannerOptions.Builder()
                        .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                        .build()
                ).startScan()
                    .addOnSuccessListener { code -> code.rawValue?.let(model::enroll) }
                    .addOnFailureListener {
                        Toast.makeText(context, R.string.scan_unavailable, Toast.LENGTH_LONG).show()
                    }
            }
        )
    )
}

/**
 * Android's own "let this app always run in the background?" question, so
 * nobody has to find the battery menus. Falls back to the list of apps when a
 * phone does not offer the question.
 */
@SuppressLint("BatteryLife")
internal fun askToRunInBackground(context: Context) {
    val ask = Intent(
        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
        "package:${context.packageName}".toUri()
    )
    try {
        context.startActivity(ask)
    } catch (_: ActivityNotFoundException) {
        try {
            context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        } catch (_: ActivityNotFoundException) {
            Unit
        }
    }
}
