@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation

import android.Manifest
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.WindowManager
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
import com.relay.agent.BuildConfig
import com.relay.agent.presentation.theme.RelayTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        // The header is deep teal in both themes, so status-bar icons stay light.
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT))
        setContent { RelayTheme { AgentScreen() } }
    }
}

@Composable
private fun AgentScreen(model: AgentViewModel = viewModel()) {
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
            discardImages = model::discardQueuedImages
        )
    )
}
