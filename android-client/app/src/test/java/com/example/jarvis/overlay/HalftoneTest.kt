package com.example.jarvis.overlay

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HalftoneTest {
    private fun weight(x: Float, y: Float) = halftoneWeight(x, y, 1000f, 2000f, 0.4f, 100f)

    @Test fun `dots are densest at the bottom corners and fade toward the centre`() {
        val corner = weight(10f, 1990f)
        val bottomCentre = weight(500f, 1990f)
        val middle = weight(500f, 1500f)
        assertTrue(corner > bottomCentre)
        assertTrue(bottomCentre > middle)
        assertTrue(corner <= 1f)
    }

    @Test fun `no dots above the reach`() {
        assertEquals(0f, weight(0f, 1100f), 0f)
        assertEquals(0f, weight(500f, 0f), 0f)
    }
}
