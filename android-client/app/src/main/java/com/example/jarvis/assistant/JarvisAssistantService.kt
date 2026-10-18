package com.example.jarvis.assistant

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import com.example.jarvis.MainActivity
import com.example.jarvis.service.PhoneUseService

/** Registered with Android as a digital assistant; input remains text-only. */
class JarvisAssistantService : VoiceInteractionService()

class JarvisAssistantSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = JarvisAssistantSession(this)
}

private class JarvisAssistantSession(context: Context) : VoiceInteractionSession(context) {
    override fun onCreate() {
        super.onCreate()
        setUiEnabled(false)
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        try {
            val service = PhoneUseService.instance
            if (service != null) service.showOverlay()
            else startAssistantActivity(Intent(context, MainActivity::class.java))
        } finally {
            hide()
        }
    }
}
