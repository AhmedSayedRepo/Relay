package com.relay.agent.presentation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class ReleaseNotesTest {
    @Test
    fun bothAppsShareOneEntryAndEveryNoteIsInBothLanguages() {
        assertNotNull(ReleaseNotes.forVersion("0.6.5-receipts"))
        assertNotNull(ReleaseNotes.forVersion("0.6.5"))
        assertNull(ReleaseNotes.forVersion("0.0.1"))
        ReleaseNotes.byVersion.values.forEach { assertEquals(it.en.size, it.ar.size) }
    }
}
