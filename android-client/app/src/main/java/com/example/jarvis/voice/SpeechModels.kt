package com.example.jarvis.voice

import android.content.Context
import android.util.Log
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineMoonshineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.SileroVadModelConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * On-device speech models, bundled as APK assets by the `speechModels` Gradle task:
 * Silero VAD for end of speech and Moonshine v2 base (English) for recognition, each loaded on its own thread.
 */
class SpeechModels(context: Context) {
    class Loaded(val vad: Vad, val recognizer: OfflineRecognizer)

    private class Slot<T : Any>(private val name: String, private val release: (T) -> Unit) {
        private val ready = CountDownLatch(1)
        @Volatile var value: T? = null
            private set
        @Volatile private var closed = false

        fun load(create: () -> T) {
            thread(name = "jarvis-load-$name") {
                try {
                    val created = create()
                    if (closed) release(created) else value = created
                } catch (error: Throwable) {
                    Log.e(TAG, "Speech model $name failed to load", error)
                } finally {
                    ready.countDown()
                }
            }
        }

        fun await(): T? {
            ready.await(LOAD_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            return value
        }

        fun close() {
            closed = true
            value?.let(release)
            value = null
        }
    }

    private val appContext = context.applicationContext
    private val vad = Slot<Vad>("vad") { it.release() }
    private val recognizer = Slot<OfflineRecognizer>("recognizer") { it.release() }

    init {
        val assets = appContext.assets
        vad.load { Vad(assets, vadConfig()) }
        recognizer.load { OfflineRecognizer(assets, recognizerConfig()) }
    }

    /** Null until the VAD and recognizer have loaded, or after a load failure; never blocks. */
    fun now(): Loaded? {
        val loadedVad = vad.value ?: return null
        val loadedRecognizer = recognizer.value ?: return null
        return Loaded(loadedVad, loadedRecognizer)
    }

    fun close() {
        vad.close()
        recognizer.close()
    }

    private fun vadConfig() = VadModelConfig(
        sileroVadModelConfig = SileroVadModelConfig(
            model = "silero_vad.onnx",
            threshold = 0.5f,
            minSilenceDuration = END_OF_SPEECH_SECONDS,
            minSpeechDuration = 0.25f,
            maxSpeechDuration = MAX_UTTERANCE_SECONDS,
        ),
        sampleRate = SAMPLE_RATE,
        numThreads = 1,
    )

    private fun recognizerConfig() = OfflineRecognizerConfig(
        modelConfig = OfflineModelConfig(
            moonshine = OfflineMoonshineModelConfig(
                encoder = "$MOONSHINE/encoder_model.ort",
                mergedDecoder = "$MOONSHINE/decoder_model_merged.ort",
            ),
            tokens = "$MOONSHINE/tokens.txt",
            numThreads = 2,
        ),
    )

    companion object {
        const val SAMPLE_RATE = 16000
        const val END_OF_SPEECH_SECONDS = 1.0f
        const val MAX_UTTERANCE_SECONDS = 20f
        private const val LOAD_TIMEOUT_SECONDS = 30L
        private const val MOONSHINE = "moonshine"
        private const val TAG = "JarvisVoice"
    }
}
