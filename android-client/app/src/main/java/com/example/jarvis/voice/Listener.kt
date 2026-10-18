package com.example.jarvis.voice

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.os.SystemClock
import android.util.Log
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * One utterance per [start]: records 16 kHz mono, lets Silero VAD find the end of speech,
 * then transcribes that segment with Moonshine. Sessions run one at a time on a worker thread.
 */
internal class Listener(private val models: SpeechModels) : VoiceInput {
    private class Session(
        val record: AudioRecord,
        val echoCanceller: AcousticEchoCanceler?,
        val onSpeech: () -> Unit,
        val onDone: (Session, String?) -> Unit,
    ) {
        @Volatile var cancelled = false
    }

    private val worker = Executors.newSingleThreadExecutor { Thread(it, "jarvis-listen") }
    private val current = AtomicReference<Session?>()
    override val echoCancellationActive: Boolean get() = current.get()?.echoCanceller != null
    @Volatile override var playbackActive = false
    @Volatile var level = 0f
        private set

    /** Starts recording immediately (audio is buffered while models finish loading). Main thread. */
    @SuppressLint("MissingPermission") // Voice checks RECORD_AUDIO before calling.
    override fun start(onSpeech: () -> Unit, onDone: (String?) -> Unit): Boolean {
        stop()
        val bufferBytes = maxOf(
            AudioRecord.getMinBufferSize(SpeechModels.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT),
            FRAME * Short.SIZE_BYTES * 4,
        )
        val record = try {
            AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, SpeechModels.SAMPLE_RATE,
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
        worker.execute { run(session) }
        return true
    }

    /** Returns true if a session was active; its result is discarded. */
    override fun stop(): Boolean {
        val session = current.getAndSet(null) ?: return false
        session.cancelled = true
        // Unblocks a pending read; the worker releases the recorder.
        runCatching { session.record.stop() }
        level = 0f
        return true
    }

    fun close() {
        stop()
        worker.shutdown()
    }

    fun awaitClosed() {
        // Do not free native models while inference is still running, even after a slow shutdown.
        while (!worker.awaitTermination(CLOSE_TIMEOUT_SECONDS, TimeUnit.SECONDS)) Unit
    }

    private fun run(session: Session) {
        var text: String? = null
        try {
            text = transcribe(session)
        } catch (error: RuntimeException) {
            Log.e(TAG, "Recognition failed", error)
        } finally {
            session.echoCanceller?.release()
            session.record.release()
            level = 0f
            if (!session.cancelled) session.onDone(session, text)
        }
    }

    private fun transcribe(session: Session): String? {
        val started = SystemClock.uptimeMillis()
        var silenceStarted = started
        val backlog = ArrayList<FloatArray>()
        var heardSpeech = false
        var vadReset = false
        while (!session.cancelled) {
            val frame = ShortArray(FRAME)
            val read = session.record.read(frame, 0, FRAME, AudioRecord.READ_BLOCKING)
            if (read <= 0) return null
            val samples = FloatArray(read) { frame[it] / 32768f }
            level = loudness(samples)
            val elapsed = SystemClock.uptimeMillis() - started
            val loaded = models.now()
            if (loaded == null) {
                if (elapsed > MODEL_WAIT_MS) return null
                backlog += samples
                continue
            }
            val vad = loaded.vad
            if (!vadReset) { vad.reset(); vadReset = true }
            backlog.forEach(vad::acceptWaveform)
            backlog.clear()
            vad.acceptWaveform(samples)
            if (!heardSpeech && (vad.isSpeechDetected() || !vad.empty())) {
                heardSpeech = true
                session.onSpeech()
            }
            if (!vad.empty()) {
                val segment = vad.front().samples
                vad.clear()
                level = 0f
                return decode(loaded, segment)
            }
            // A long spoken question must not consume the user's response timeout.
            val now = SystemClock.uptimeMillis()
            if (playbackActive) silenceStarted = now
            if (!heardSpeech && now - silenceStarted > NO_SPEECH_TIMEOUT_MS) return null
        }
        return null
    }

    private fun decode(models: SpeechModels.Loaded, samples: FloatArray): String? {
        val stream = models.recognizer.createStream()
        try {
            stream.acceptWaveform(samples, SpeechModels.SAMPLE_RATE)
            models.recognizer.decode(stream)
            return models.recognizer.getResult(stream).text.trim().ifEmpty { null }
        } finally {
            stream.release()
        }
    }

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
        const val FRAME = 512 // 32 ms at 16 kHz, Silero's window size
        const val NO_SPEECH_TIMEOUT_MS = 8_000L
        const val MODEL_WAIT_MS = 15_000L
        const val CLOSE_TIMEOUT_SECONDS = 5L
        const val TAG = "JarvisVoice"
    }
}
