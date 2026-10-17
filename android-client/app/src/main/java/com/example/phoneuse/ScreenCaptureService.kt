package com.example.phoneuse

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import java.io.File

class ScreenCaptureService : AccessibilityService() {

    private val handler = Handler(Looper.getMainLooper())
    private var capturesDone = 0

    override fun onServiceConnected() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            Log.w(TAG, "takeScreenshot needs API 30+")
            return
        }
        capturesDone = 0
        handler.post(captureStep)
    }

    private val captureStep = object : Runnable {
        override fun run() {
            captureScreen(capturesDone + 1)
            capturesDone++
            if (capturesDone < CAPTURE_COUNT) handler.postDelayed(this, CAPTURE_INTERVAL_MS)
        }
    }

    private fun captureScreen(index: Int) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val bitmap = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                val file = File(getExternalFilesDir(null), "capture_$index.png")
                bitmap?.compress(Bitmap.CompressFormat.PNG, 100, file.outputStream())
                result.hardwareBuffer.close()
                Log.i(TAG, "capture $index/$CAPTURE_COUNT ${bitmap?.width}x${bitmap?.height} saved to ${file.absolutePath}")
            }

            override fun onFailure(errorCode: Int) {
                Log.e(TAG, "capture $index failed, errorCode=$errorCode")
            }
        })
    }

    override fun onDestroy() {
        handler.removeCallbacks(captureStep)
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    companion object {
        private const val TAG = "ScreenCapture"
        private const val CAPTURE_COUNT = 10
        private const val CAPTURE_INTERVAL_MS = 2000L
    }
}
