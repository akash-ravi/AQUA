package com.example.jarvis.voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.SystemClock
import android.util.Base64
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.LinkedBlockingQueue
import kotlin.concurrent.thread

/**
 * ElevenLabs Turbo v2.5 over its WebSocket, played as the audio arrives. A connection is opened ahead of
 * each reply ([warm]) so speaking skips the TLS handshake. Public calls are on the main thread.
 */
internal class ElevenSpeaker(private val tokens: VoiceTokens, private val client: OkHttpClient) {
    enum class Result { DONE, INTERRUPTED, FAILED }

    /** One connection, ready before it has text to say; ElevenLabs closes it after [IDLE_SECONDS] unused. */
    private inner class Line(token: VoiceToken) : WebSocketListener() {
        val openedAt = SystemClock.elapsedRealtime()
        @Volatile var alive = true
        @Volatile var playback: Playback? = null
        val socket: WebSocket = client.newWebSocket(request(token), this)

        init {
            // OkHttp queues this until the connection opens.
            socket.send(JSONObject().put("text", " ").toString())
        }

        val fresh get() = alive && SystemClock.elapsedRealtime() - openedAt < (IDLE_SECONDS - 10) * 1000L

        fun say(text: String, into: Playback) {
            playback = into
            socket.send(JSONObject().put("text", "$text ").toString())
            socket.send(JSONObject().put("text", "").toString()) // end of input: generate everything now
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val message = runCatching { JSONObject(text) }.getOrNull() ?: return
            val audio = message.optString("audio").takeIf { !message.isNull("audio") && it.isNotEmpty() }
            if (audio != null) playback?.add(Base64.decode(audio, Base64.DEFAULT))
            if (message.optBoolean("isFinal")) playback?.end()
            if (message.has("error")) Log.w(TAG, "Text-to-speech error: ${message.optString("error")}")
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            alive = false
            if (playback?.cancelled == false) Log.w(TAG, "Text-to-speech connection failed", t)
            playback?.end()
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            alive = false
            webSocket.close(NORMAL_CLOSE, null)
            playback?.end()
        }

        fun close() {
            alive = false
            socket.close(NORMAL_CLOSE, null)
        }
    }

    /** Streams PCM chunks into an AudioTrack on its own thread; [onDone] is called once. */
    private inner class Playback(private val text: String, private val onDone: (Result) -> Unit) {
        @Volatile var cancelled = false
        @Volatile private var track: AudioTrack? = null
        @Volatile private var startedAt = 0L
        private val chunks = LinkedBlockingQueue<ByteArray>()
        private val envelope = Envelope()
        @Volatile private var line: Line? = null

        fun start(line: Line) {
            this.line = line
            line.say(text, this)
            if (cancelled) line.close() // cancelled while the token was on its way
        }

        fun add(pcm: ByteArray) = chunks.put(pcm)

        fun end() = chunks.put(END)

        fun cancel() {
            if (cancelled) return
            cancelled = true
            line?.close()
            // Silences at once and unblocks a pending write; the playback thread releases the track.
            runCatching { track?.pause(); track?.flush() }
            chunks.put(END)
        }

        /** Loudness of the audio being heard now, by elapsed time since the first chunk. */
        fun level(): Float {
            val started = startedAt
            if (started == 0L) return 0f
            return envelope.at((SystemClock.uptimeMillis() - started) * SAMPLE_RATE / 1000)
        }

        fun play() = thread(name = "jarvis-speak") {
            var frames = 0L
            var odd: Byte? = null // a chunk may split a 16-bit sample
            try {
                while (!cancelled) {
                    var pcm = chunks.take()
                    if (pcm === END || cancelled) break
                    odd?.let { pcm = byteArrayOf(it) + pcm }
                    odd = if (pcm.size % 2 == 1) pcm.last() else null
                    val size = pcm.size - pcm.size % 2
                    val output = track ?: newTrack().also {
                        track = it
                        it.play()
                        startedAt = SystemClock.uptimeMillis()
                    }
                    frames += size / 2
                    envelope.add(frames, pcmLoudness(pcm, size))
                    output.write(pcm, 0, size)
                }
                val output = track
                if (!cancelled && output != null) {
                    // The head position may reset once a stopped track drains, so wait out what is left instead.
                    val left = (frames - output.playbackHeadPosition.toLong()).coerceAtLeast(0)
                    output.stop() // plays out what is buffered
                    val drained = SystemClock.uptimeMillis() + minOf(left * 1000 / SAMPLE_RATE + DRAIN_MARGIN_MS, DRAIN_LIMIT_MS)
                    while (!cancelled && SystemClock.uptimeMillis() < drained) Thread.sleep(10)
                }
            } catch (error: RuntimeException) {
                Log.e(TAG, "Speech playback failed", error)
            } finally {
                val output = track
                track = null
                startedAt = 0L
                output?.release()
                if (current === this@Playback) current = null
                // With no audio at all the caller falls back to the phone's own voice.
                onDone(when {
                    cancelled -> Result.INTERRUPTED
                    output == null -> Result.FAILED
                    else -> Result.DONE
                })
            }
        }
    }

