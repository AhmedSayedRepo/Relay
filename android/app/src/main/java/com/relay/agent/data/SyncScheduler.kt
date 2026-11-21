package com.relay.agent.data

import android.content.Context
import android.provider.MediaStore
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.relay.agent.RelayApplication
import com.relay.agent.application.RetryPolicy
import com.relay.agent.domain.AgentStore
import com.relay.agent.domain.SyncOutcome
import java.util.concurrent.TimeUnit

class SyncScheduler(context: Context, private val store: AgentStore) {
    private val workManager = WorkManager.getInstance(context)

    suspend fun schedule() {
        val state = store.read()
        if (state.credentials == null) {
            workManager.cancelUniqueWork(WORK_NAME)
            return
        }
        val seconds = state.configuration?.syncIntervalSeconds ?: 900
        val request = PeriodicWorkRequestBuilder<ConfigurationSyncWorker>(seconds, TimeUnit.SECONDS)
            .setConstraints(
                Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            )
            .setBackoffCriteria(
                BackoffPolicy.EXPONENTIAL,
                RetryPolicy.BASE_DELAY_SECONDS,
                TimeUnit.SECONDS
            )
            .build()
        workManager.enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, request)
        if (state.localImageConsent && state.approvedImageSource != null) {
            NewImageWatcher.watch(workManager, ExistingWorkPolicy.KEEP)
        } else {
            workManager.cancelUniqueWork(NewImageWatcher.WORK_NAME)
        }
    }

    companion object {
        private const val WORK_NAME = "relay.configuration.sync"
    }
}

/**
 * Wakes the app as soon as a new image is saved to the phone's gallery
 * (WhatsApp, Bluetooth, ...) instead of waiting for the 15-minute cycle.
 * The image collector then reads only the folders the person chose.
 */
class NewImageWatcher(context: Context, parameters: WorkerParameters) :
    CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        EventUploadScheduler(applicationContext).enqueue()
        // A content trigger fires once; queue the next watch behind this one.
        watch(WorkManager.getInstance(applicationContext), ExistingWorkPolicy.APPEND_OR_REPLACE)
        return Result.success()
    }

    companion object {
        const val WORK_NAME = "relay.images.watch"

        fun watch(workManager: WorkManager, policy: ExistingWorkPolicy) {
            val constraints = Constraints.Builder()
                .addContentUriTrigger(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true)
                .setTriggerContentUpdateDelay(3, TimeUnit.SECONDS)
                .setTriggerContentMaxDelay(30, TimeUnit.SECONDS)
                .build()
            val request = OneTimeWorkRequestBuilder<NewImageWatcher>()
                .setConstraints(constraints)
                .build()
            workManager.enqueueUniqueWork(WORK_NAME, policy, request)
        }
    }
}

class EventUploadScheduler(context: Context) {
    private val workManager = WorkManager.getInstance(context)

    fun enqueue() {
        val request = OneTimeWorkRequestBuilder<EventUploadWorker>()
            .setConstraints(
                Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            )
            .setBackoffCriteria(
                BackoffPolicy.EXPONENTIAL,
                RetryPolicy.BASE_DELAY_SECONDS,
                TimeUnit.SECONDS
            )
            .build()
        workManager.enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.KEEP, request)
    }

    companion object {
        private const val WORK_NAME = "relay.event.upload"
    }
}

class ConfigurationSyncWorker(context: Context, parameters: WorkerParameters) :
    CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val app = RelayApplication.from(applicationContext)
        val outcome = app.controller.sync()
        return when (outcome) {
            SyncOutcome.SUCCESS -> {
                // Bank SMS that arrived while Android had the app stopped.
                runCatching { app.catchUpSmsSinceLastCheck() }
                val upload = app.controller.upload()
                app.imageCollector.collect()
                val imageUpload = app.imageUploader.upload()
                app.scheduler.schedule()
                if (
                    (upload == SyncOutcome.RETRYABLE || imageUpload == SyncOutcome.RETRYABLE) &&
                    RetryPolicy.shouldRetry(runAttemptCount)
                ) {
                    Result.retry()
                } else {
                    Result.success()
                }
            }
            SyncOutcome.ENROLLMENT_REQUIRED -> {
                app.scheduler.schedule()
                Result.success()
            }
            SyncOutcome.RETRYABLE -> if (RetryPolicy.shouldRetry(
                    runAttemptCount
                )
            ) {
                Result.retry()
            } else {
                Result.success()
            }
            // Finish this bounded cycle. A future periodic cycle can recover after administrator correction.
            SyncOutcome.REJECTED -> Result.success()
        }
    }
}

class EventUploadWorker(context: Context, parameters: WorkerParameters) :
    CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val app = RelayApplication.from(applicationContext)
        val eventOutcome = app.controller.upload()
        app.imageCollector.collect()
        val imageOutcome = app.imageUploader.upload()
        val outcome = if (
            eventOutcome == SyncOutcome.RETRYABLE || imageOutcome == SyncOutcome.RETRYABLE
        ) {
            SyncOutcome.RETRYABLE
        } else {
            imageOutcome
        }
        return when (outcome) {
            SyncOutcome.SUCCESS,
            SyncOutcome.ENROLLMENT_REQUIRED,
            SyncOutcome.REJECTED -> Result.success()
            SyncOutcome.RETRYABLE -> if (RetryPolicy.shouldRetry(runAttemptCount)) {
                Result.retry()
            } else {
                Result.success()
            }
        }
    }
}
