package com.example.jarvis.voice

import android.content.Context
import android.content.res.AssetManager
import android.util.Log
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineMoonshineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.OfflineTtsConfig
import com.k2fsa.sherpa.onnx.OfflineTtsKokoroModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig
import com.k2fsa.sherpa.onnx.SileroVadModelConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * On-device speech models, bundled as APK assets by the `speechModels` Gradle task:
 * Silero VAD for end of speech, Moonshine v2 base (English) for recognition, Kokoro int8 for speech.
 * Loading takes a second or two, so it starts in the background when the service connects.
 */
class SpeechModels(context: Context) {
    class Loaded(val vad: Vad, val recognizer: OfflineRecognizer, val tts: OfflineTts)

    private val appContext = context.applicationContext
    private val ready = CountDownLatch(1)
    @Volatile private var loaded: Loaded? = null
    @Volatile private var closed = false

    init {
        thread(name = "jarvis-speech-load") {
            try {
                val assets = appContext.assets
                val result = Loaded(Vad(assets, vadConfig()), OfflineRecognizer(assets, recognizerConfig()),
                    OfflineTts(assets, ttsConfig(espeakData(assets))))
                if (closed) release(result) else loaded = result
            } catch (error: Throwable) {
                Log.e(TAG, "Speech models failed to load", error)
            } finally {
                ready.countDown()
            }
        }
    }

    /** Null while loading or after a load failure; never blocks. */
    fun now(): Loaded? = loaded

    /** Blocks the calling worker thread until loading finishes. */
    fun await(): Loaded? {
        ready.await(LOAD_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        return loaded
    }

    fun close() {
        closed = true
        loaded?.let(::release)
        loaded = null
    }

    private fun release(models: Loaded) {
        models.vad.release()
        models.recognizer.release()
        models.tts.release()
    }

    /** espeak-ng reads its phoneme data from the filesystem, so copy it out of the APK once per version. */
    private fun espeakData(assets: AssetManager): String {
        val target = File(appContext.filesDir, "speech/$MODEL_VERSION/espeak-ng-data")
        val marker = File(target, ".complete")
        if (!marker.exists()) {
            target.parentFile?.parentFile?.listFiles()?.filter { it.name != MODEL_VERSION }?.forEach { it.deleteRecursively() }
            target.deleteRecursively()
            copyAssetDir(assets, "$KOKORO/espeak-ng-data", target)
            marker.createNewFile()
        }
        return target.absolutePath
    }

    private fun copyAssetDir(assets: AssetManager, path: String, target: File) {
        val children = assets.list(path).orEmpty()
        if (children.isEmpty()) {
            target.parentFile?.mkdirs()
            assets.open(path).use { input -> target.outputStream().use { input.copyTo(it) } }
            return
        }
        target.mkdirs()
        children.forEach { copyAssetDir(assets, "$path/$it", File(target, it)) }
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

    private fun ttsConfig(dataDir: String) = OfflineTtsConfig(
        model = OfflineTtsModelConfig(
            kokoro = OfflineTtsKokoroModelConfig(
                model = "$KOKORO/model.int8.onnx",
                voices = "$KOKORO/voices.bin",
                tokens = "$KOKORO/tokens.txt",
                dataDir = dataDir,
            ),
            numThreads = 4,
        ),
        // One sentence per chunk so playback starts after the first sentence, not the whole reply.
        maxNumSentences = 1,
    )

    companion object {
        const val SAMPLE_RATE = 16000
        const val END_OF_SPEECH_SECONDS = 0.6f
        const val MAX_UTTERANCE_SECONDS = 20f
        private const val LOAD_TIMEOUT_SECONDS = 30L
        private const val MOONSHINE = "moonshine"
        private const val KOKORO = "kokoro"
        // Bump with the model pins in app/build.gradle.kts so stale espeak data is replaced.
        private const val MODEL_VERSION = "kokoro-int8-en-v0_19"
        private const val TAG = "JarvisVoice"
    }
}
