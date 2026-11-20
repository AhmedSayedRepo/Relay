package com.relay.agent.data

import org.junit.Assert.assertTrue
import org.junit.Test

class CrashLogTest {
    @Test
    fun describesTheRootErrorAndOnlyRelayCodeLines() {
        val cause = IllegalStateException("folder gone")
        cause.stackTrace = arrayOf(
            StackTraceElement("android.content.ContentResolver", "query", "X.java", 10),
            StackTraceElement("com.relay.agent.data.SafTreeImageSource", "recent", "S.kt", 55)
        )
        val text = CrashLog.describe(RuntimeException("wrapper", cause))
        assertTrue(text.startsWith("IllegalStateException: folder gone"))
        assertTrue(text.contains("SafTreeImageSource.recent:55"))
        assertTrue(!text.contains("ContentResolver"))
    }
}
