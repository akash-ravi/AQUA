package com.example.jarvis

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import com.example.jarvis.service.PhoneUseService

/** Android's assistant gesture opens the floating composer; the full app only when accessibility is off. */
class AssistantActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private var openOverlayAfterClose = false
    private var waitedMs = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (accessibilityEnabled()) waitForService() else openSetup()
    }

    /** On a cold start Android binds the accessibility service a moment after this activity starts. */
    private fun waitForService() {
        when {
            PhoneUseService.instance != null -> {
                openOverlayAfterClose = true
                finish()
            }
            waitedMs >= SERVICE_WAIT_MS -> openSetup()
            else -> {
                waitedMs += POLL_MS
                handler.postDelayed(::waitForService, POLL_MS)
            }
        }
    }

    private fun openSetup() {
        // Launch setup while this invocation activity is still in the foreground.
        startActivity(Intent(this, MainActivity::class.java)
            .putExtra(EXTRA_OPEN_OVERLAY, true)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }

    private fun accessibilityEnabled(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
            ?: return false
        val service = ComponentName(this, PhoneUseService::class.java)
        return enabled.split(':').any { ComponentName.unflattenFromString(it) == service }
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacksAndMessages(null)
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
        private const val SERVICE_WAIT_MS = 2_000L
        private const val POLL_MS = 100L
    }
}
