package com.example.jarvis

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import com.example.jarvis.service.PhoneUseService

/** Android's assistant gesture opens the same composer as the app and notification. */
class AssistantActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val service = PhoneUseService.instance
        if (service == null) startActivity(Intent(this, MainActivity::class.java))
        else service.showOverlay()
        finish()
    }
}