    @Volatile private var current: Playback? = null
    @Volatile private var standby: Line? = null
    @Volatile private var closed = false

    /** Opens the next reply's connection now, unless a usable one is already waiting. */
    fun warm() {
        if (closed || standby?.fresh == true) return
        tokens.take(KIND) { token ->
            if (token == null || closed) return@take
            val line = Line(token)
            val previous = standby
            standby = line
            previous?.close()
        }
    }

    /** Interrupts anything already playing; [onDone] runs on a background thread. */
    fun speak(text: String, onDone: (Result) -> Unit) {
        stop()
        val playback = Playback(text, onDone)
        current = playback
        playback.play()
        val ready = standby?.takeIf { it.fresh }
        standby?.takeIf { it !== ready }?.close()
        standby = null
        if (ready != null) {
            playback.start(ready)
        } else {
            tokens.take(KIND) { token ->
                when {
                    playback.cancelled -> Unit
                    token == null || closed -> playback.end()
                    else -> playback.start(Line(token))
                }
            }
        }
        warm()
    }

    fun stop() {
        current?.cancel()
        current = null
    }

    fun close() {
        closed = true
        stop()
        standby?.close()
        standby = null
    }

    fun level(): Float = current?.level() ?: 0f

    private fun request(token: VoiceToken) = Request.Builder().url(
        "wss://api.elevenlabs.io/v1/text-to-speech/${token.voiceId}/stream-input?model_id=eleven_turbo_v2_5" +
            "&output_format=pcm_$SAMPLE_RATE&auto_mode=true&inactivity_timeout=$IDLE_SECONDS" +
            "&single_use_token=${token.token}"
    ).build()

    private fun newTrack(): AudioTrack {
        val format = AudioFormat.Builder()
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .build()
        return AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build())
            .setAudioFormat(format)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
            .setBufferSizeInBytes(AudioTrack.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT))
            .build()
    }

    private class Envelope {
        private val ends = ArrayList<Long>()
        private val levels = ArrayList<Float>()

        @Synchronized fun add(endFrame: Long, level: Float) { ends += endFrame; levels += level }

        @Synchronized fun at(frame: Long): Float {
            val index = ends.indexOfFirst { it > frame }
            return if (index < 0) 0f else levels[index]
        }
    }

    private companion object {
        const val KIND = "speak"
        const val SAMPLE_RATE = 44100
        const val IDLE_SECONDS = 180 // ElevenLabs' maximum inactivity_timeout
        const val DRAIN_MARGIN_MS = 40L
        const val DRAIN_LIMIT_MS = 5_000L
        const val NORMAL_CLOSE = 1000
        const val TAG = "JarvisVoice"
        val END = ByteArray(0)

        fun pcmLoudness(pcm: ByteArray, size: Int) = loudness(FloatArray(size / 2) {
            ((pcm[2 * it].toInt() and 0xFF) or (pcm[2 * it + 1].toInt() shl 8)).toShort() / 32768f
        })
    }
}
