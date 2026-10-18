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
import com.example.jarvis.overlay.AuraRing
import com.example.jarvis.overlay.AssistantInput
import com.example.jarvis.overlay.OverlayState
import com.example.jarvis.overlay.OverlayMode
import com.example.jarvis.overlay.OverlaySubmission
import com.example.jarvis.overlay.routeInput
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.distinctUntilChanged
import org.json.JSONObject

class PhoneUseService : AccessibilityService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val clock = UiActivityClock()
    private val notification by lazy { TaskNotification(this) }
    private var socket: AgentSocket? = null
    private var overlay: AgentOverlay? = null
    private var aura: AuraRing? = null
    private var overlayVisible = false
    private var captureHidden = false
    private var taskGeneration = 0

    private val stopReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == TaskNotification.ACTION_SHOW) showOverlay() else stopTask()
        }
    }

    override fun onServiceConnected() {
        instance = this
        val filter = IntentFilter(TaskNotification.ACTION_STOP).apply { addAction(TaskNotification.ACTION_SHOW) }
        registerReceiver(stopReceiver, filter, RECEIVER_NOT_EXPORTED)
        overlay = AgentOverlay(this, ::submitOverlayInput, ::stopTask,
            ::pauseTask, ::continueTask, ::hideOverlay)
        aura = AuraRing(this)
        scope.launch {
            AgentState.state.map { OverlayState.from(it) }.distinctUntilChanged().collect {
                overlay?.render(it)
                updateAura()
                if (it.mode != OverlayMode.IDLE) notification.show(when (it.mode) {
                    OverlayMode.PAUSED -> "Jarvis is paused"
                    OverlayMode.PAUSING -> "Jarvis is pausing"
                    OverlayMode.WAITING_FOR_USER -> "Jarvis needs your input"
                    else -> "Jarvis is controlling the phone"
                })
            }
        }
    }

    override fun onDestroy() {
        instance = null
        stopTask()
        overlay?.close()
        overlay = null
        aura?.close()
        aura = null
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
        showOverlay(openComposer = false)
        val generation = ++taskGeneration
        val executor = CommandExecutor(this, clock,
            beforeScreenshot = { setCaptureHidden(true); delay(80) },
            afterScreenshot = { setCaptureHidden(false) })
        val agentSocket = AgentSocket(BuildConfig.AGENT_URL, executor, scope) {
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
        AgentState.message("Stopped")
    }

    fun pauseTask() {
        socket?.pause()
    }

    fun continueTask() {
        overlay?.collapse()
        socket?.resume()
    }

    fun showOverlay(openComposer: Boolean = true) {
        overlayVisible = true
        if (openComposer) overlay?.openComposer() else overlay?.show()
        overlay?.hideForCapture(captureHidden)
        updateAura()
    }

    fun hideOverlay() {
        overlayVisible = false
        overlay?.close()
        aura?.close()
    }

    private fun setCaptureHidden(hidden: Boolean) {
        captureHidden = hidden
        overlay?.hideForCapture(hidden)
        updateAura()
    }

    private fun updateAura() {
        aura?.render(OverlayState.from(AgentState.state.value).mode, overlayVisible && !captureHidden)
    }

    fun answerQuestion(questionId: String, text: String) {
        if (AgentState.state.value.question?.id != questionId) return
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
            hideOverlay()
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
