@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation

import android.Manifest
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import com.relay.agent.BuildConfig
import com.relay.agent.R
import com.relay.agent.presentation.theme.RelayTheme

class MainActivity : ComponentActivity() {
    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(AppLanguage.wrap(newBase))
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
            }
        }
    }
}

@Composable
private fun AgentScreen(switchLanguage: () -> Unit, model: AgentViewModel = viewModel()) {
    val status by model.status.collectAsStateWithLifecycle()
    LifecycleResumeEffect(model) {
        model.permissionChanged()
        onPauseOrDispose {}
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { model.permissionChanged() }
    val context = LocalContext.current
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
    AgentContent(
        status = status,
        appVersion = BuildConfig.VERSION_NAME,
        switchLanguage = switchLanguage,
        actions = AgentActions(
            enroll = model::enroll,
            sync = model::sync,
            setSmsConsent = model::setSmsConsent,
            requestSmsPermission = { permissionLauncher.launch(Manifest.permission.RECEIVE_SMS) },
            discardSms = model::discardQueuedEvents,
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
