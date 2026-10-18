package com.example.jarvis.overlay

import android.accessibilityservice.AccessibilityService
import android.animation.ValueAnimator
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.SweepGradient
import android.view.View
import android.view.WindowManager
import android.view.animation.LinearInterpolator

/** A separate, non-interactive window; it never takes focus or intercepts touches. */
class AuraRing(service: AccessibilityService) {
    private val windows = service.getSystemService(WindowManager::class.java)
    private var attached = false
    private val density = service.resources.displayMetrics.density
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4 * density
    }
    private val view = object : View(service) {
        override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
            paint.shader = SweepGradient(w / 2f, h / 2f,
                intArrayOf(Color.rgb(115, 145, 255), Color.rgb(195, 115, 255), Color.CYAN, Color.rgb(115, 145, 255)), null)
        }

        override fun onDraw(canvas: Canvas) {
            val inset = paint.strokeWidth
            canvas.drawRoundRect(inset, inset, width - inset, height - inset, 28 * density, 28 * density, paint)
        }
    }
    private val animation = ValueAnimator.ofInt(100, 245).apply {
        duration = 1800
        repeatCount = ValueAnimator.INFINITE
        repeatMode = ValueAnimator.REVERSE
        interpolator = LinearInterpolator()
        addUpdateListener { paint.alpha = it.animatedValue as Int; view.invalidate() }
    }
    private val params = WindowManager.LayoutParams(
        -1, -1, WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
        PixelFormat.TRANSLUCENT,
    ).apply { setFitInsetsTypes(0); setTitle("Jarvis aura") }

    fun render(next: OverlayMode, visible: Boolean) {
        if (!visible || next == OverlayMode.IDLE) { close(); return }
        if (!attached) { windows.addView(view, params); attached = true }
        val animate = next == OverlayMode.PROCESSING || next == OverlayMode.PAUSING
        if (animate && !animation.isStarted) animation.start()
        if (!animate) {
            animation.cancel()
            paint.alpha = if (next == OverlayMode.WAITING_FOR_USER) 230 else 110
            view.invalidate()
        }
    }

    fun close() {
        animation.cancel()
        if (attached) { windows.removeView(view); attached = false }
    }
}
