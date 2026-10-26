package com.relay.agent.application

import com.relay.agent.domain.ApiFailure
import java.io.IOException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RetryPolicyTest {
    @Test
    fun retriesOnlyBoundedTransientFailures() {
        assertTrue(RetryPolicy.retryable(IOException()))
        assertTrue(RetryPolicy.retryable(ApiFailure(408)))
        assertTrue(RetryPolicy.retryable(ApiFailure(429)))
        assertTrue(RetryPolicy.retryable(ApiFailure(503)))
        assertFalse(RetryPolicy.retryable(ApiFailure(401)))
        assertFalse(RetryPolicy.retryable(ApiFailure(422)))
        assertEquals(listOf(30L, 60L, 120L, 240L, 480L), (0..4).map(RetryPolicy::delaySeconds))
        assertTrue(RetryPolicy.shouldRetry(3))
        assertFalse(RetryPolicy.shouldRetry(4))
    }
}
