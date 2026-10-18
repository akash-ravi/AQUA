package com.example.jarvis.voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.SystemClock
import android.util.Log
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Kokoro speech, streamed: each sentence plays as soon as it is synthesized. A per-window
 * loudness envelope is matched to the playback head so visuals follow what is audible.
 */
internal class Speaker(private val models: SpeechModels) : VoiceOutput {
    private val worker = Executors.newSingleThreadExecutor { Thread(it, "jarvis-speak") }
    private val generation = AtomicInteger()
    private val envelope = ArrayList<Float>()
    @Volatile private var track: AudioTrack? = null

    /**
     * [onDone] runs on the worker thread unless this utterance was interrupted, including when
     * the models are missing, so callers never wait forever on speech that cannot play.
     */
    override fun speak(text: String, onDone: () -> Unit) {
        val id = generation.incrementAndGet()
        worker.execute {
            try {
                play(id, text)
            } catch (error: RuntimeException) {
                Log.e(TAG, "Speech failed", error)
            }
            if (id == generation.get()) onDone()
        }
    }

    override fun stop() {
        generation.incrementAndGet()
        track?.let { runCatching { it.pause(); it.flush() } }
    }

    fun close() {
        stop()
        worker.shutdown()
    }

    fun awaitClosed() {
        while (!worker.awaitTermination(CLOSE_TIMEOUT_SECONDS, TimeUnit.SECONDS)) Unit
    }

    fun level(): Float {
        val playing = track ?: return 0f
        val head = runCatching { playing.playbackHeadPosition }.getOrDefault(0)
        return synchronized(envelope) { envelope.getOrNull(head / WINDOW) ?: 0f }
    }

    private fun play(id: Int, text: String) {
        val tts = models.await()?.tts
        if (tts == null) { Log.w(TAG, "Kokoro is not loaded; skipping speech"); return }
        if (id != generation.get()) return
        val sampleRate = tts.sampleRate()
        val output = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build())
            .setAudioFormat(AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                .setSampleRate(sampleRate)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                .build())
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_FLOAT))
            .build()
        synchronized(envelope) { envelope.clear() }
        var written = 0
        var pending = FloatArray(0)
        track = output
        try {
            output.play()
            tts.generateWithCallback(text, sid = VOICE_ID, speed = 1.0f) { samples ->
                if (id != generation.get()) return@generateWithCallback 0
                pending = record(pending + samples)
                if (!writeAll(output, samples, id)) return@generateWithCallback 0
                written += samples.size
                1
            }
            // Silence pushes a short tail past the device's start threshold; then wait until the
            // spoken samples are audible, bounded in case the head position never reaches them.
            writeAll(output, FloatArray(output.bufferSizeInFrames), id)
            val deadline = SystemClock.uptimeMillis() + written * 1000L / sampleRate + DRAIN_GRACE_MS
            while (id == generation.get() && output.playbackHeadPosition < written &&
                SystemClock.uptimeMillis() < deadline) Thread.sleep(POLL_MS)
        } finally {
            track = null
            runCatching { output.stop() }
            output.release()
        }
    }

    /** Non-blocking so [stop] can pause the track without stranding this thread in write(). */
    private fun writeAll(output: AudioTrack, samples: FloatArray, id: Int): Boolean {
        var offset = 0
        while (offset < samples.size) {
            if (id != generation.get()) return false
            val count = output.write(samples, offset, samples.size - offset, AudioTrack.WRITE_NON_BLOCKING)
            if (count < 0) return false
            offset += count
            if (count == 0) Thread.sleep(POLL_MS)
        }
        return true
    }

    /** Appends full loudness windows and returns the leftover samples. */
    private fun record(samples: FloatArray): FloatArray {
        val full = samples.size / WINDOW
        synchronized(envelope) {
            for (i in 0 until full) envelope += loudness(samples.copyOfRange(i * WINDOW, (i + 1) * WINDOW))
        }
        return samples.copyOfRange(full * WINDOW, samples.size)
    }

    private companion object {
        const val VOICE_ID = 1 // af_bella in kokoro-en-v0_19
        const val WINDOW = 512
        const val POLL_MS = 20L
        const val DRAIN_GRACE_MS = 2_000L
        const val CLOSE_TIMEOUT_SECONDS = 5L
        const val TAG = "JarvisVoice"
    }
}
