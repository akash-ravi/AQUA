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
import com.example.jarvis.overlay.AgentOverlay
import com.example.jarvis.overlay.AssistantInput
import com.example.jarvis.overlay.OverlayState
import com.example.jarvis.overlay.OverlaySubmission
import com.example.jarvis.overlay.routeInput
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import org.json.JSONObject

class PhoneUseService : AccessibilityService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val clock = UiActivityClock()
    private val notification by lazy { TaskNotification(this) }
    private var socket: AgentSocket? = null
    private var overlay: AgentOverlay? = null
    private var taskGeneration = 0

    private val stopReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) = stopTask()
    }

    override fun onServiceConnected() {
        instance = this
        registerReceiver(stopReceiver, IntentFilter(TaskNotification.ACTION_STOP), RECEIVER_NOT_EXPORTED)
        overlay = AgentOverlay(this, ::submitOverlayInput, ::stopTask).also { it.show() }
        scope.launch { AgentState.state.collect { overlay?.render(OverlayState.from(it)) } }
    }

    override fun onDestroy() {
        instance = null
        stopTask()
        overlay?.close()
        overlay = null
        unregisterReceiver(stopReceiver)
        scope.cancel()
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = clock.markActivity()

    override fun onInterrupt() = Unit

    fun startTask(task: String) {
        overlay?.collapse()
        stopTask()
        AgentState.begin(task)
        notification.show()
        val generation = ++taskGeneration
        val agentSocket = AgentSocket(BuildConfig.AGENT_URL, CommandExecutor(this, clock), scope) {
            onTaskFinished(generation)
        }
        socket = agentSocket
        agentSocket.start(task, deviceInfo())
    }

    fun stopTask() {
        ++taskGeneration
        socket?.stop()
        socket = null
        notification.hide()
        AgentState.end()
    }

    fun answerQuestion(questionId: String, text: String) {
        overlay?.collapse()
        socket?.answer(questionId, text)
    }

    private fun submitOverlayInput(input: AssistantInput): Boolean {
        return when (val submission = routeInput(OverlayState.from(AgentState.state.value), input)) {
            is OverlaySubmission.StartTask -> { startTask(submission.text); true }
            is OverlaySubmission.Answer -> { answerQuestion(submission.questionId, submission.text); true }
            null -> false
        }
    }

    private fun onTaskFinished(generation: Int) {
        // WebSocket callbacks may arrive on OkHttp's worker thread.
        scope.launch {
            if (generation != taskGeneration) return@launch
            socket = null
            notification.hide()
            AgentState.end()
        }
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
