package com.example.jarvis.voice

import android.content.Context
import android.media.AudioAttributes
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice as TtsVoice
import android.util.Log
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.exp

/**
 * The phone's own text-to-speech engine (Google's on Pixel): it starts talking within a few
 * hundred milliseconds, so it is the primary voice. It exposes no audio, so loudness for the
 * orb and edge glow is a pulse on each spoken word.
 */
internal class SystemSpeaker(context: Context) {
    private val main = Handler(Looper.getMainLooper())
    private val done = ConcurrentHashMap<String, (Boolean) -> Unit>()
    @Volatile private var ready = false
    @Volatile private var active: String? = null
    @Volatile private var wordAt = 0L
    private lateinit var tts: TextToSpeech

    init {
        // onInit may run inside the constructor when binding fails, before tts is assigned.
        tts = TextToSpeech(context.applicationContext) { status -> main.post { ready = configure(status) } }
    }

    private fun configure(status: Int): Boolean {
        if (status != TextToSpeech.SUCCESS) {
            Log.w(TAG, "System text-to-speech unavailable (status $status)")
            return false
        }
        tts.setAudioAttributes(AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build())
        if (tts.setLanguage(Locale.US) < TextToSpeech.LANG_AVAILABLE) {
            Log.w(TAG, "System text-to-speech has no English voice")
            return false
        }
        // Prefer the best offline English voice; the engine default is used if none is listed.
        runCatching {
            tts.voices.orEmpty()
                .filter { it.locale.language == "en" && !it.isNetworkConnectionRequired && it.quality >= TtsVoice.QUALITY_HIGH }
                .maxByOrNull { it.quality + if (it.locale.country == "US") 1 else 0 }
                ?.let { tts.setVoice(it) }
        }
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String) { active = id; wordAt = SystemClock.uptimeMillis() }
            override fun onDone(id: String) = finish(id, true)
            @Deprecated("Deprecated in Java")
            override fun onError(id: String) = finish(id, true)
            override fun onError(id: String, errorCode: Int) = finish(id, true)
            override fun onStop(id: String, interrupted: Boolean) = finish(id, false)
            override fun onRangeStart(id: String, start: Int, end: Int, frame: Int) { wordAt = SystemClock.uptimeMillis() }
        })
        return true
    }

    /** Errors count as finished so a question still reopens the mic. */
    private fun finish(id: String, completed: Boolean) {
        if (active == id) active = null
        done.remove(id)?.invoke(completed)
    }

    /**
     * Interrupts anything already playing. [onDone] gets true when the text finished (or failed
     * mid-way) and false when it was interrupted. Returns false if the engine is not usable.
     */
    fun speak(text: String, onDone: (Boolean) -> Unit): Boolean {
        if (!ready) return false
        val id = UUID.randomUUID().toString()
        done[id] = onDone
        if (tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, id) == TextToSpeech.SUCCESS) return true
        done.remove(id)
        return false
    }

    fun stop() {
        if (ready) tts.stop()
    }

    fun close() {
        ready = false
        done.clear()
        tts.stop()
        tts.shutdown()
    }

    /** A bright pulse at each word that decays before the next, so the orb moves with the speech. */
    fun level(): Float {
        if (active == null) return 0f
        val sinceWord = SystemClock.uptimeMillis() - wordAt
        return 0.25f + 0.45f * exp(-sinceWord / WORD_DECAY_MS)
    }

    private companion object {
        const val WORD_DECAY_MS = 140f
        const val TAG = "JarvisVoice"
    }
}
