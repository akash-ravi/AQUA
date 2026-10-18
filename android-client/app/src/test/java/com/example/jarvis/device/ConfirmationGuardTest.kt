package com.example.jarvis.device

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfirmationGuardTest {
    @Test
    fun sensitiveLabelsNeedConfirmation() {
        listOf("Place order", "Pay \$14.20", "Buy now", "Send message").forEach {
            assertTrue(it, ConfirmationGuard.needsConfirmation(it))
        }
    }

    @Test
    fun harmlessLabelsDoNotNeedConfirmation() {
        listOf("Search", "Payment methods", "Chipotle", "").forEach {
            assertFalse(it, ConfirmationGuard.needsConfirmation(it))
        }
    }
}
