package com.relay.agent.presentation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdaterTest {
    @Test
    fun comparesVersionsIgnoringTheBuildSuffix() {
        assertTrue(isNewer("0.6.2-receipts", "0.6.3"))
        assertTrue(isNewer("0.6.9", "0.7.0"))
        assertFalse(isNewer("0.6.2", "0.6.2"))
        assertFalse(isNewer("0.7.0", "0.6.9"))
    }

    @Test
    fun picksOnlyThisAppsFileFromTheRelease() {
        val base =
            "https://github.com/AhmedSayedRepo/relay-releases/releases/download/agent-v0.6.3"
        val body = """
            {"tag_name": "agent-v0.6.3", "assets": [
              {"name": "relay-bank-sms.apk", "browser_download_url": "$base/relay-bank-sms.apk"},
              {"name": "relay-receipts.apk", "browser_download_url": "$base/relay-receipts.apk"}
            ]}
        """.trimIndent()
        val receipts = parseRelease(body, updateAssetName(smsCapture = false))
        assertEquals(AppRelease("0.6.3", "$base/relay-receipts.apk"), receipts)
        assertEquals(
            "$base/relay-bank-sms.apk",
            parseRelease(body, updateAssetName(smsCapture = true))?.downloadUrl
        )
        assertNull(parseRelease("""{"tag_name": "v1.0.0", "assets": []}""", "relay-receipts.apk"))
        val elsewhere = body.replace("https://github.com/", "https://evil.example/")
        assertNull(parseRelease(elsewhere, "relay-receipts.apk"))
    }
}
