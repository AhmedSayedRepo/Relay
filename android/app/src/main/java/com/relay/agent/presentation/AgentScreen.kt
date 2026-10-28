@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation

import android.text.format.DateUtils
import android.text.format.Formatter
import androidx.annotation.StringRes
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsBottomHeight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.relay.agent.R
import com.relay.agent.domain.AgentStatus
import com.relay.agent.domain.ConnectionState
import com.relay.agent.domain.EnrollmentState
import com.relay.agent.presentation.theme.LocalRelayColors
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

/** Everything the screen can ask for; the activity wires these to Android and the view model. */
data class AgentActions(
    val enroll: (String) -> Unit = {},
    val sync: () -> Unit = {},
    val setSmsConsent: (Boolean) -> Unit = {},
    val requestSmsPermission: () -> Unit = {},
    val discardSms: () -> Unit = {},
    val setImageConsent: (Boolean) -> Unit = {},
    val chooseFolder: () -> Unit = {},
    val removeFolder: () -> Unit = {},
    val discardImages: () -> Unit = {}
)

enum class CollectorState { ACTIVE, PAUSED, OFF }

/** The one condition that currently stops a collector, in the order a person can fix it. */
enum class Blocker {
    NONE,
    LOCAL_OFF,
    SMS_PERMISSION,
    SERVER_OFF,
    FOLDER,
    FOLDER_LOST,
    WAITING_SYNC
}

fun smsBlocker(status: AgentStatus): Blocker = when {
    status.effectiveSmsCollection -> Blocker.NONE
    !status.localSmsConsent -> Blocker.LOCAL_OFF
    !status.smsPermissionGranted -> Blocker.SMS_PERMISSION
    !status.serverSmsEnabled -> Blocker.SERVER_OFF
    else -> Blocker.WAITING_SYNC
}

fun imageBlocker(status: AgentStatus): Blocker = when {
    status.effectiveImageCollection -> Blocker.NONE
    !status.localImageConsent -> Blocker.LOCAL_OFF
    !status.serverImagesEnabled -> Blocker.SERVER_OFF
    status.approvedImageSourceId == null -> Blocker.FOLDER
    !status.imageAccessGranted -> Blocker.FOLDER_LOST
    else -> Blocker.WAITING_SYNC
}

fun Blocker.state(): CollectorState = when (this) {
    Blocker.NONE -> CollectorState.ACTIVE
    Blocker.LOCAL_OFF -> CollectorState.OFF
    else -> CollectorState.PAUSED
}

@StringRes
private fun Blocker.reason(): Int = when (this) {
    Blocker.NONE, Blocker.WAITING_SYNC -> R.string.reason_waiting_sync
    Blocker.LOCAL_OFF -> R.string.reason_local_off
    Blocker.SMS_PERMISSION -> R.string.reason_sms_permission
    Blocker.SERVER_OFF -> R.string.reason_server_off
    Blocker.FOLDER -> R.string.reason_folder
    Blocker.FOLDER_LOST -> R.string.reason_folder_lost
}

private val knownMessages = mapOf(
    "Local storage could not be read. Restart the app and try again." to R.string.msg_storage,
    "Enter a valid enrollment token." to R.string.msg_invalid_token,
    "Enrolled successfully. Sync to retrieve configuration." to R.string.msg_enrolled,
    "Configuration is up to date." to R.string.msg_config_current,
    "Connection unavailable. Last valid configuration remains available." to R.string.msg_offline,
    "Sync was rejected. Last valid configuration remains available." to R.string.msg_rejected,
    "Event upload completed." to R.string.msg_upload_done,
    "Event upload will retry." to R.string.msg_upload_retry,
    "Invalid queued events were discarded." to R.string.msg_invalid_discarded,
    "Local SMS consent enabled." to R.string.msg_sms_on,
    "Local SMS consent disabled." to R.string.msg_sms_off,
    "Local image consent enabled. Select a folder to approve access." to R.string.msg_images_on,
    "Local image consent disabled and queued images removed." to R.string.msg_images_off,
    "Image source approved for new images only." to R.string.msg_folder_approved,
    "Image source access removed locally." to R.string.msg_folder_removed,
    "Queued events discarded locally." to R.string.msg_events_discarded,
    "Enrollment is required. Contact your administrator." to R.string.msg_enrollment_required
)

