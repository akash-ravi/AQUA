package com.example.jarvis

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import com.example.jarvis.service.PhoneUseService

/** Android's assistant gesture opens the same composer as the app and notification. */
class AssistantActivity : Activity() {
    private var openOverlayAfterClose = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        openOverlayAfterClose = PhoneUseService.instance != null
        if (!openOverlayAfterClose) {
            // Launch setup while this invocation activity is still in the foreground.
            startActivity(Intent(this, MainActivity::class.java)
                .putExtra(EXTRA_OPEN_OVERLAY, true)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP))
        }
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        if (!isFinishing || !openOverlayAfterClose) return
        val appContext = applicationContext
        // Give the invocation window back before the overlay takes text-input focus.
        Handler(Looper.getMainLooper()).post {
            val service = PhoneUseService.instance
            if (service != null) service.showOverlay()
            else appContext.startActivity(Intent(appContext, MainActivity::class.java)
                .putExtra(EXTRA_OPEN_OVERLAY, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        }
    }

    companion object {
        const val EXTRA_OPEN_OVERLAY = "com.example.jarvis.OPEN_OVERLAY"
    }
}
