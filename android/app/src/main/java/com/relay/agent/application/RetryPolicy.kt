package com.relay.agent.application

import com.relay.agent.domain.ApiFailure
import java.io.IOException

object RetryPolicy {
    const val MAX_ATTEMPTS = 5
    const val BASE_DELAY_SECONDS = 30L

    fun retryable(error: Exception): Boolean = error is IOException ||
        (
            error is ApiFailure &&
                (error.statusCode in setOf(408, 429) || error.statusCode in 500..599)
            )

    fun shouldRetry(runAttemptCount: Int): Boolean = runAttemptCount < MAX_ATTEMPTS - 1

    fun delaySeconds(runAttemptCount: Int): Long =
        BASE_DELAY_SECONDS * (1L shl runAttemptCount.coerceIn(0, 4))
}
