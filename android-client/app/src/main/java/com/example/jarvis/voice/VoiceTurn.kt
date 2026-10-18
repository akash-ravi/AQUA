package com.example.jarvis.voice

internal interface VoiceInput {
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

    val speaking: Boolean get() = playback != null

    /** An explicit mic tap interrupts playback. */
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
        // The mic stays off while Jarvis talks: echo leaking past AEC used to cut its own answers off.
        stopListening()
        output.speak(text) {
            dispatch {
                if (!closed && playback === token) {
                    playback = null
                    input.playbackActive = false
                    if (resumeListening && recording == null) listen()
                }
            }
        }
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
