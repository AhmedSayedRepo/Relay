package com.relay.agent.presentation

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.relay.agent.data.KeepConnectedService

/** After the phone restarts, connect again without anyone opening the app. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        KeepConnectedService.start(context)
    }
}
