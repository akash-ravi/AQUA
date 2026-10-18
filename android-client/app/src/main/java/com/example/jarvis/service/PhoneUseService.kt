package com.example.jarvis.service

import android.accessibilityservice.AccessibilityService
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.view.accessibility.AccessibilityEvent
import com.example.jarvis.BuildConfig
import com.example.jarvis.agent.AgentSocket
import com.example.jarvis.agent.AgentState
import com.example.jarvis.device.CommandExecutor
import com.example.jarvis.device.UiActivityClock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import org.json.JSONObject

class PhoneUseService : AccessibilityService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val clock = UiActivityClock()
    private val notification by lazy { TaskNotification(this) }
    private var socket: AgentSocket? = null

    private val stopReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) = stopTask()
    }

    override fun onServiceConnected() {
        instance = this
        registerReceiver(stopReceiver, IntentFilter(TaskNotification.ACTION_STOP), RECEIVER_NOT_EXPORTED)
    }

    override fun onDestroy() {
        instance = null
        stopTask()
        unregisterReceiver(stopReceiver)
        scope.cancel()
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = clock.markActivity()

    override fun onInterrupt() = Unit

    fun startTask(task: String) {
        stopTask()
        AgentState.begin(task)
        notification.show()
        val agentSocket = AgentSocket(BuildConfig.AGENT_URL, CommandExecutor(this, clock), scope, ::onTaskFinished)
        socket = agentSocket
        agentSocket.start(task, deviceInfo())
    }

    fun stopTask() {
        socket?.stop()
        socket = null
    }

    fun answerQuestion(questionId: String, text: String) = socket?.answer(questionId, text)

    private fun onTaskFinished() {
        notification.hide()
        AgentState.end()
    }

    private fun deviceInfo() = JSONObject()
        .put("width", resources.displayMetrics.widthPixels)
        .put("height", resources.displayMetrics.heightPixels)
        .put("sdk", Build.VERSION.SDK_INT)

    companion object {
        var instance: PhoneUseService? = null
            private set
    }
}