private fun relative(value: String?, now: Instant): String? = value?.let {
    runCatching {
        DateUtils.getRelativeTimeSpanString(
            Instant.parse(it).toEpochMilli(),
            now.toEpochMilli(),
            DateUtils.MINUTE_IN_MILLIS
        ).toString()
    }.getOrDefault(it)
}

private fun absolute(value: String?): String? = value?.let {
    runCatching {
        DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
            .withZone(ZoneId.systemDefault())
            .format(Instant.parse(it))
    }.getOrDefault(it)
}

@Composable
fun AgentContent(
    status: AgentStatus,
    actions: AgentActions,
    now: Instant = Instant.now(),
    appVersion: String = ""
) {
    val snackbar = remember { SnackbarHostState() }
    val message = status.message?.let { raw ->
        knownMessages[raw]?.let { stringResource(it) } ?: raw
    }
    LaunchedEffect(message) { if (message != null) snackbar.showSnackbar(message) }
    val enrolled = status.enrollment == EnrollmentState.ENROLLED
    val syncing = status.connection == ConnectionState.SYNCING
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            if (enrolled) {
                ExtendedFloatingActionButton(
                    onClick = { if (!syncing) actions.sync() },
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = MaterialTheme.colorScheme.onPrimary,
                    icon = {
                        if (syncing) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.onPrimary
                            )
                        } else {
                            Icon(Icons.Filled.Refresh, contentDescription = null)
                        }
                    },
                    text = {
                        Text(stringResource(if (syncing) R.string.syncing else R.string.sync_now))
                    }
                )
            }
        },
        contentWindowInsets = WindowInsets(0)
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .background(MaterialTheme.colorScheme.background)
                .verticalScroll(rememberScrollState())
        ) {
            Header(status, now)
            Column(
                Modifier.padding(horizontal = 16.dp, vertical = 20.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp)
            ) {
                when (status.enrollment) {
                    EnrollmentState.LOADING -> Unit
                    EnrollmentState.ENROLLED -> {
                        Collectors(status, actions, now)
                        DeviceDetails(status, appVersion)
                    }
                    else -> EnrollmentCard(
                        status.enrollment == EnrollmentState.ENROLLING,
                        actions.enroll
                    )
                }
                // Keeps the last row clear of the floating button and the gesture bar.
                Spacer(Modifier.height(if (enrolled) 72.dp else 0.dp))
                Spacer(Modifier.windowInsetsBottomHeight(WindowInsets.navigationBars))
            }
        }
    }
}

@Composable
private fun Header(status: AgentStatus, now: Instant) {
    val relay = LocalRelayColors.current
    Column(
        Modifier
            .fillMaxWidth()
            .background(relay.header)
            .statusBarsPadding()
            .padding(start = 20.dp, end = 20.dp, top = 16.dp, bottom = 20.dp)
    ) {
        Text(
            stringResource(R.string.app_name),
            style = MaterialTheme.typography.titleLarge,
            color = relay.onHeader,
            modifier = Modifier.semantics { heading() }
        )
        when (status.enrollment) {
            EnrollmentState.LOADING -> {
                Spacer(Modifier.height(20.dp))
                LinearProgressIndicator(
                    modifier = Modifier.fillMaxWidth(),
                    color = relay.onHeader,
                    trackColor = relay.headerRule
                )
            }
            EnrollmentState.ENROLLED -> {
                Spacer(Modifier.height(20.dp))
                Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                    Count(
                        status.queuedEventCount.toLong(),
                        R.string.count_sms_waiting,
                        Modifier.weight(1f)
                    )
                    HeaderRule()
                    Count(
                        status.queuedImageCount.toLong(),
                        R.string.count_images_waiting,
                        Modifier.weight(1f)
                    )
                    HeaderRule()
                    Count(
                        status.droppedEventCount + status.droppedImageCount,
                        R.string.count_dropped,
                        Modifier.weight(1f),
                        waitingTone = false
                    )
                }
                Spacer(Modifier.height(16.dp))
                Text(
                    syncLine(status, now),
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (status.connection == ConnectionState.ERROR) {
                        relay.waiting
                    } else {
                        relay.onHeaderSoft
                    }
                )
            }
            else -> {
                Spacer(Modifier.height(8.dp))
                Text(
                    stringResource(R.string.not_enrolled_header),
                    style = MaterialTheme.typography.bodyLarge,
                    color = relay.onHeaderSoft
                )
            }
        }
    }
}

