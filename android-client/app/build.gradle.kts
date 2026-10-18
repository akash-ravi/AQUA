import java.net.URI
import java.security.MessageDigest
import java.util.Properties
import javax.inject.Inject

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

fun localProperty(name: String, default: String): String {
    val file = rootProject.file("local.properties")
    val properties = Properties().apply { if (file.exists()) file.inputStream().use(::load) }
    return properties.getProperty(name, default)
}

android {
    namespace = "com.example.jarvis"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.example.jarvis"
        minSdk = 30
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        buildConfigField("String", "AGENT_URL", "\"${localProperty("AGENT_URL", "ws://10.0.2.2:8000/agent")}\"")

        // sherpa-onnx ships native code per ABI; every supported phone and Apple-silicon emulator is arm64.
        ndk { abiFilters += "arm64-v8a" }
    }

    androidResources {
        // sherpa-onnx reads speech models straight from the APK; compressing them only slows loading.
        noCompress += listOf("onnx", "ort", "bin")
    }

    buildTypes {
        release {
            optimization {
                enable = true
                packageScope = setOf("androidx.**", "kotlin.**", "kotlinx.**")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        buildConfig = true
        compose = true
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.okhttp)
    implementation(libs.sherpa.onnx)
    testImplementation(libs.junit)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
/**
 * On-device speech models (see voice/SpeechModels.kt). They exceed GitHub's file limit, so they are
 * fetched from pinned sherpa-onnx releases into a git-ignored cache and packaged as generated assets.
 */
abstract class SpeechModelsTask @Inject constructor(
    private val files: FileSystemOperations,
    private val archives: ArchiveOperations,
) : DefaultTask() {
    @get:Internal abstract val cacheDir: DirectoryProperty
    @get:OutputDirectory abstract val outputDir: DirectoryProperty

    private class Download(val url: String, val sha256: String) {
        val name get() = url.substringAfterLast('/')
    }

    private val release = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
    private val moonshine = Download("$release/asr-models/sherpa-onnx-moonshine-base-en-quantized-2026-02-27.tar.bz2",
        "43232c1d13013d37317163baec3135bd771a186a4356f28c889bab453bb0e891")
    private val kokoro = Download("$release/tts-models/kokoro-int8-en-v0_19.tar.bz2",
        "c9f0dd393615805b0bab050c340834d5e684e732aec91c0e860cd30e982c08bd")
    private val vad = Download("$release/asr-models/silero_vad.onnx",
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6")

    @get:Input val pins: List<String> get() = listOf(moonshine, kokoro, vad).map { "${it.url}@${it.sha256}" }

    @TaskAction
    fun unpack() {
        val out = outputDir.get().asFile
        files.delete { delete(out) }
        unpackArchive(fetch(moonshine), "moonshine") {
            include("*/encoder_model.ort", "*/decoder_model_merged.ort", "*/tokens.txt")
        }
        // English-only voice: keep espeak-ng's English dictionary and drop the other ~390 (≈17 MB).
        unpackArchive(fetch(kokoro), "kokoro") {
            include("*/model.int8.onnx", "*/voices.bin", "*/tokens.txt", "*/espeak-ng-data/**")
            exclude { it.name.endsWith("_dict") && it.name != "en_dict" }
        }
        files.copy { from(fetch(vad)); into(out) }
    }

    private fun unpackArchive(archive: File, into: String, select: CopySpec.() -> Unit) = files.copy {
        from(archives.tarTree(archives.bzip2(archive)))
        select()
        // Drop the archive's top-level folder so assets keep stable paths across model versions.
        eachFile { relativePath = RelativePath(true, *relativePath.segments.drop(1).toTypedArray()) }
        includeEmptyDirs = false
        into(outputDir.get().dir(into))
    }

    private fun fetch(download: Download): File {
        val file = cacheDir.get().file(download.name).asFile
        if (file.exists() && sha256(file) == download.sha256) return file
        file.parentFile.mkdirs()
        logger.lifecycle("Downloading ${download.name}")
        val partial = File(file.path + ".part")
        URI(download.url).toURL().openStream().use { input -> partial.outputStream().use { input.copyTo(it) } }
        val actual = sha256(partial)
        if (actual != download.sha256) {
            partial.delete()
            throw GradleException("${download.name}: expected SHA-256 ${download.sha256}, got $actual")
        }
        partial.renameTo(file)
        return file
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1 shl 16)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}

val speechModels = tasks.register<SpeechModelsTask>("speechModels") {
    cacheDir = rootProject.layout.projectDirectory.dir(".speech-models")
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(speechModels, SpeechModelsTask::outputDir)
    }
}
