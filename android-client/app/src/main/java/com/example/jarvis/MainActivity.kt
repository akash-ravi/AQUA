package com.example.jarvis

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import com.example.jarvis.agent.AgentState
import com.example.jarvis.service.PhoneUseService
import com.example.jarvis.ui.TaskScreen
import com.example.jarvis.ui.theme.JarvisTheme

class MainActivity : ComponentActivity() {

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        requestNotificationPermission()
        setContent {
            JarvisTheme {
                val state by AgentState.state.collectAsState()
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    TaskScreen(
                        state = state,
                        onSubmitTask = ::startTask,
                        onStop = { PhoneUseService.instance?.stopTask() },
                        onAnswer = { questionId, text -> PhoneUseService.instance?.answerQuestion(questionId, text) },
                        modifier = Modifier.padding(innerPadding),
                    )
                }
            }
        }
    }

    private fun startTask(task: String) {
        val service = PhoneUseService.instance
        if (service == null) {
            Toast.makeText(this, "Enable the Jarvis accessibility service first", Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        } else {
            service.startTask(task)
            moveTaskToBack(true)
        }
    }

    private fun requestNotificationPermission() {
        val needsPermission = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        if (needsPermission) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}
