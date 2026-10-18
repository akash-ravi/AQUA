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
    private var spokenQuestionId: String? = null
    private var speechQuestionId: String? = null
    private var listening = false
    private var overlayVisible = false
    private var captureHidden = false
    private var taskGeneration = 0
    private var awaitingFollowUp = false
    private val queuedTasks = ArrayDeque<String>()

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
                val questionId = if (it.mode == OverlayMode.WAITING_FOR_USER) it.questionId else null
                if (spokenQuestionId != questionId) {
                    // An answer recorded for one question must never answer a replacement question.
                    voice?.cancel()
                    spokenQuestionId = questionId
                    speechQuestionId = questionId
                    if (questionId != null && overlayVisible) {
                        voice?.speak(it.question.orEmpty(), listenForReply = true)
                    }
                }
                if (it.mode != OverlayMode.IDLE) notification.show(when (it.mode) {
                    OverlayMode.PAUSED -> "Jarvis is paused"
                    OverlayMode.PAUSING -> "Jarvis is pausing"
                    OverlayMode.WAITING_FOR_USER -> "Jarvis needs your input"
                    else -> it.status ?: "Jarvis is controlling the phone"
                })
            }
        }
    }

    override fun onDestroy() {
        instance = null
        // Android suspends this service when another tool takes over accessibility (e.g. adb uiautomator).
        val reason = "Jarvis's accessibility service was turned off, so the task ended."
        endTask(reason, TaskOutcome.FAILED) { it.disconnect(reason) }
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
        awaitingFollowUp = false
        endTask("Stopped", TaskOutcome.STOPPED) { it.stop() }
        AgentState.begin(task)
        val generation = ++taskGeneration
        return try {
            overlay?.collapse()
            notification.show()
            showOverlay(listen = false)
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
        queuedTasks.clear()
        endTask("Stopped", TaskOutcome.STOPPED) { it.stop() }
    }

    private fun endTask(message: String, outcome: TaskOutcome, close: (AgentSocket) -> Unit) {
        voice?.cancel()
        spokenQuestionId = null
        speechQuestionId = null
        ++taskGeneration
        socket?.let(close)
        socket = null
        notification.hide()
        AgentState.finish(outcome, message)
    }

    fun pauseTask() {
        socket?.pause()
    }

    fun continueTask() {
        overlay?.collapse()
        socket?.resume()
    }

    /** Shows the orb; tapping it opens the text box. With [listen], the mic starts right away. */
    fun showOverlay(listen: Boolean = true) {
        overlayVisible = true
        overlay?.show()
        overlay?.collapse()
        overlay?.hideForCapture(captureHidden)
        updateAura()
        if (listen) listen()
    }

    fun hideOverlay() {
        awaitingFollowUp = false
        voice?.cancel()
        overlayVisible = false
        overlay?.dismiss()
        updateAura()
    }

    private fun setCaptureHidden(hidden: Boolean) {
        captureHidden = hidden
        overlay?.hideForCapture(hidden)
        updateAura()
    }

    private fun updateAura() {
        if (auraUnavailable) return
        try {
            aura?.render(OverlayState.from(AgentState.state.value).mode, overlayVisible, listening, captureHidden)
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

    fun answerQuestion(questionId: String, text: String, voiceInput: Boolean = false): Boolean {
        val question = AgentState.state.value.question ?: return false
        if (question.id != questionId) return false
        if (socket?.answer(questionId, text, voiceInput) != true) {
            val message = if (question.secret) "Use the microphone for this secret, or enter it directly on the phone and reply done."
                else "The answer could not be sent. Check the connection."
            AgentState.message(message)
            Toast.makeText(this, message, Toast.LENGTH_LONG).show()
            return false
        }
        voice?.cancel()
        spokenQuestionId = null
        speechQuestionId = null
        overlay?.collapse()
        voice?.speak("Got it.")
        return true
    }

    private fun submitOverlayInput(input: AssistantInput): Boolean {
        return when (val submission = routeInput(OverlayState.from(AgentState.state.value), input)) {
            is OverlaySubmission.StartTask -> startTask(submission.text)
            is OverlaySubmission.QueueTask -> queueTask(submission.text)
            is OverlaySubmission.Answer -> answerQuestion(submission.questionId, submission.text, submission.voice)
            null -> false
        }
    }

    private fun queueTask(task: String): Boolean {
        queuedTasks.addLast(task)
        AgentState.log("queued (${queuedTasks.size}): $task")
        overlay?.collapse()
        voice?.speak("Got it, that's next.")
        return true
    }

    private fun listen(): Boolean {
        speechQuestionId = OverlayState.from(AgentState.state.value).questionId
        return voice?.listen() == true
    }

    private fun onMicPressed() {
        if (listening) { voice?.cancel(); return }
        if (!listen()) AgentState.message("Voice input is unavailable. Allow microphone access in the Jarvis app.")
    }

    private fun onListeningChanged(value: Boolean) {
        listening = value
        overlay?.setListening(value)
        updateAura()
        // Launched so a heard follow-up starts its task (clearing the flag) before this runs.
        if (!value) scope.launch {
            if (awaitingFollowUp && !listening && voice?.speaking != true && overlay?.expanded != true) hideOverlay()
        }
    }

    private fun submitSpeech(text: String) {
        if (!overlayVisible || speechQuestionId != OverlayState.from(AgentState.state.value).questionId) return
        submitOverlayInput(AssistantInput(text, speechQuestionId, voice = true))
    }

    private fun onTaskFinished(generation: Int, outcome: TaskOutcome, message: String) {
        // WebSocket callbacks may arrive on OkHttp's worker thread.
        scope.launch {
            if (generation != taskGeneration) return@launch
            voice?.cancel()
            spokenQuestionId = null
            speechQuestionId = null
            socket = null
            notification.hide()
            AgentState.finish(outcome, message)
            val next = queuedTasks.removeFirstOrNull()
            if (next != null) {
                // startTask cancels voice, so the summary is spoken once the next task is under way.
                startTask(next)
                voice?.speak(message)
                return@launch
            }
            if (outcome == TaskOutcome.COMPLETED) {
                awaitingFollowUp = overlayVisible
            } else if (outcome.dismissOverlay) {
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
            voice?.speak(message, listenForReply = overlayVisible)
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
