package com.relay.agent.presentation

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.relay.agent.RelayApplication
import com.relay.agent.domain.SyncOutcome
import kotlinx.coroutines.launch

class AgentViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as RelayApplication
    val status = app.controller.status

    fun enroll(challenge: String) {
        viewModelScope.launch {
            if (app.controller.enroll(challenge) == SyncOutcome.SUCCESS) app.controller.sync()
            app.scheduler.schedule()
        }
    }

    fun sync() {
        viewModelScope.launch {
            app.controller.sync()
            app.controller.upload()
            app.imageCollector.collect()
            app.imageUploader.upload()
            app.controller.refreshPrivacyState()
            app.scheduler.schedule()
        }
    }

    fun setSmsConsent(value: Boolean) {
        viewModelScope.launch { app.controller.setLocalSmsConsent(value) }
    }

    fun permissionChanged() {
        viewModelScope.launch { app.controller.refreshPrivacyState() }
    }

    fun setImageConsent(value: Boolean) {
        viewModelScope.launch { app.controller.setLocalImageConsent(value) }
    }

    fun approveImageSource(treeUri: String) {
        viewModelScope.launch {
            val sourceId = app.store.read().configuration?.images?.sources?.firstOrNull()?.id
                ?: return@launch
            app.controller.approveImageSource(sourceId, treeUri)
        }
    }

    fun clearImageSource() {
        viewModelScope.launch { app.controller.clearImageSource() }
    }

    fun discardQueuedImages() {
        viewModelScope.launch {
            app.binaryOutbox.discardAll()
            app.controller.refreshPrivacyState()
        }
    }

    fun discardQueuedEvents() {
        viewModelScope.launch { app.controller.discardQueuedEvents() }
    }
}
