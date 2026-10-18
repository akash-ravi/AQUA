package com.example.jarvis.device

object ConfirmationGuard {
    private val sensitiveLabel = Regex("\\b(place order|pay|buy|send)\\b", RegexOption.IGNORE_CASE)

    fun needsConfirmation(label: String) = sensitiveLabel.containsMatchIn(label)
}
