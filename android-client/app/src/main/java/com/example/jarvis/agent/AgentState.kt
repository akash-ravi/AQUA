package com.example.jarvis.agent

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

data class Question(val id: String, val text: String)

data class AgentUiState(
    val running: Boolean = false,
    val log: List<String> = emptyList(),
    val question: Question? = null,
    val pauseRequested: Boolean = false,
    val paused: Boolean = false,
    val message: String? = null,
)

object AgentState {
    private const val MAX_LOG_LINES = 200

    private val mutableState = MutableStateFlow(AgentUiState())
    val state: StateFlow<AgentUiState> = mutableState

    fun begin(task: String) {
        mutableState.value = AgentUiState(running = true, log = listOf("task: $task"), message = task)
    }

    fun log(line: String) = mutableState.update { it.copy(log = (it.log + line).takeLast(MAX_LOG_LINES)) }

    fun ask(question: Question) {
        log("? ${question.text}")
        mutableState.update { it.copy(question = question) }
    }

    fun clearQuestion() = mutableState.update { it.copy(question = null) }

    fun requestPause() = mutableState.update { it.copy(pauseRequested = true, question = null) }

    fun setPaused(paused: Boolean) = mutableState.update {
        it.copy(paused = paused, pauseRequested = false, question = if (paused) null else it.question)
    }

    fun message(text: String) = mutableState.update { it.copy(message = text) }

    fun end() = mutableState.update { it.copy(running = false, question = null, paused = false, pauseRequested = false) }
}
