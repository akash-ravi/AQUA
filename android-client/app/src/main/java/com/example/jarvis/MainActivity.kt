package com.example.jarvis

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.SystemBarStyle
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

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        if (intent.getBooleanExtra(AssistantActivity.EXTRA_OPEN_OVERLAY, false)) {
            if (PhoneUseService.instance != null) {
                showAssistant()
            } else {
                Toast.makeText(this, "Enable Jarvis accessibility to open the floating assistant", Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(Color.TRANSPARENT),
        )
        requestNotificationPermission()
        setContent {
            JarvisTheme {
                val state by AgentState.state.collectAsState()
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    TaskScreen(
                        state = state,
                        onSubmitTask = ::startTask,
                        onStop = { PhoneUseService.instance?.stopTask() },
                        onPause = { PhoneUseService.instance?.pauseTask() },
                        onContinue = { PhoneUseService.instance?.continueTask() },
                        onShowAssistant = ::showAssistant,
                        onSetDefaultAssistant = ::chooseDefaultAssistant,
                        onAnswer = { questionId, text -> PhoneUseService.instance?.answerQuestion(questionId, text) },
                        modifier = Modifier.padding(innerPadding),
                    )
                }
            }
        }
    }

    private fun showAssistant() {
        val service = PhoneUseService.instance
        if (service == null) {
            intent.putExtra(AssistantActivity.EXTRA_OPEN_OVERLAY, true)
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        } else {
            intent.removeExtra(AssistantActivity.EXTRA_OPEN_OVERLAY)
            service.showOverlay()
            moveTaskToBack(true)
        }
    }

    private fun chooseDefaultAssistant() {
        // The assistant role is selected through Settings, not a request-role dialog.
        try {
            startActivity(Intent(Settings.ACTION_VOICE_INPUT_SETTINGS))
        } catch (_: ActivityNotFoundException) {
            startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
        }
    }

    private fun startTask(task: String) {
        val service = PhoneUseService.instance
        if (service == null) {
            Toast.makeText(this, "Enable the Jarvis accessibility service first", Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        } else {
            if (service.startTask(task)) moveTaskToBack(true)
        }
    }

    private fun requestNotificationPermission() {
        val needsPermission = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        if (needsPermission) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}
