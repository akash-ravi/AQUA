package com.example.jarvis.device

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class Gestures(private val service: AccessibilityService) {

    suspend fun tap(x: Int, y: Int) {
        dispatch(Path().apply { moveTo(x.toFloat(), y.toFloat()) }, TAP_DURATION_MS)
    }

    suspend fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Long) {
        val path = Path().apply {
            moveTo(x1.toFloat(), y1.toFloat())
            lineTo(x2.toFloat(), y2.toFloat())
        }
        dispatch(path, durationMs)
    }

    private suspend fun dispatch(path: Path, durationMs: Long) = suspendCancellableCoroutine { continuation ->
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs))
            .build()
        val callback = object : AccessibilityService.GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) = continuation.resume(Unit)

            override fun onCancelled(gestureDescription: GestureDescription?) =
                continuation.resumeWithException(CommandException("actionFailed", "gesture was cancelled"))
        }
        if (!service.dispatchGesture(gesture, callback, null)) {
            continuation.resumeWithException(CommandException("actionFailed", "gesture was rejected"))
        }
    }

    companion object {
        private const val TAP_DURATION_MS = 50L
    }
}