@Composable
private fun syncLine(status: AgentStatus, now: Instant): String = when (status.connection) {
    ConnectionState.SYNCING -> stringResource(R.string.syncing)
    ConnectionState.OFFLINE -> stringResource(R.string.state_offline)
    ConnectionState.ERROR -> stringResource(R.string.state_error)
    else ->
        relative(status.lastSuccessfulSync, now)
            ?.let { stringResource(R.string.synced_ago, it) }
            ?: stringResource(R.string.never_synced)
}

@Composable
private fun HeaderRule() {
    Box(
        Modifier
            .padding(horizontal = 12.dp)
            .width(1.dp)
            .fillMaxHeight()
            .background(LocalRelayColors.current.headerRule)
    )
}

@Composable
private fun Count(
    value: Long,
    @StringRes label: Int,
    modifier: Modifier,
    waitingTone: Boolean = true
) {
    val relay = LocalRelayColors.current
    Column(modifier) {
        Text(
            value.toString(),
            style = MaterialTheme.typography.displayMedium,
            color = if (waitingTone && value > 0) relay.waiting else relay.onHeader,
            maxLines = 1
        )
        Text(
            stringResource(label),
            style = MaterialTheme.typography.labelLarge,
            color = relay.onHeaderSoft,
            maxLines = 2
        )
    }
}

@Composable
private fun SectionTitle(@StringRes text: Int) {
    Text(
        stringResource(text),
        style = MaterialTheme.typography.titleMedium,
        color = MaterialTheme.colorScheme.onSurface,
        modifier = Modifier.padding(horizontal = 4.dp).semantics { heading() }
    )
}

@Composable
private fun Collectors(status: AgentStatus, actions: AgentActions, now: Instant) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionTitle(R.string.collection_title)
        Surface(
            shape = MaterialTheme.shapes.large,
            color = MaterialTheme.colorScheme.surfaceContainer,
            tonalElevation = 1.dp,
            shadowElevation = 1.dp
        ) {
            Column {
                val sms = smsBlocker(status)
                CollectorBlock(
                    title = R.string.sms_title,
                    blocker = sms,
                    activeText = stringResource(R.string.sms_active),
                    consent = status.localSmsConsent,
                    consentLabel = R.string.allow_sms_switch,
                    onConsent = actions.setSmsConsent,
                    waiting = status.queuedEventCount.toLong(),
                    dropped = status.droppedEventCount,
                    lastSent = relative(status.lastSuccessfulUpload, now),
                    error = status.lastUploadError,
                    discardLabel = R.string.discard_sms,
                    onDiscard = actions.discardSms
                ) {
                    if (sms == Blocker.SMS_PERMISSION) {
                        Button(onClick = actions.requestSmsPermission) {
                            Text(stringResource(R.string.allow_sms_access))
                        }
                    }
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                val images = imageBlocker(status)
                val folder =
                    status.approvedImageSourceId ?: stringResource(R.string.the_chosen_folder)
                CollectorBlock(
                    title = R.string.images_title,
                    blocker = images,
                    activeText = stringResource(R.string.images_active, folder),
                    consent = status.localImageConsent,
                    consentLabel = R.string.allow_images_switch,
                    onConsent = actions.setImageConsent,
                    waiting = status.queuedImageCount.toLong(),
                    dropped = status.droppedImageCount,
                    lastSent = relative(status.lastSuccessfulImageUpload, now),
                    error = status.lastImageUploadError,
                    discardLabel = R.string.discard_images,
                    onDiscard = actions.discardImages,
                    queuedBytes = status.queuedImageBytes
                ) {
                    if (images == Blocker.FOLDER || images == Blocker.FOLDER_LOST) {
                        Button(onClick = actions.chooseFolder) {
                            Text(stringResource(R.string.choose_folder))
                        }
                    } else if (status.approvedImageSourceId != null && status.localImageConsent) {
                        TextButton(onClick = actions.chooseFolder) {
                            Text(stringResource(R.string.change_folder))
                        }
                        TextButton(onClick = actions.removeFolder) {
                            Text(stringResource(R.string.remove_folder))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun CollectorBlock(
    @StringRes title: Int,
    blocker: Blocker,
    activeText: String,
    consent: Boolean,
    @StringRes consentLabel: Int,
    onConsent: (Boolean) -> Unit,
    waiting: Long,
    dropped: Long,
    lastSent: String?,
    error: String?,
    @StringRes discardLabel: Int,
    onDiscard: () -> Unit,
    queuedBytes: Long = 0,
    fix: @Composable () -> Unit
) {
    val context = LocalContext.current
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                stringResource(title),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f)
            )
            StateChip(blocker.state())
        }
        Text(
            if (blocker == Blocker.NONE) activeText else stringResource(blocker.reason()),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) { fix() }
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                stringResource(consentLabel),
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.weight(1f).padding(end = 12.dp)
            )
            Switch(checked = consent, onCheckedChange = onConsent)
        }
        val details = buildList {
            add(stringResource(R.string.waiting_summary, waiting, dropped))
            add(
                lastSent?.let { stringResource(R.string.last_sent, it) }
                    ?: stringResource(R.string.never_sent)
            )
            if (queuedBytes > 0) {
                val size = Formatter.formatShortFileSize(context, queuedBytes)
                add(stringResource(R.string.queued_size, size))
            }
        }
        Text(
            details.joinToString("  ·  "),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        error?.let {
            Text(
                stringResource(R.string.upload_problem, it),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis
            )
        }
        if (waiting > 0) {
            OutlinedButton(
                onClick = onDiscard,
                contentPadding = PaddingValues(horizontal = 16.dp)
            ) {
                Text(stringResource(discardLabel))
            }
        }
    }
}

@Composable
private fun StateChip(state: CollectorState) {
    val relay = LocalRelayColors.current
    val (container: Color, content: Color, label: Int) = when (state) {
        CollectorState.ACTIVE -> Triple(relay.successContainer, relay.success, R.string.chip_active)
        CollectorState.PAUSED -> Triple(relay.warningContainer, relay.warning, R.string.chip_paused)
        CollectorState.OFF -> Triple(
            MaterialTheme.colorScheme.surfaceContainerHigh,
            MaterialTheme.colorScheme.onSurfaceVariant,
            R.string.chip_off
        )
    }
    Surface(color = container, contentColor = content, shape = CircleShape) {
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            Box(Modifier.size(8.dp).background(content, CircleShape))
            Text(stringResource(label), style = MaterialTheme.typography.labelLarge)
        }
    }
}

