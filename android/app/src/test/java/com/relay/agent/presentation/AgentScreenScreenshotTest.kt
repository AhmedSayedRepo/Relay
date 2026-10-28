package com.relay.agent.presentation

import android.graphics.Bitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import com.relay.agent.domain.AgentStatus
import com.relay.agent.domain.ConnectionState
import com.relay.agent.domain.EnrollmentState
import com.relay.agent.presentation.theme.RelayTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File
import java.time.Instant

/**
 * Renders the agent screen in its key states and writes PNGs for review.
 * All data is synthetic. Output: app/build/screenshots (CI copies it to android/screenshots).
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(qualifiers = "w411dp-h891dp-xxhdpi")
class AgentScreenScreenshotTest {
    @get:Rule val compose = createComposeRule()

    private val now: Instant = Instant.parse("2026-10-28T10:15:00Z")

    private val working = AgentStatus(
        installationId = "8c1f2b9e-5d1a-4f0e-9a77-2f3c6e1d4b50",
        deviceId = "3e7a9c1d-0b2f-4c6e-8d5a-1f9b7e3c2a40",
        deviceName = "Front desk phone",
        enrollment = EnrollmentState.ENROLLED,
        connection = ConnectionState.CONNECTED,
        configurationVersion = 4,
        lastSuccessfulSync = "2026-10-28T10:12:00Z",
        localSmsConsent = true,
        smsPermissionGranted = true,
        serverSmsEnabled = true,
        effectiveSmsCollection = true,
        queuedEventCount = 2,
        lastSuccessfulUpload = "2026-10-28T10:12:00Z",
        localImageConsent = true,
        imageAccessGranted = true,
        serverImagesEnabled = true,
        effectiveImageCollection = true,
        approvedImageSourceId = "Screenshots",
        lastSuccessfulImageUpload = "2026-10-28T09:58:00Z"
    )

    private val needsAttention = working.copy(
        smsPermissionGranted = false,
        effectiveSmsCollection = false,
        queuedEventCount = 0,
        imageAccessGranted = false,
        approvedImageSourceId = null,
        effectiveImageCollection = false,
        queuedImageCount = 3,
        queuedImageBytes = 1_840_000,
        droppedImageCount = 1,
        connection = ConnectionState.OFFLINE
    )

    private val notEnrolled = AgentStatus(
        installationId = "8c1f2b9e-5d1a-4f0e-9a77-2f3c6e1d4b50",
        enrollment = EnrollmentState.REQUIRED
    )

    private fun shoot(name: String, status: AgentStatus, dark: Boolean = false) {
        compose.setContent {
            RelayTheme(dark = dark) {
                AgentContent(status, AgentActions(), now = now, appVersion = "0.4.0")
            }
        }
        compose.waitForIdle()
        val bitmap = compose.onRoot().captureToImage().asAndroidBitmap()
        val dir = File("build/screenshots").apply { mkdirs() }
        File(dir, "$name.png").outputStream().use {
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
        }
    }

    @Test
    fun workingLight() {
        shoot("working-light", working)
        compose.onNodeWithText("Synced 3 minutes ago").assertExists()
        assertEquals(2, compose.onAllNodesWithTextCount("Active"))
    }

    @Test
    fun workingDark() = shoot("working-dark", working, dark = true)

    @Test
    fun needsAttentionLight() {
        shoot("needs-attention-light", needsAttention)
        compose.onNodeWithText("Allow SMS access").assertExists()
        compose.onNodeWithText("Choose folder").assertExists()
        compose.onNodeWithText("Offline · will retry").assertExists()
    }

    @Test
    fun notEnrolledLight() {
        shoot("not-enrolled-light", notEnrolled)
        compose.onNodeWithText("Connect phone").assertExists()
    }

    @Test
    @Config(qualifiers = "+ar")
    fun workingArabic() {
        shoot("working-arabic", working)
        compose.onNodeWithText("مزامنة الآن").assertExists()
    }

    @Test
    @Config(qualifiers = "+ar")
    fun needsAttentionArabicDark() =
        shoot("needs-attention-arabic-dark", needsAttention, dark = true)

    private fun androidx.compose.ui.test.junit4.ComposeContentTestRule.onAllNodesWithTextCount(
        text: String
    ): Int = onAllNodes(androidx.compose.ui.test.hasText(text)).fetchSemanticsNodes().size
}
