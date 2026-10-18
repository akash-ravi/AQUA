package com.example.jarvis

import android.Manifest
import android.app.role.RoleManager
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
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
                    Column(Modifier.padding(innerPadding)) {
                        Row {
                            TextButton(onClick = ::showAssistant) { Text("Show assistant") }
                            TextButton(onClick = ::chooseDefaultAssistant) { Text("Set default assistant") }
                        }
                        TaskScreen(
                            state = state,
                            onSubmitTask = ::startTask,
                            onStop = { PhoneUseService.instance?.stopTask() },
                            onAnswer = { questionId, text -> PhoneUseService.instance?.answerQuestion(questionId, text) },
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }
        }
    }

    private fun showAssistant() {
        val service = PhoneUseService.instance
        if (service == null) startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        else { service.showOverlay(); moveTaskToBack(true) }
    }

    private fun chooseDefaultAssistant() {
        val roles = getSystemService(RoleManager::class.java)
        if (roles.isRoleAvailable(RoleManager.ROLE_ASSISTANT) && !roles.isRoleHeld(RoleManager.ROLE_ASSISTANT)) {
            assistantRole.launch(roles.createRequestRoleIntent(RoleManager.ROLE_ASSISTANT))
        } else {
            startActivity(Intent(Settings.ACTION_VOICE_INPUT_SETTINGS))
        }
    }

    private val assistantRole = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { }

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
