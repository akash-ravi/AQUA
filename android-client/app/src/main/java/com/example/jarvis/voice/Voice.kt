package com.example.jarvis.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import com.example.jarvis.BuildConfig
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * ElevenLabs voice: Scribe v2 Realtime in (see [Listener]), Flash v2.5 out (see [ElevenSpeaker]), both streamed
 * straight from the phone with single-use tokens from the Mac. The phone's own text-to-speech speaks when
 * ElevenLabs can't. Public calls and callbacks are on the main thread; audio runs on workers.
 */
class Voice(
    private val context: Context,
    private val onHeard: (String) -> Unit,
    private val onListening: (Boolean) -> Unit,
) {
    private val handler = Handler(Looper.getMainLooper())
    private val client = OkHttpClient.Builder()
        .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private val tokens = VoiceTokens(BuildConfig.AGENT_URL, client)
    private val listener = Listener(tokens, client)
    private val eleven = ElevenSpeaker(tokens, client)
    private val system = SystemSpeaker(context)
    private val output = object : VoiceOutput {
        private var utterance = 0

        override fun speak(text: String, onDone: () -> Unit) {
            val id = ++utterance
            eleven.speak(text) { result ->
                when (result) {
                    ElevenSpeaker.Result.DONE -> onDone()
                    ElevenSpeaker.Result.INTERRUPTED -> Unit
                    ElevenSpeaker.Result.FAILED -> handler.post {
                        if (id != utterance) return@post // stopped or replaced meanwhile
                        if (!system.speak(text) { completed -> if (completed) onDone() }) onDone()
                    }
                }
            }
        }

        override fun stop() {
            utterance++
            eleven.stop()
            system.stop()
        }
    }
    private val turn = VoiceTurn(listener, output,
        canListen = { context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED },
        dispatch = { action -> handler.post { action() } },
        onHeard = onHeard, onListening = onListening)

    init {
        listener.prefetch()
    }

    /** Also opens the reply's speech connection, since Jarvis answers right after hearing a request. */
    fun listen(): Boolean = turn.listen().also { if (it) eleven.warm() }

    /** With [listenForReply], the mic stays open during playback when AEC is available. */
    fun speak(text: String, listenForReply: Boolean = false) {
        turn.speak(text, listenForReply)
        listener.prefetch()
    }

    fun cancel() = turn.cancel()

    fun close() {
        turn.close()
        listener.close()
        eleven.close()
        system.close()
        client.dispatcher.executorService.shutdown()
    }

    /** Current mic or speech loudness, 0..1, for the orb and edge glow. Safe from any thread. */
    fun level(): Float = maxOf(listener.level, eleven.level(), system.level())

    private companion object {
        const val CONNECT_TIMEOUT_SECONDS = 5L
    }
}
