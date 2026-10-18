package com.example.jarvis.overlay

import com.example.jarvis.agent.AgentUiState

enum class OverlayMode { IDLE, PROCESSING, WAITING_FOR_USER }

data class AssistantInput(
    val text: String,
    val questionId: String? = null,
)

data class OverlayState(
    val mode: OverlayMode = OverlayMode.IDLE,
    val questionId: String? = null,
    val question: String? = null,
) {
    val acceptsInput: Boolean get() = mode != OverlayMode.PROCESSING

    companion object {
        fun from(agent: AgentUiState) = when {
            agent.running && agent.question != null -> OverlayState(
                OverlayMode.WAITING_FOR_USER, agent.question.id, agent.question.text,
            )
            agent.running -> OverlayState(OverlayMode.PROCESSING)
            else -> OverlayState()
        }
    }
}

sealed interface OverlaySubmission {
    data class StartTask(val text: String) : OverlaySubmission
    data class Answer(val questionId: String, val text: String) : OverlaySubmission
}

/** Reject busy submissions and answers drafted for a different question. */
fun routeInput(state: OverlayState, input: AssistantInput): OverlaySubmission? {
    val text = input.text.trim()
    if (text.isEmpty() || text.length > 8000 || !state.acceptsInput) return null
    if (input.questionId != state.questionId) return null
    return when (state.mode) {
        OverlayMode.IDLE -> OverlaySubmission.StartTask(text)
        OverlayMode.WAITING_FOR_USER -> state.questionId?.let { OverlaySubmission.Answer(it, text) }
        OverlayMode.PROCESSING -> null
    }
}
