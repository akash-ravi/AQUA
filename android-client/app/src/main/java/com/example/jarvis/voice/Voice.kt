package com.example.jarvis.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import kotlin.concurrent.thread

/**
 * On-device voice: Silero VAD + Moonshine in (see [SpeechModels]); the phone's own text-to-speech out.
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
    private val system = SystemSpeaker(context)
    private val output = object : VoiceOutput {
        override fun speak(text: String, onDone: () -> Unit) {
            if (!system.speak(text) { completed -> if (completed) onDone() }) onDone()
        }

        override fun stop() = system.stop()
    }
    private val turn = VoiceTurn(listener, output,
        canListen = { context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED },
        dispatch = { action -> handler.post { action() } },
        onHeard = onHeard, onListening = onListening)

    fun listen(): Boolean = turn.listen()

    /** With [listenForReply], the mic stays open during playback when AEC is available. */
    fun speak(text: String, listenForReply: Boolean = false) = turn.speak(text, listenForReply)

    /** Status chatter: skipped while something is already being said, so updates never pile up. */
    fun speakIfIdle(text: String) = turn.speakIfIdle(text)

    fun cancel() = turn.cancel()

    fun close() {
        turn.close()
        listener.close()
        system.close()
        // Native models are freed only after the workers stop touching them.
        thread(name = "jarvis-speech-close") {
            listener.awaitClosed()
            models.close()
        }
    }

    /** Current mic or speech loudness, 0..1, for the orb and edge glow. Safe from any thread. */
    fun level(): Float = maxOf(listener.level, system.level())
}
