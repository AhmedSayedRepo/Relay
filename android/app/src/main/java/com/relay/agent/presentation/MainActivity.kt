@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation

import android.Manifest
import android.content.Intent
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.relay.agent.BuildConfig
import com.relay.agent.R
import com.relay.agent.domain.AgentStatus
import com.relay.agent.domain.ConnectionState
import com.relay.agent.domain.EnrollmentState

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        enableEdgeToEdge()
        setContent { MaterialTheme { AgentScreen() } }
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
    Scaffold(modifier = Modifier.fillMaxSize()) { padding ->
        Column(
            modifier = Modifier.fillMaxSize().padding(
                padding
            ).padding(24.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Text(stringResource(R.string.app_name), style = MaterialTheme.typography.headlineLarge)
            Text(
                stringResource(R.string.agent_description),
                style = MaterialTheme.typography.bodyLarge
            )
            if (status.enrollment == EnrollmentState.LOADING) {
                CircularProgressIndicator()
            } else {
                StatusDetails(status)
                PrivacyDetails(
                    status,
                    model::setSmsConsent,
                    { permissionLauncher.launch(Manifest.permission.RECEIVE_SMS) },
                    model::discardQueuedEvents,
                    model::setImageConsent,
                    { folderLauncher.launch(null) },
                    {
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
                    model::discardQueuedImages
                )
                if (status.enrollment == EnrollmentState.ENROLLED) {
                    Button(
                        onClick = model::sync,
                        enabled = status.connection != ConnectionState.SYNCING,
                        modifier = Modifier.fillMaxWidth(),
                        contentPadding = PaddingValues(16.dp)
                    ) { Text(stringResource(R.string.manual_sync)) }
                } else {
                    EnrollmentForm(status.enrollment == EnrollmentState.ENROLLING, model::enroll)
                }
            }
            status.message?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        }
    }
}

@Composable
private fun PrivacyDetails(
    status: AgentStatus,
    setConsent: (Boolean) -> Unit,
    requestPermission: () -> Unit,
    discard: () -> Unit,
    setImageConsent: (Boolean) -> Unit,
    selectImageSource: () -> Unit,
    clearImageSource: () -> Unit,
    discardImages: () -> Unit
) {
    Text(stringResource(R.string.privacy_title), style = MaterialTheme.typography.titleLarge)
    Text(stringResource(R.string.sms_consent, status.localSmsConsent.toYesNo()))
    Switch(checked = status.localSmsConsent, onCheckedChange = setConsent)
    Text(stringResource(R.string.sms_permission, status.smsPermissionGranted.toYesNo()))
    if (status.localSmsConsent && !status.smsPermissionGranted) {
        Button(onClick = requestPermission, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.request_sms_permission))
        }
    }
    Text(stringResource(R.string.server_sms, status.serverSmsEnabled.toYesNo()))
    Text(stringResource(R.string.effective_sms, status.effectiveSmsCollection.toYesNo()))
    Text(stringResource(R.string.queued_events, status.queuedEventCount))
    Text(stringResource(R.string.dropped_events, status.droppedEventCount))
    Text(
        stringResource(
            R.string.last_upload,
            status.lastSuccessfulUpload ?: stringResource(R.string.not_yet)
        )
    )
    status.lastUploadError?.let { Text(stringResource(R.string.last_upload_error, it)) }
    if (status.queuedEventCount > 0) {
        Button(onClick = discard, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.discard_queued_events))
        }
    }
    Text(stringResource(R.string.images_title), style = MaterialTheme.typography.titleMedium)
    Text(stringResource(R.string.image_consent, status.localImageConsent.toYesNo()))
    Switch(checked = status.localImageConsent, onCheckedChange = setImageConsent)
    Text(stringResource(R.string.image_access, status.imageAccessGranted.toYesNo()))
    Text(stringResource(R.string.server_images, status.serverImagesEnabled.toYesNo()))
    Text(stringResource(R.string.effective_images, status.effectiveImageCollection.toYesNo()))
    Text(
        stringResource(
            R.string.approved_image_source,
            status.approvedImageSourceId ?: stringResource(R.string.none_selected)
        )
    )
    if (status.localImageConsent && status.serverImagesEnabled) {
        Button(onClick = selectImageSource, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.select_image_folder))
        }
    }
    if (status.approvedImageSourceId != null) {
        Button(onClick = clearImageSource, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.remove_image_folder))
        }
    }
    Text(stringResource(R.string.queued_images, status.queuedImageCount))
    Text(stringResource(R.string.queued_image_bytes, status.queuedImageBytes))
    Text(stringResource(R.string.dropped_images, status.droppedImageCount))
    Text(
        stringResource(
            R.string.last_image_upload,
            status.lastSuccessfulImageUpload ?: stringResource(R.string.not_yet)
        )
    )
    status.lastImageUploadError?.let {
        Text(stringResource(R.string.last_image_upload_error, it))
    }
    if (status.queuedImageCount > 0) {
        Button(onClick = discardImages, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.discard_queued_images))
        }
    }
}

private fun Boolean.toYesNo(): String = if (this) "yes" else "no"

@Composable
private fun EnrollmentForm(busy: Boolean, submit: (String) -> Unit) {
    // Intentionally not rememberSaveable: secrets are never written to saved instance state.
    var challenge by remember { mutableStateOf("") }
    Text(stringResource(R.string.enrollment_help))
    OutlinedTextField(
        value = challenge,
        onValueChange = { if (it.length <= 1024) challenge = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text(stringResource(R.string.enrollment_token)) },
        singleLine = true,
        enabled = !busy,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(
            keyboardType = KeyboardType.Password,
            autoCorrectEnabled = false
        )
    )
    Button(
        onClick = {
            val submitted = challenge
            challenge = ""
            submit(submitted)
        },
        enabled = !busy && challenge.isNotBlank(),
        modifier = Modifier.fillMaxWidth(),
        contentPadding = PaddingValues(16.dp)
    ) { Text(stringResource(R.string.enroll)) }
    if (busy) CircularProgressIndicator()
}

@Composable
private fun StatusDetails(status: AgentStatus) {
    Text(stringResource(R.string.connection, status.connection.name.lowercase().replace('_', ' ')))
    Text(stringResource(R.string.enrollment, status.enrollment.name.lowercase().replace('_', ' ')))
    status.deviceName?.let { Text(stringResource(R.string.device_name, it)) }
    status.deviceId?.let { Text(stringResource(R.string.device_id, it)) }
    Text(stringResource(R.string.installation_id, status.installationId))
    Text(stringResource(R.string.configuration_version, status.configurationVersion))
    Text(stringResource(R.string.app_version, BuildConfig.VERSION_NAME))
    Text(
        stringResource(
            R.string.last_sync,
            status.lastSuccessfulSync ?: stringResource(R.string.not_yet)
        )
    )
}
