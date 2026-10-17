package com.example.jarvis

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.io.File

class ScreenCaptureService : AccessibilityService() {

    private val handler = Handler(Looper.getMainLooper())

    override fun onServiceConnected() {
        instance = this
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    fun startCaptureBurst() {
        handler.removeCallbacksAndMessages(null)
        screenshotDirectory().listFiles()?.forEach { it.delete() }
        repeat(CAPTURE_COUNT) { index ->
            handler.postDelayed({ captureOnce(index + 1) }, index * CAPTURE_INTERVAL_MS)
        }
    }

    fun openDoorDash() {
        performGlobalAction(GLOBAL_ACTION_HOME)
        handler.postDelayed({ swipeUpToOpenDrawer() }, STEP_DELAY_MS)
        handler.postDelayed({ searchDrawerFor("DoorDash") }, STEP_DELAY_MS * 2)
        handler.postDelayed({ tapAppIcon("DoorDash") }, STEP_DELAY_MS * 3)
    }

    private fun swipeUpToOpenDrawer() {
        val metrics = resources.displayMetrics
        val path = Path().apply {
            moveTo(metrics.widthPixels / 2f, metrics.heightPixels * 0.85f)
            lineTo(metrics.widthPixels / 2f, metrics.heightPixels * 0.3f)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, SWIPE_DURATION_MS)
        dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
    }

    private fun searchDrawerFor(appName: String) {
        val searchBox = findNode { it.isEditable }
        if (searchBox == null) {
            Log.e(TAG, "drawer search box not found")
            return
        }
        val text = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, appName) }
        searchBox.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, text)
    }

    private fun tapAppIcon(appName: String) {
        Log.i(TAG, "tree before tap:\n${describeNode(rootInActiveWindow, 0)}")
        val icon = findNode { !it.isEditable && (it.text?.toString() == appName || it.contentDescription?.toString() == appName) }
        if (icon == null) {
            Log.e(TAG, "$appName icon not found")
            return
        }
        val bounds = Rect().also { icon.getBoundsInScreen(it) }
        Log.i(TAG, "tapping $appName at ${bounds.centerX()},${bounds.centerY()}")
        val path = Path().apply { moveTo(bounds.centerX().toFloat(), bounds.centerY().toFloat()) }
        val stroke = GestureDescription.StrokeDescription(path, 0, TAP_DURATION_MS)
        dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
    }

    private fun findNode(matches: (AccessibilityNodeInfo) -> Boolean): AccessibilityNodeInfo? {
        val queue = ArrayDeque<AccessibilityNodeInfo>().apply { rootInActiveWindow?.let { add(it) } }
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            if (matches(node)) return node
            repeat(node.childCount) { index -> node.getChild(index)?.let { queue.add(it) } }
        }
        return null
    }

    private fun captureOnce(captureNumber: Int) {
        Log.i(TAG, "capture $captureNumber/$CAPTURE_COUNT tree:\n${describeNode(rootInActiveWindow, 0)}")
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(screenshot: ScreenshotResult) {
                val bitmap = Bitmap.wrapHardwareBuffer(screenshot.hardwareBuffer, screenshot.colorSpace)
                val file = File(screenshotDirectory(), "capture$captureNumber.png")
                Thread {
                    file.outputStream().use { bitmap?.compress(Bitmap.CompressFormat.PNG, 100, it) }
                    screenshot.hardwareBuffer.close()
                    Log.i(TAG, "capture $captureNumber saved ${bitmap?.width}x${bitmap?.height} to ${file.absolutePath}")
                }.start()
            }

            override fun onFailure(errorCode: Int) {
                Log.e(TAG, "capture $captureNumber screenshot failed, code $errorCode")
            }
        })
    }

    private fun screenshotDirectory() = File(getExternalFilesDir(null), "screenshots").apply { mkdirs() }

    private fun describeNode(node: AccessibilityNodeInfo?, depth: Int): String {
        if (node == null) return ""
        val line = "${"  ".repeat(depth)}${node.className} text=${node.text} desc=${node.contentDescription} " +
            "id=${node.viewIdResourceName} clickable=${node.isClickable}\n"
        val children = (0 until node.childCount).joinToString("") { describeNode(node.getChild(it), depth + 1) }
        return line + children
    }

    companion object {
        const val TAG = "JarvisCapture"
        const val STEP_DELAY_MS = 1000L
        const val SWIPE_DURATION_MS = 300L
        const val TAP_DURATION_MS = 50L
        const val CAPTURE_COUNT = 10
        const val CAPTURE_INTERVAL_MS = 2000L
        var instance: ScreenCaptureService? = null
    }
}
