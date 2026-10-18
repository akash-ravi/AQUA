package com.example.jarvis.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TaskOutcomeTest {
    @Test fun `only completion and stop dismiss the assistant`() {
        assertTrue(TaskOutcome.COMPLETED.dismissOverlay)
        assertTrue(TaskOutcome.STOPPED.dismissOverlay)
        listOf("failed", "unconfirmed", "needs_user", "unexpected").forEach {
            assertFalse(TaskOutcome.fromWire(it).dismissOverlay)
        }
    }

    @Test fun `failed task keeps a visible explanation and clears running state`() {
        AgentState.begin("Call Alice")
        AgentState.ask(Question("q1", "Which number?"))
        AgentState.finish(TaskOutcome.FAILED, "Cannot connect to the backend")
        val state = AgentState.state.value
        assertFalse(state.running)
        assertTrue(state.failed)
        assertEquals("Cannot connect to the backend", state.message)
        assertEquals(null, state.question)
        AgentState.begin("Open Maps")
        assertFalse(AgentState.state.value.failed)
        AgentState.finish(TaskOutcome.STOPPED, "Stopped")
    }
}
