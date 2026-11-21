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

    @Test
    fun bankAppFindsItsNewestReleaseEvenWhenTheLatestHasNoBankFile() {
        val base = "https://github.com/AhmedSayedRepo/relay-releases/releases/download"
        fun release(tag: String, file: String): String {
            val asset = """{"name": "$file", "browser_download_url": "$base/$tag/$file"}"""
            return """{"tag_name": "$tag", "assets": [$asset]}"""
        }
        val body = listOf(
            release("agent-v0.8.4", "relay-receipts.apk"),
            release("agent-v0.8.3-bank", "relay-bank-sms.apk"),
            release("bank-sms", "relay-bank-sms.apk")
        ).joinToString(",", "[", "]")
        assertEquals("0.8.3-bank", newestRelease(body, updateAssetName(smsCapture = true))?.version)
        assertEquals("0.8.4", newestRelease(body, updateAssetName(smsCapture = false))?.version)
    }
}
