package com.example.jarvis.overlay

import android.content.Context
import android.animation.ValueAnimator
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.SweepGradient
import android.os.Build
import android.view.Gravity
import android.view.RoundedCorner
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.view.animation.LinearInterpolator
import com.example.jarvis.ui.BrandColors

/** Display-sized, non-interactive window, independent of the panel and keyboard bounds. */
class AuraRing(context: Context) {
    private val windows = context.getSystemService(WindowManager::class.java)
    private val density = context.resources.displayMetrics.density
    private var attached = false
    private val edge = Path()
    private val radii = FloatArray(8) { 28 * density }
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3 * density
    }
    private val glow = Paint(paint).apply { strokeWidth = 10 * density; alpha = 40 }
    private val params = WindowManager.LayoutParams(
        -1, -1, WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.LEFT
        layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
        softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING
        setFitInsetsTypes(0)
        setTitle("Jarvis edge aura")
    }

    private val view = object : View(context) {
        override fun onApplyWindowInsets(insets: WindowInsets): WindowInsets {
            if (Build.VERSION.SDK_INT >= 31) {
                val positions = intArrayOf(RoundedCorner.POSITION_TOP_LEFT, RoundedCorner.POSITION_TOP_RIGHT,
                    RoundedCorner.POSITION_BOTTOM_RIGHT, RoundedCorner.POSITION_BOTTOM_LEFT)
                positions.forEachIndexed { index, position ->
                    val radius = insets.getRoundedCorner(position)?.radius?.toFloat() ?: 0f
                    radii[index * 2] = radius
                    radii[index * 2 + 1] = radius
                }
            }
            updateEdge(width, height)
            return insets
        }

        override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
            val shader = SweepGradient(w / 2f, h / 2f,
                intArrayOf(BrandColors.Orange.toInt(), BrandColors.Red.toInt(), BrandColors.Amber.toInt(),
                    BrandColors.Orange.toInt()), null)
            paint.shader = shader
            glow.shader = shader
            updateEdge(w, h)
        }

        override fun onConfigurationChanged(newConfig: Configuration) {
            super.onConfigurationChanged(newConfig)
            post {
                if (isAttachedToWindow) { syncBounds(); requestApplyInsets() }
            }
        }

        override fun onDraw(canvas: Canvas) {
            canvas.drawPath(edge, glow)
            canvas.drawPath(edge, paint)
        }
    }
    private val animation = ValueAnimator.ofInt(120, 245).apply {
        duration = 1800
        repeatCount = ValueAnimator.INFINITE
        repeatMode = ValueAnimator.REVERSE
        interpolator = LinearInterpolator()
        addUpdateListener { paint.alpha = it.animatedValue as Int; view.invalidate() }
    }

    private fun updateEdge(width: Int, height: Int) {
        val inset = paint.strokeWidth / 2f
        if (width <= paint.strokeWidth || height <= paint.strokeWidth) return
        edge.reset()
        edge.addRoundRect(RectF(inset, inset, width - inset, height - inset),
            FloatArray(8) { (radii[it] - inset).coerceAtLeast(0f) }, Path.Direction.CW)
        view.invalidate()
    }

    private fun syncBounds() {
        val bounds = windows.maximumWindowMetrics.bounds
        params.width = bounds.width()
        params.height = bounds.height()
        if (attached) windows.updateViewLayout(view, params)
    }

    fun render(next: OverlayMode, visible: Boolean) {
        if (!visible || next == OverlayMode.IDLE) { close(); return }
        if (!attached) {
            syncBounds()
            windows.addView(view, params)
            attached = true
            view.requestApplyInsets()
        }
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
