package com.example.jarvis.voice

import kotlin.math.log10
import kotlin.math.sqrt

/** Maps a frame's RMS to 0..1 for visuals: -50 dBFS is silent, -10 dBFS is full. */
internal fun loudness(samples: FloatArray): Float {
    if (samples.isEmpty()) return 0f
    var sum = 0.0
    for (sample in samples) sum += sample * sample
    val rms = sqrt(sum / samples.size)
    if (rms <= 0.0) return 0f
    return ((20 * log10(rms) + 50) / 40).toFloat().coerceIn(0f, 1f)
}
