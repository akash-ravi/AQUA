package com.example.jarvis.voice

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

/** Free platform speech: on-device recognition in, the best installed offline voice out. Main thread only. */
class Voice(
    private val context: Context,
    private val onHeard: (String) -> Unit,
    private val onListening: (Boolean) -> Unit,
) {
    private val handler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var afterSpeech: () -> Unit = {}
    private var speechReady = false
    private val speech: TextToSpeech = TextToSpeech(context) { status ->
        speechReady = status == TextToSpeech.SUCCESS
        if (speechReady) useBestVoice()
    }

    private val recognitionListener = object : RecognitionListener {
        override fun onResults(results: Bundle?) {
            finishListening()
            results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.let(onHeard)
        }

        override fun onError(error: Int) = finishListening()
        override fun onReadyForSpeech(params: Bundle?) = Unit
        override fun onBeginningOfSpeech() = Unit
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit
        override fun onPartialResults(partialResults: Bundle?) = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    init {
        speech.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit

            override fun onDone(utteranceId: String?) {
                handler.post {
                    val next = afterSpeech
                    afterSpeech = {}
                    next()
                }
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) = Unit
        })
    }

    fun listen(): Boolean {
        val allowed = context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (!allowed || !SpeechRecognizer.isRecognitionAvailable(context)) return false
        cancel()
        val onDevice = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        val next = if (onDevice) SpeechRecognizer.createOnDeviceSpeechRecognizer(context) else SpeechRecognizer.createSpeechRecognizer(context)
        next.setRecognitionListener(recognitionListener)
        next.startListening(
            Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM),
        )
        recognizer = next
        onListening(true)
        return true
    }

    fun speak(text: String, then: () -> Unit = {}) {
        if (!speechReady || text.isBlank()) { then(); return }
        afterSpeech = then
        speech.speak(text.take(TextToSpeech.getMaxSpeechInputLength()), TextToSpeech.QUEUE_FLUSH, null, UTTERANCE_ID)
    }

    fun cancel() {
        speech.stop()
        afterSpeech = {}
        if (recognizer != null) finishListening()
    }

    fun close() {
        cancel()
        speech.shutdown()
    }

    private fun finishListening() {
        recognizer?.destroy()
        recognizer = null
        onListening(false)
    }

    private fun useBestVoice() {
        val language = Locale.getDefault().language
        speech.voices.orEmpty()
            .filter { it.locale.language == language && !it.isNetworkConnectionRequired }
            .filter { TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED !in it.features }
            .maxByOrNull { it.quality }
            ?.let { speech.setVoice(it) }
    }

    private companion object {
        const val UTTERANCE_ID = "jarvis"
    }
}
