package com.example.jarvis.voice

internal interface VoiceInput {
    val echoCancellationActive: Boolean
    var playbackActive: Boolean
    fun start(onSpeech: () -> Unit, onDone: (String?) -> Unit): Boolean
    fun stop(): Boolean
}

internal interface VoiceOutput {
    fun speak(text: String, onDone: () -> Unit)
    fun stop()
}

/** Main-thread turn ownership; queued audio callbacks cannot revive a cancelled turn. */
internal class VoiceTurn(
    private val input: VoiceInput,
    private val output: VoiceOutput,
    private val canListen: () -> Boolean,
    private val dispatch: (() -> Unit) -> Unit,
    private val onHeard: (String) -> Unit,
    private val onListening: (Boolean) -> Unit,
) {
    private var recording: Any? = null
    private var playback: Any? = null
    private var userSpeaking = false
    private var closed = false

    /** An explicit mic tap interrupts playback, including on devices without AEC. */
    fun listen(): Boolean {
        if (closed || !canListen()) return false
        stopSpeaking()
        return recording != null || startListening()
    }

    fun speak(text: String, listenForReply: Boolean = false) {
        if (closed || userSpeaking) return
        stopSpeaking()
        if (text.isBlank()) {
            if (listenForReply) listen()
            return
        }
        val resumeListening = listenForReply || recording != null
        val token = Any()
        playback = token
        input.playbackActive = true
        if (resumeListening && recording == null && canListen()) startListening()
        // Never feed unprotected speaker output back into the recognizer.
        if (recording != null && !input.echoCancellationActive) stopListening()
        output.speak(text) {
            dispatch {
                if (!closed && playback === token) {
                    playback = null
                    input.playbackActive = false
                    // With AEC the same recording continues, preserving the start of the answer.
                    if (resumeListening && recording == null) listen()
                }
            }
        }
    }

    fun speakIfIdle(text: String) {
        if (playback == null && !userSpeaking) speak(text)
    }

    fun cancel() {
        stopSpeaking()
        stopListening()
    }

    fun close() {
        closed = true
        cancel()
    }

    private fun startListening(): Boolean {
        val token = Any()
        recording = token
        userSpeaking = false
        val started = input.start(
            onSpeech = {
                dispatch {
                    if (!closed && recording === token) {
                        userSpeaking = true
                        stopSpeaking()
                    }
                }
            },
            onDone = { text ->
                dispatch {
                    if (!closed && recording === token) {
                        recording = null
                        userSpeaking = false
                        onListening(false)
                        if (!text.isNullOrBlank()) {
                            stopSpeaking()
                            onHeard(text)
                        }
                    }
                }
            },
        )
        if (!started) {
            recording = null
            return false
        }
        onListening(true)
        return true
    }

    private fun stopSpeaking() {
        playback = null
        input.playbackActive = false
        output.stop()
    }

    private fun stopListening() {
        val wasListening = recording != null
        recording = null
        userSpeaking = false
        input.stop()
        if (wasListening) onListening(false)
    }
}
