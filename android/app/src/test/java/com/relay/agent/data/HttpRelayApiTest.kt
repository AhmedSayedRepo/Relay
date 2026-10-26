package com.relay.agent.data

import com.relay.agent.domain.ApiFailure
import com.relay.agent.domain.Credentials
import com.relay.agent.domain.GenericEvent
import java.util.UUID
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class HttpRelayApiTest {
    private lateinit var server: MockWebServer
    private lateinit var api: HttpRelayApi

    @Before
    fun start() {
        server = MockWebServer()
        server.start()
        api = HttpRelayApi(server.url("/").toString(), "0.3.0", true)
    }

    @After
    fun stop() = server.shutdown()

    @Test
    fun sendsEnrollmentContractWithoutAutomaticRedirects() = runTest {
        val installation = UUID.randomUUID().toString()
        server.enqueue(MockResponse().setResponseCode(201).setBody(tokenBody(installation)))
        val value = api.enroll("one-time-secret", installation)
        assertEquals(installation, value.installationId)
        val request = server.takeRequest()
        assertEquals("/api/v1/device/enroll", request.path)
        assertEquals("POST", request.method)
        val body = request.body.readUtf8()
        assertTrue(body.contains("\"challenge\":\"one-time-secret\""))
        assertTrue(body.contains("\"platform\":\"android\""))
        assertTrue(body.contains("\"app_version\":\"0.3.0\""))
    }

    @Test
    fun configurationSupportsNotModifiedAndBearerAuth() = runTest {
        server.enqueue(MockResponse().setResponseCode(304))
        assertNull(api.configuration("access", 2))
        val request = server.takeRequest()
        assertEquals("/api/v1/device/configuration?known_version=2", request.path)
        assertEquals("Bearer access", request.getHeader("Authorization"))
    }

    @Test
    fun doesNotFollowRedirectOrRetryServerFailure() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(307).addHeader("Location", server.url("/elsewhere"))
        )
        try {
            api.heartbeat("access", 1)
            error("Expected ApiFailure")
        } catch (error: ApiFailure) {
            assertEquals(307, error.statusCode)
        }
        assertEquals(1, server.requestCount)
    }

    @Test
    fun rejectsUntrustedCleartextOrigins() {
        assertThrows(IllegalArgumentException::class.java) {
            HttpRelayApi("http://example.com/", "0.3.0", true)
        }
        assertThrows(IllegalArgumentException::class.java) {
            HttpRelayApi("https://user:pass@example.com/", "0.3.0", false)
        }
    }

    @Test
    fun uploadsGenericBatchAndParsesBoundedPerEventResults() = runTest {
        server.enqueue(
            MockResponse().setBody(
                """{"results":[{"event_id":"event-1","status":"accepted"},""" +
                    """{"event_id":"event-2","status":"duplicate"}]}"""
            )
        )
        val results = api.uploadEvents("device-access", listOf(event("event-1"), event("event-2")))
        assertEquals(listOf("accepted", "duplicate"), results.map { it.status })
        val request = server.takeRequest()
        assertEquals("/api/v1/device/events/batch", request.path)
        assertEquals("Bearer device-access", request.getHeader("Authorization"))
        val body = request.body.readUtf8()
        assertTrue(body.contains(""""collector":"sms""""))
        assertTrue(body.contains(""""payload":{"sender":"Sender","body":"Body"}"""))
        assertTrue(body.contains(""""metadata":{"rule_id":"rule"}"""))
    }

    @Test
    fun rejectsMalformedIngestionResultDocument() = runTest {
        server.enqueue(
            MockResponse().setBody("""{"results":[{"event_id":"event-1","status":"later"}]}""")
        )
        try {
            api.uploadEvents("access", listOf(event("event-1")))
            error("Expected ApiFailure")
        } catch (error: ApiFailure) {
            assertEquals(502, error.statusCode)
        }
    }

    private fun event(id: String) = GenericEvent(
        id,
        "00000000-0000-0000-0000-000000000002",
        "sms",
        1,
        4,
        "2024-01-01T00:00:00Z",
        "2024-01-01T00:00:01Z",
        "Sender",
        "Body",
        "rule"
    )

    private fun tokenBody(installation: String): String = """
        {
          "device_id":"00000000-0000-0000-0000-000000000001",
          "installation_id":"$installation",
          "device_name":"Managed",
          "access_token":"access",
          "token_type":"bearer",
          "expires_in":900,
          "refresh_token":"refresh",
          "refresh_expires_at":"2099-01-01T00:00:00Z"
        }
    """.trimIndent()
}
