package com.example.jarvis.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceTurnTest {
    private class Input : VoiceInput {
        class Capture(val onSpeech: () -> Unit, val onDone: (String?) -> Unit)
        override var echoCancellationActive = true
        override var playbackActive = false
        var available = true
        var active = false
        val captures = mutableListOf<Capture>()
        override fun start(onSpeech: () -> Unit, onDone: (String?) -> Unit): Boolean {
            if (!available) return false
            captures += Capture(onSpeech, onDone)
            active = true
            return true
        }
        override fun stop(): Boolean = active.also { active = false }
        fun speech() = captures.last().onSpeech()
        fun finish(text: String?) {
            active = false
            captures.last().onDone(text)
        }
    }

    private class Output : VoiceOutput {
        var active = false
        val completions = mutableListOf<() -> Unit>()
        override fun speak(text: String, onDone: () -> Unit) {
            active = true
            completions += onDone
        }
        override fun stop() { active = false }
        fun finish() {
            active = false
            completions.last()()
        }
    }

    private class Harness {
        val input = Input()
        val output = Output()
        val pending = ArrayDeque<() -> Unit>()
        val heard = mutableListOf<String>()
        val listening = mutableListOf<Boolean>()
        var permission = true
        val turn = VoiceTurn(input, output, { permission }, { pending.addLast(it) }, heard::add, listening::add)
        fun dispatch() { while (pending.isNotEmpty()) pending.removeFirst()() }
    }

    @Test fun speechInterruptsPlaybackBeforeTranscriptionAndPreservesRecording() {
        val h = Harness()
        h.turn.speak("Which number?", listenForReply = true)
        assertTrue(h.input.active)
        assertTrue(h.output.active)
        h.input.speech()
        h.dispatch()
        assertFalse(h.output.active)
        assertTrue(h.input.active)
        assertFalse(h.input.playbackActive)
        assertTrue(h.heard.isEmpty())
        h.input.finish("The mobile number")
        h.dispatch()
        assertEquals(listOf("The mobile number"), h.heard)
        assertEquals(listOf(true, false), h.listening)
        assertEquals(1, h.input.captures.size)
    }

    @Test fun naturalCompletionDoesNotRestartTheMicrophone() {
        val h = Harness()
        h.turn.speak("Which number?", listenForReply = true)
        h.output.finish()
        h.dispatch()
        assertTrue(h.input.active)
        assertFalse(h.input.playbackActive)
        assertEquals(1, h.input.captures.size)
        assertEquals(listOf(true), h.listening)
    }

    @Test fun missingEchoCancellationFallsBackToSpeakThenListen() {
        val h = Harness()
        h.input.echoCancellationActive = false
        h.turn.speak("Which number?", listenForReply = true)
        assertFalse(h.input.active)
        assertTrue(h.output.active)
        h.output.finish()
        h.dispatch()
        assertTrue(h.input.active)
        assertEquals(2, h.input.captures.size)
    }

    @Test fun interruptedPlaybackCannotReopenTheMicAfterAnAnswer() {
        val h = Harness()
        h.turn.speak("Which number?", listenForReply = true)
        h.input.speech()
        h.dispatch()
        h.input.finish("Mobile")
        h.dispatch()
        h.output.finish() // A completion already queued by the audio worker.
        h.dispatch()
        assertFalse(h.input.active)
        assertEquals(1, h.input.captures.size)
        assertEquals(listOf("Mobile"), h.heard)
    }

    @Test fun cancelledCallbacksCannotAnswerANewQuestionOrStopItsPlayback() {
        val h = Harness()
        h.turn.speak("First question", listenForReply = true)
        h.input.speech()
        h.input.finish("Old answer")
        h.output.finish()
        h.turn.cancel()
        h.turn.speak("Replacement question", listenForReply = true)
        h.dispatch()
        assertTrue(h.input.active)
        assertTrue(h.output.active)
        assertTrue(h.heard.isEmpty())
        assertEquals(2, h.input.captures.size)
    }

    @Test fun cancelDuringFallbackDoesNotReopenTheMic() {
        val h = Harness()
        h.input.echoCancellationActive = false
        h.turn.speak("Which number?", listenForReply = true)
        h.output.finish()
        h.turn.cancel()
        h.dispatch()
        assertFalse(h.input.active)
        assertEquals(1, h.input.captures.size)
    }

    @Test fun explicitMicTapInterruptsPlaybackWithoutEchoCancellation() {
        val h = Harness()
        h.input.echoCancellationActive = false
        h.turn.speak("Which number?", listenForReply = true)
        assertTrue(h.turn.listen())
        assertFalse(h.output.active)
        assertTrue(h.input.active)
    }

    @Test fun permissionDenialAndRecorderFailureDoNotReportListening() {
        val h = Harness()
        h.permission = false
        h.turn.speak("Which number?", listenForReply = true)
        h.output.finish()
        h.dispatch()
        assertTrue(h.listening.isEmpty())
        assertTrue(h.input.captures.isEmpty())
        h.permission = true
        h.input.available = false
        assertFalse(h.turn.listen())
        assertTrue(h.listening.isEmpty())
    }

    @Test fun timeoutDoesNotRestartAnIdleMicrophone() {
        val h = Harness()
        h.turn.listen()
        h.input.finish(null)
        h.dispatch()
        assertFalse(h.input.active)
        assertEquals(1, h.input.captures.size)
        assertEquals(listOf(true, false), h.listening)
        assertTrue(h.heard.isEmpty())
    }

    @Test fun closeRejectsQueuedCallbacksAndNewAudio() {
        val h = Harness()
        h.turn.speak("Which number?", listenForReply = true)
        h.input.speech()
        h.input.finish("Mobile")
        h.output.finish()
        h.turn.close()
        h.dispatch()
        assertFalse(h.turn.listen())
        h.turn.speak("Another question", listenForReply = true)
        assertEquals(1, h.output.completions.size)
        assertFalse(h.input.active)
        assertFalse(h.output.active)
        assertTrue(h.heard.isEmpty())
    }
}
