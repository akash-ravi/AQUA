package com.example.jarvis.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import kotlin.concurrent.thread

/**
 * Open-source, on-device voice: Silero VAD + Moonshine in, Kokoro out (see [SpeechModels]).
 * Public calls and callbacks are on the main thread; audio and inference run on workers.
 */
class Voice(
    private val context: Context,
    private val onHeard: (String) -> Unit,
    private val onListening: (Boolean) -> Unit,
) {
    private val handler = Handler(Looper.getMainLooper())
    private val models = SpeechModels(context)
    private val listener = Listener(models)
    private val speaker = Speaker(models)
    private var utterance: Any? = null

    fun listen(): Boolean {
        val allowed = context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (!allowed) return false
        cancel()
        val started = listener.start { text ->
            handler.post {
                onListening(false)
                text?.let(onHeard)
            }
        }
        if (started) onListening(true)
        return started
    }

    fun speak(text: String, then: () -> Unit = {}) {
        if (text.isBlank()) { then(); return }
        if (listener.stop()) onListening(false)
        val token = Any()
        utterance = token
        speaker.speak(text) {
            handler.post {
                if (utterance !== token) return@post
                utterance = null
                then()
            }
        }
    }

    /** Status chatter: skipped while something is already being said, so updates never pile up. */
    fun speakIfIdle(text: String) {
        if (utterance == null) speak(text)
    }

    fun cancel() {
        speaker.stop()
        utterance = null
        if (listener.stop()) onListening(false)
    }

    fun close() {
        cancel()
        listener.close()
        speaker.close()
        // Native models are freed only after the workers stop touching them.
        thread(name = "jarvis-speech-close") {
            listener.awaitClosed()
            speaker.awaitClosed()
            models.close()
        }
    }

    /** Current mic or speech loudness, 0..1, for the orb and edge glow. Safe from any thread. */
    fun level(): Float = maxOf(listener.level, speaker.level())
}
