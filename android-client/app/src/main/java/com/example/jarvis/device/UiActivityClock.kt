package com.example.jarvis.device

import android.os.SystemClock

class UiActivityClock {
    @Volatile
    var lastEventUptimeMs = 0L
        private set

    fun markActivity() {
        lastEventUptimeMs = SystemClock.uptimeMillis()
    }
}
