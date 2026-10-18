package com.example.jarvis.voice

import org.junit.Assert.assertEquals
import org.junit.Test

class LoudnessTest {
    @Test
    fun silenceIsZero() {
        assertEquals(0f, loudness(FloatArray(512)), 0f)
        assertEquals(0f, loudness(FloatArray(0)), 0f)
    }

    @Test
    fun fullScaleIsClampedToOne() {
        assertEquals(1f, loudness(FloatArray(512) { 1f }), 0f)
    }

    @Test
    fun midLevelMapsLinearlyInDecibels() {
        // RMS 10^-1.5 is -30 dBFS, halfway between the -50 and -10 dBFS limits.
        val amplitude = Math.pow(10.0, -1.5).toFloat()
        assertEquals(0.5f, loudness(FloatArray(512) { amplitude }), 0.001f)
    }
}
