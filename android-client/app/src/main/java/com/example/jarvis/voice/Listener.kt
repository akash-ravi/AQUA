package com.example.jarvis.voice

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.os.SystemClock
import android.util.Base64
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

private val DANGLING_WORDS = setOf(
    "to", "and", "or", "but", "the", "a", "an", "of", "for", "with", "in", "on", "at", "from", "my", "so",
    "uh", "um", "like", "then", "is", "want", "go",
)

/** A pause mid-sentence ("I want to go to...", "I just want-") is not the end of the request. */
internal fun soundsUnfinished(transcript: String): Boolean {
    val text = transcript.trim()
    if (text.endsWith("...") || text.endsWith("…") || text.endsWith("-") || text.endsWith("—") || text.endsWith(",")) return true
    if (text.lastOrNull()?.isLetter() != true) return false
    return text.substringAfterLast(' ').lowercase() in DANGLING_WORDS
}

/**
 * One utterance per [start]: records 16 kHz mono and streams it to ElevenLabs Scribe v2 Realtime, whose VAD
 * commits the transcript once the user stops talking. Recording starts at once; audio captured while the
 * socket connects is sent as soon as the session opens. Sessions run one at a time on a worker thread.
 */
internal class Listener(private val tokens: VoiceTokens, private val client: OkHttpClient) : VoiceInput {
    private inner class Session(
        val record: AudioRecord,
        val echoCanceller: AcousticEchoCanceler?,
        val onSpeech: () -> Unit,
        val onDone: (Session, String?) -> Unit,
    ) {
        @Volatile var cancelled = false
        @Volatile var socket: WebSocket? = null
        @Volatile var open = false
        val heardSpeech = AtomicBoolean(false)
        @Volatile var unfinishedText = ""
        @Volatile var lastSpeechMs = 0L
        private val finished = AtomicBoolean(false)

        /** First call wins: the transcript, or null for silence, failure or cancel. Ends the recording. */
        fun finishWithWhatWasSaid() = finish(unfinishedText.ifEmpty { null })

        fun finish(text: String?) {
            if (!finished.compareAndSet(false, true)) return
            runCatching { record.stop() } // unblocks the worker's read
            socket?.close(NORMAL_CLOSE, null)
            if (!cancelled) onDone(this, text)
        }

        fun connect(token: VoiceToken) {
            if (finished.get()) return
            socket = client.newWebSocket(request(token), listener)
            // finish() may have run before the socket was stored.
            if (finished.get()) socket?.close(NORMAL_CLOSE, null)
        }

        val listener = object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                val message = runCatching { JSONObject(text) }.getOrNull() ?: return
                val type = message.optString("message_type")
                val transcript = message.optString("text").trim()
                when {
                    type == "session_started" -> open = true
                    type == "partial_transcript" -> if (transcript.isNotEmpty()) {
                        lastSpeechMs = SystemClock.uptimeMillis()
                        if (heardSpeech.compareAndSet(false, true)) onSpeech()
                    }
                    // VAD can commit a cough or noise as empty text; keep listening for real words.
                    type == "committed_transcript" -> if (transcript.isNotEmpty()) {
                        val said = "$unfinishedText $transcript".trim()
                        if (soundsUnfinished(said)) {
                            unfinishedText = said
                            lastSpeechMs = SystemClock.uptimeMillis()
                        } else {
                            finish(said)
                        }
                    }
                    message.has("error") -> {
                        Log.w(TAG, "Speech-to-text $type: ${message.optString("error")}")
                        finishWithWhatWasSaid()
                    }
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (!cancelled) Log.w(TAG, "Speech-to-text connection failed", t)
                finish(null)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) = finishWithWhatWasSaid()
        }
    }

    private val worker = Executors.newSingleThreadExecutor { Thread(it, "jarvis-listen") }
    private val current = AtomicReference<Session?>()
    override val echoCancellationActive: Boolean get() = current.get()?.echoCanceller != null
    @Volatile override var playbackActive = false
    @Volatile var level = 0f
        private set

    /** Starts recording immediately and connects in parallel. Main thread. */
    @SuppressLint("MissingPermission") // Voice checks RECORD_AUDIO before calling.
    override fun start(onSpeech: () -> Unit, onDone: (String?) -> Unit): Boolean {
        stop()
        val bufferBytes = maxOf(
            AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT),
            FRAME * Short.SIZE_BYTES * 4,
        )
        val record = try {
            AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferBytes)
        } catch (error: RuntimeException) {
            Log.e(TAG, "Microphone unavailable", error)
            return false
        }
        if (record.state != AudioRecord.STATE_INITIALIZED) { record.release(); return false }
        val echoCanceller = enableEchoCancellation(record.audioSessionId)
        val session = Session(record, echoCanceller, onSpeech) { session, text ->
            if (current.compareAndSet(session, null)) onDone(text)
        }
        try {
            record.startRecording()
        } catch (error: RuntimeException) {
            Log.e(TAG, "Microphone could not start", error)
            echoCanceller?.release()
            record.release()
            return false
        }
        current.set(session)
        tokens.take(KIND) { token -> if (token == null) session.finish(null) else session.connect(token) }
        worker.execute { run(session) }
        return true
    }

    /** Returns true if a session was active; its result is discarded. */
    override fun stop(): Boolean {
        val session = current.getAndSet(null) ?: return false
        session.cancelled = true
        session.finish(null)
        level = 0f
        return true
    }

    /** Fetches the next session's token ahead, so speaking right away doesn't wait on it. */
    fun prefetch() = tokens.prefetch(KIND)

    fun close() {
        stop()
        worker.shutdown()
    }

    private fun request(token: VoiceToken) = Request.Builder().url(
        "$STT_URL?model_id=scribe_v2_realtime&token=${token.token}&audio_format=pcm_$SAMPLE_RATE&language_code=en" +
            "&commit_strategy=vad&vad_silence_threshold_secs=$END_OF_SPEECH_SECONDS"
    ).build()

    private fun run(session: Session) {
        try {
            stream(session)
        } catch (error: RuntimeException) {
            Log.e(TAG, "Recognition failed", error)
        } finally {
            session.finishWithWhatWasSaid()
            session.echoCanceller?.release()
            session.record.release()
            level = 0f
        }
    }

    private fun stream(session: Session) {
        val started = SystemClock.uptimeMillis()
        var silenceStarted = started
        var speechStarted = 0L
        var committed = false
        val backlog = ArrayList<ByteArray>()
        val frame = ShortArray(FRAME)
        while (!session.cancelled) {
            val read = session.record.read(frame, 0, FRAME, AudioRecord.READ_BLOCKING)
            if (read <= 0) return
            level = loudness(FloatArray(read) { frame[it] / 32768f })
            val pcm = ByteBuffer.allocate(read * Short.SIZE_BYTES).order(ByteOrder.LITTLE_ENDIAN)
            pcm.asShortBuffer().put(frame, 0, read)
            backlog += pcm.array()
            val now = SystemClock.uptimeMillis()
            val socket = session.socket
            if (session.open && socket != null) {
                backlog.forEach { socket.send(chunk(it, commit = false)) }
                backlog.clear()
            } else if (now - started > CONNECT_TIMEOUT_MS) {
                Log.w(TAG, "Speech-to-text did not connect in time")
                return
            }
            if (session.heardSpeech.get()) {
                if (speechStarted == 0L) speechStarted = now
                if (session.unfinishedText.isNotEmpty() && now - session.lastSpeechMs > UNFINISHED_PAUSE_MS) return
                // A long utterance is cut at the limit; the transcript of what was said still arrives.
                if (!committed && now - speechStarted > MAX_UTTERANCE_MS && socket != null) {
                    socket.send(chunk(ByteArray(0), commit = true))
                    committed = true
                }
                if (now - speechStarted > MAX_UTTERANCE_MS + COMMIT_WAIT_MS) return
                continue
            }
            // A long spoken question must not consume the user's response timeout.
            if (playbackActive) silenceStarted = now
            if (now - silenceStarted > NO_SPEECH_TIMEOUT_MS) return
        }
    }

    private fun chunk(pcm: ByteArray, commit: Boolean) = JSONObject()
        .put("message_type", "input_audio_chunk")
        .put("audio_base_64", Base64.encodeToString(pcm, Base64.NO_WRAP))
        .put("commit", commit)
        .put("sample_rate", SAMPLE_RATE)
        .toString()

    private fun enableEchoCancellation(audioSessionId: Int): AcousticEchoCanceler? {
        var effect: AcousticEchoCanceler? = null
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                effect = AcousticEchoCanceler.create(audioSessionId)
                effect?.setEnabled(true)
                if (effect?.enabled == true) return effect
            }
        } catch (error: RuntimeException) {
            Log.w(TAG, "Echo cancellation unavailable; using speak-then-listen", error)
        }
        effect?.release()
        return null
    }

    private companion object {
        const val KIND = "listen"
        const val STT_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
        const val SAMPLE_RATE = 16000
        const val FRAME = 800 // 50 ms: small chunks let the server's VAD hear the end of speech sooner
        const val END_OF_SPEECH_SECONDS = 1.0f
        const val MAX_UTTERANCE_MS = 20_000L
        const val NO_SPEECH_TIMEOUT_MS = 8_000L
        const val UNFINISHED_PAUSE_MS = 4_000L
        const val COMMIT_WAIT_MS = 3_000L
        const val CONNECT_TIMEOUT_MS = 5_000L
        const val NORMAL_CLOSE = 1000
        const val TAG = "JarvisVoice"
    }
}
