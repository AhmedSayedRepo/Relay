package com.relay.agent.data

import com.relay.agent.domain.GenericEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test

class EventCodecTest {
    private val codec = EventCodec()

    @Test
    fun roundTripsGenericEventAndItsSafeStringNeverExposesPayload() {
        val event = event("sensitive body")
        val encoded = codec.encode(event)
        assertEquals(event, codec.decode(encoded))
        assertFalse(event.toString().contains(event.body))
    }

    @Test
    fun rejectsOversizedAndStructurallyUnexpectedPlaintext() {
        assertThrows(IllegalArgumentException::class.java) {
            codec.encode(event("x".repeat(EventCodec.MAX_PLAINTEXT_BYTES)))
        }
        assertThrows(IllegalArgumentException::class.java) {
            codec.decode("""{"event_id":"id","unexpected":"value"}""".toByteArray())
        }
    }

    private fun event(body: String) = GenericEvent(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "sms",
        1,
        3,
        "2024-01-01T00:00:00Z",
        "2024-01-01T00:00:01Z",
        "Sender",
        body,
        "rule"
    )
}
