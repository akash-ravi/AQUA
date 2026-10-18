package com.example.jarvis.overlay

import com.example.jarvis.agent.AgentUiState
import com.example.jarvis.agent.Question
import com.example.jarvis.agent.MAX_INPUT_CHARS
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OverlayStateTest {
    @Test fun `task text is trimmed and routed from idle`() {
        assertEquals(OverlaySubmission.StartTask("Find pizza"), routeInput(OverlayState(), AssistantInput(" Find pizza ")))
        assertNull(routeInput(OverlayState(), AssistantInput("  ")))
        assertNull(routeInput(OverlayState(), AssistantInput("x".repeat(MAX_INPUT_CHARS + 1))))
        val longest = "x".repeat(MAX_INPUT_CHARS)
        assertEquals(OverlaySubmission.StartTask(longest), routeInput(OverlayState(), AssistantInput(longest)))
    }

    @Test fun `busy input cannot replace a running task`() {
        val state = OverlayState.from(AgentUiState(running = true))
        assertEquals(OverlayMode.PROCESSING, state.mode)
        assertNull(routeInput(state, AssistantInput("Call someone else")))
    }

    @Test fun `pause request and acknowledged pause do not accept tasks or answers`() {
        val asking = AgentUiState(running = true, question = Question("q", "Place order?"))
        val pausing = OverlayState.from(asking.copy(pauseRequested = true))
        val paused = OverlayState.from(asking.copy(paused = true))
        assertEquals(OverlayMode.PAUSING, pausing.mode)
        assertEquals(OverlayMode.PAUSED, paused.mode)
        assertNull(routeInput(pausing, AssistantInput("yes", questionId = "q")))
        assertNull(routeInput(paused, AssistantInput("new task")))
    }

    @Test fun `answer is bound to the current question`() {
        val state = OverlayState.from(AgentUiState(running = true, question = Question("q7", "Which pizza?")))
        assertEquals(OverlayMode.WAITING_FOR_USER, state.mode)
        assertEquals(OverlaySubmission.Answer("q7", "Margherita"),
            routeInput(state, AssistantInput("Margherita", questionId = "q7")))
    }

    @Test fun `stale question after task end cannot receive an answer`() {
        val state = OverlayState.from(AgentUiState(running = false, question = Question("old", "Old question")))
        assertEquals(OverlayMode.IDLE, state.mode)
        assertNull(state.questionId)
    }

    @Test fun `draft for an old question cannot answer a replacement or start a new task`() {
        val stale = AssistantInput("yes", questionId = "old")
        assertNull(routeInput(OverlayState(), stale))
        assertNull(routeInput(OverlayState(OverlayMode.WAITING_FOR_USER, "new", "A different question"), stale))
    }
}
