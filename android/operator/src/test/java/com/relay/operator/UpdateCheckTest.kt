package com.relay.operator

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateCheckTest {
    @Test
    fun comparesVersionsByNumberNotText() {
        assertTrue(isNewer("0.6.10", "0.6.9"))
        assertTrue(isNewer("0.7.0", "0.6.9"))
        assertFalse(isNewer("0.6.6", "0.6.6"))
        assertFalse(isNewer("0.6.5", "0.6.6"))
        assertFalse(isNewer("garbage", "0.6.6"))
    }
}