@Composable
private fun EnrollmentCard(busy: Boolean, submit: (String) -> Unit) {
    // Intentionally not rememberSaveable: secrets are never written to saved instance state.
    var challenge by remember { mutableStateOf("") }
    Surface(
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceContainer,
        shadowElevation = 1.dp
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text(
                stringResource(R.string.enroll_title),
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.semantics { heading() }
            )
            Text(
                stringResource(R.string.enrollment_help),
                style = MaterialTheme.typography.bodyLarge
            )
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
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)
            ) {
                if (busy) {
                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                } else {
                    Text(stringResource(R.string.enroll))
                }
            }
            Text(
                stringResource(R.string.privacy_promise),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

@Composable
private fun DeviceDetails(status: AgentStatus, appVersion: String) {
    var open by rememberSaveable { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(Modifier.weight(1f)) { SectionTitle(R.string.device_title) }
            TextButton(onClick = { open = !open }) {
                Text(stringResource(if (open) R.string.hide_details else R.string.show_details))
                Icon(
                    if (open) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
                    contentDescription = null
                )
            }
        }
        AnimatedVisibility(open) {
            Surface(
                shape = MaterialTheme.shapes.large,
                color = MaterialTheme.colorScheme.surfaceContainer
            ) {
                Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
                    status.deviceName?.let { Detail(R.string.device_name, it) }
                    status.deviceId?.let { Detail(R.string.device_id, it) }
                    Detail(R.string.installation_id, status.installationId)
                    Detail(R.string.configuration_version, status.configurationVersion.toString())
                    if (appVersion.isNotEmpty()) Detail(R.string.app_version, appVersion)
                    Detail(
                        R.string.last_sync,
                        absolute(status.lastSuccessfulSync) ?: stringResource(R.string.not_yet)
                    )
                }
            }
        }
    }
}

@Composable
private fun Detail(@StringRes label: Int, value: String) {
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Text(
            stringResource(label),
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}
