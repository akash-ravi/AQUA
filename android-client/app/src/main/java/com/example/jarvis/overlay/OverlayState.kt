package com.example.jarvis.overlay

import com.example.jarvis.agent.AgentUiState
import com.example.jarvis.agent.MAX_INPUT_CHARS

enum class OverlayMode { IDLE, PROCESSING, WAITING_FOR_USER, PAUSING, PAUSED }

data class AssistantInput(
    val text: String,
    val questionId: String? = null,
    val voice: Boolean = false,
)

data class OverlayState(
    val mode: OverlayMode = OverlayMode.IDLE,
    val questionId: String? = null,
    val question: String? = null,
    val message: String? = null,
    val status: String? = null,
) {
    companion object {
        fun from(agent: AgentUiState) = when {
            agent.running && agent.pauseRequested -> OverlayState(OverlayMode.PAUSING, message = agent.message)
            agent.running && agent.paused -> OverlayState(OverlayMode.PAUSED, message = agent.message)
            agent.running && agent.question != null -> OverlayState(
                OverlayMode.WAITING_FOR_USER, agent.question.id, agent.question.text,
            )
            agent.running -> OverlayState(OverlayMode.PROCESSING, message = agent.message, status = agent.status)
            else -> OverlayState(message = agent.message)
        }
    }
}

sealed interface OverlaySubmission {
    data class StartTask(val text: String) : OverlaySubmission
    data class QueueTask(val text: String) : OverlaySubmission
    data class Answer(val questionId: String, val text: String, val voice: Boolean = false) : OverlaySubmission
}

/** New tasks while busy are queued; answers drafted for a different question are rejected. */
fun routeInput(state: OverlayState, input: AssistantInput): OverlaySubmission? {
    val text = input.text.trim()
    if (text.isEmpty() || text.length > MAX_INPUT_CHARS) return null
    if (input.questionId != state.questionId) return null
    return when (state.mode) {
        OverlayMode.IDLE -> OverlaySubmission.StartTask(text)
        OverlayMode.WAITING_FOR_USER -> state.questionId?.let { OverlaySubmission.Answer(it, text, input.voice) }
        OverlayMode.PROCESSING, OverlayMode.PAUSING, OverlayMode.PAUSED -> OverlaySubmission.QueueTask(text)
    }
}
