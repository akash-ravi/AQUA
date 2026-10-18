package com.example.jarvis.service

import android.accessibilityservice.AccessibilityService
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.display.DisplayManager
import android.os.Build
import android.util.Log
import android.view.WindowManager
import android.view.Display
import android.widget.Toast
import android.view.accessibility.AccessibilityEvent
import com.example.jarvis.BuildConfig
import com.example.jarvis.agent.AgentSocket
import com.example.jarvis.agent.AgentState
import com.example.jarvis.agent.TaskOutcome
import com.example.jarvis.device.CommandExecutor
import com.example.jarvis.device.UiActivityClock
import com.example.jarvis.overlay.AgentOverlay
import com.example.jarvis.overlay.AuraRing
import com.example.jarvis.overlay.AssistantInput
import com.example.jarvis.overlay.OverlayState
import com.example.jarvis.overlay.OverlayMode
import com.example.jarvis.overlay.OverlaySubmission
import com.example.jarvis.overlay.routeInput
import com.example.jarvis.voice.Voice
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
    private var auraUnavailable = false
    private var voice: Voice? = null
    private var listening = false
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
        val display = getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)
        val windowContext = createDisplayContext(display)
            .createWindowContext(WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY, null)
        voice = Voice(this, ::submitSpeech, ::onListeningChanged)
        val level = { voice?.level() ?: 0f }
        overlay = AgentOverlay(windowContext, ::submitOverlayInput, ::stopTask,
            ::pauseTask, ::continueTask, ::hideOverlay, ::onMicPressed, level)
        aura = AuraRing(windowContext, level)
        scope.launch {
            AgentState.state.map { OverlayState.from(it) }.distinctUntilChanged().collect {
                overlay?.render(it)
                updateAura()
                if (it.mode == OverlayMode.WAITING_FOR_USER) voice?.speak(it.question.orEmpty(), ::listen)
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
        closeAura()
        aura = null
        voice?.close()
        voice = null
        unregisterReceiver(stopReceiver)
        scope.cancel()
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = clock.markActivity()

    override fun onInterrupt() = Unit

    fun startTask(task: String): Boolean {
        stopTask()
        AgentState.begin(task)
        val generation = ++taskGeneration
        return try {
            overlay?.collapse()
            notification.show()
            showOverlay(openComposer = false)
            val executor = CommandExecutor(this, clock,
                beforeScreenshot = { setCaptureHidden(true); delay(80) },
                afterScreenshot = { setCaptureHidden(false) })
            val agentSocket = AgentSocket(BuildConfig.AGENT_URL, executor, scope) { outcome, message ->
                onTaskFinished(generation, outcome, message)
            }
            socket = agentSocket
            agentSocket.start(task, deviceInfo())
            true
        } catch (error: RuntimeException) {
            Log.e("JarvisTask", "Task startup failed", error)
            ++taskGeneration
            socket?.stop()
            socket = null
            notification.hide()
            AgentState.finish(TaskOutcome.FAILED, "Could not start the task. Open the activity log for details.")
            AgentState.log("Startup failed: ${error.javaClass.simpleName}")
            Toast.makeText(this, "Jarvis could not start this task. Open the app for details.", Toast.LENGTH_LONG).show()
            false
        }
    }

    fun stopTask() {
        voice?.cancel()
        ++taskGeneration
        socket?.stop()
        socket = null
        notification.hide()
        AgentState.finish(TaskOutcome.STOPPED, "Stopped")
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
        if (openComposer) listen()
    }

    fun hideOverlay() {
        voice?.cancel()
        overlayVisible = false
        overlay?.close()
        closeAura()
    }

    private fun setCaptureHidden(hidden: Boolean) {
        captureHidden = hidden
        overlay?.hideForCapture(hidden)
        updateAura()
    }

    private fun updateAura() {
        if (auraUnavailable) return
        try {
            aura?.render(OverlayState.from(AgentState.state.value).mode, overlayVisible && !captureHidden, listening)
        } catch (error: RuntimeException) {
            // Decorative UI must not prevent the executor from starting or answering a command.
            auraUnavailable = true
            Log.e("JarvisAura", "Aura disabled after a window failure", error)
            closeAura()
        }
    }

    private fun closeAura() {
        try {
            aura?.close()
        } catch (error: RuntimeException) {
            Log.e("JarvisAura", "Could not detach aura window", error)
        }
    }

    fun answerQuestion(questionId: String, text: String) {
        if (AgentState.state.value.question?.id != questionId) return
        voice?.cancel()
        overlay?.collapse()
        socket?.answer(questionId, text)
    }

    private fun submitOverlayInput(input: AssistantInput): Boolean {
        return when (val submission = routeInput(OverlayState.from(AgentState.state.value), input)) {
            is OverlaySubmission.StartTask -> startTask(submission.text)
            is OverlaySubmission.Answer -> { answerQuestion(submission.questionId, submission.text); true }
            null -> false
        }
    }

    private fun listen(): Boolean =
        OverlayState.from(AgentState.state.value).acceptsInput && voice?.listen() == true

    private fun onMicPressed() {
        if (listening) { voice?.cancel(); return }
        if (!listen()) AgentState.message("Voice input is unavailable. Allow microphone access in the Jarvis app.")
    }

    private fun onListeningChanged(value: Boolean) {
        listening = value
        overlay?.setListening(value)
        updateAura()
    }

    private fun submitSpeech(text: String) {
        submitOverlayInput(AssistantInput(text, OverlayState.from(AgentState.state.value).questionId))
    }

    private fun onTaskFinished(generation: Int, outcome: TaskOutcome, message: String) {
        // WebSocket callbacks may arrive on OkHttp's worker thread.
        scope.launch {
            if (generation != taskGeneration) return@launch
            socket = null
            notification.hide()
            AgentState.finish(outcome, message)
            if (outcome.dismissOverlay) {
                hideOverlay()
            } else if (overlayVisible) {
                // Errors must remain visible instead of making the app appear to close.
                try {
                    overlay?.render(OverlayState.from(AgentState.state.value))
                    overlay?.openComposer()
                } catch (error: RuntimeException) {
                    Log.e("JarvisTask", "Could not show task failure", error)
                    Toast.makeText(this@PhoneUseService, message, Toast.LENGTH_LONG).show()
                }
            }
            // hideOverlay() cancels voice, so speak only after the overlay is settled.
            voice?.speak(message)
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
