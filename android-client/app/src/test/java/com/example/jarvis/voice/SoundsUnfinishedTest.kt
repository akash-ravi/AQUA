package com.example.jarvis.voice

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SoundsUnfinishedTest {
    @Test
    fun trailingOffIsUnfinished() {
        assertTrue(soundsUnfinished("Okay. Now I want to go to..."))
        assertTrue(soundsUnfinished("I just want-"))
        assertTrue(soundsUnfinished("Open Lyft and"))
        assertTrue(soundsUnfinished("Check Uber,"))
    }

    @Test
    fun completeSentencesAreFinished() {
        assertFalse(soundsUnfinished("Old."))
        assertFalse(soundsUnfinished("Keep trying."))
        assertFalse(soundsUnfinished("Can you open Splitwise and tell me how much money do I owe to everyone?"))
        assertFalse(soundsUnfinished("Take me to USC"))
    }
}
