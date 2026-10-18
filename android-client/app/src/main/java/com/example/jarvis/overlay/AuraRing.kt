package com.example.jarvis.overlay

import android.content.Context
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.BlurMaskFilter
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.SweepGradient
import android.os.Build
import android.os.SystemClock
import android.view.Gravity
import android.view.RoundedCorner
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import com.example.jarvis.ui.BrandColors
import kotlin.math.min

/**
 * Display-sized, non-interactive window, independent of the panel and keyboard bounds.
 * The soft edge is rendered once into an alpha mask; each frame only tints it with a rotating
 * warm gradient, so the flow and voice reaction cost one bitmap draw.
 */
class AuraRing(context: Context, private val level: () -> Float) {
    private val windows = context.getSystemService(WindowManager::class.java)
    private val density = context.resources.displayMetrics.density
    private var attached = false
    private var mode = OverlayMode.IDLE
    private var listening = false
    private val radii = FloatArray(4) { DEFAULT_CORNER_DP * density }
    private var mask: Bitmap? = null
    private var shader: SweepGradient? = null
    private val rotation = Matrix()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private var angle = 0f
    private var intensity = 0f
    private var lastFrame = 0L
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
            readCorners(insets)
            rebuildMask(width, height)
            return insets
        }

        override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
            shader = SweepGradient(w / 2f, h / 2f, FLOW, null)
            rebuildMask(w, h)
        }

        override fun onConfigurationChanged(newConfig: Configuration) {
            super.onConfigurationChanged(newConfig)
            post {
                if (isAttachedToWindow) { syncBounds(); requestApplyInsets() }
            }
        }

        override fun onDraw(canvas: Canvas) {
            drawFrame(canvas, width, height)
            if (attached) postInvalidateOnAnimation()
        }
    }

    /** The display reports the panel's true corner radii (API 31+); insets are a fallback, then a default. */
    private fun readCorners(insets: WindowInsets) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        CORNERS.forEachIndexed { index, position ->
            val corner = view.display?.getRoundedCorner(position) ?: insets.getRoundedCorner(position)
            corner?.radius?.takeIf { it > 0 }?.let { radii[index] = it.toFloat() }
        }
    }

    private fun edge(width: Int, height: Int, inset: Float) = Path().apply {
        addRoundRect(RectF(inset, inset, width - inset, height - inset),
            FloatArray(8) { (radii[it / 2] - inset).coerceAtLeast(0f) }, Path.Direction.CW)
    }

    private fun rebuildMask(width: Int, height: Int) {
        if (width <= 0 || height <= 0) return
        val next = Bitmap.createBitmap(width, height, Bitmap.Config.ALPHA_8)
        val canvas = Canvas(next)
        // Centred on the physical edge: the outer half is off-glass, the inner half fades inward.
        canvas.drawPath(edge(width, height, 0f), Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = GLOW_DP * density
            maskFilter = BlurMaskFilter(GLOW_BLUR_DP * density, BlurMaskFilter.Blur.NORMAL)
            alpha = 150
        })
        val line = LINE_DP * density
        canvas.drawPath(edge(width, height, line / 2f), Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = line
            maskFilter = BlurMaskFilter(density, BlurMaskFilter.Blur.NORMAL)
        })
        mask = next
        view.invalidate()
    }

    private fun drawFrame(canvas: Canvas, width: Int, height: Int) {
        val currentMask = mask ?: return
        val currentShader = shader ?: return
        val now = SystemClock.uptimeMillis()
        val dt = if (lastFrame == 0L) 0f else ((now - lastFrame) / 1000f).coerceAtMost(MAX_FRAME_SECONDS)
        lastFrame = now
        val voice = level().coerceIn(0f, 1f)
        val style = AuraStyle.of(mode, listening)
        val target = (style.intensity + voice * VOICE_GAIN).coerceAtMost(1f)
        intensity += (target - intensity) * min(1f, dt * EASE_PER_SECOND)
        angle = (angle + dt * (style.degreesPerSecond + voice * VOICE_SPIN)) % 360f
        rotation.setRotate(angle, width / 2f, height / 2f)
        currentShader.setLocalMatrix(rotation)
        paint.shader = currentShader
        paint.alpha = (255 * intensity).toInt()
        canvas.drawBitmap(currentMask, 0f, 0f, paint)
    }

    private fun syncBounds() {
        val bounds = windows.maximumWindowMetrics.bounds
        params.width = bounds.width()
        params.height = bounds.height()
        if (attached) windows.updateViewLayout(view, params)
    }

    fun render(next: OverlayMode, visible: Boolean, listening: Boolean = false) {
        if (!visible || (next == OverlayMode.IDLE && !listening)) { close(); return }
        mode = next
        this.listening = listening
        if (!attached) {
            syncBounds()
            intensity = 0f
            windows.addView(view, params)
            attached = true
            view.requestApplyInsets()
        }
        view.invalidate()
    }

    fun close() {
        lastFrame = 0L
        if (attached) { windows.removeView(view); attached = false }
    }

    private companion object {
        val CORNERS = intArrayOf(RoundedCorner.POSITION_TOP_LEFT, RoundedCorner.POSITION_TOP_RIGHT,
            RoundedCorner.POSITION_BOTTOM_RIGHT, RoundedCorner.POSITION_BOTTOM_LEFT)
        // Two warm cycles around the screen so the flow is visible on every edge; first == last.
        val FLOW = intArrayOf(BrandColors.Orange.toInt(), BrandColors.Red.toInt(), BrandColors.Amber.toInt(),
            BrandColors.Orange.toInt(), BrandColors.Red.toInt(), BrandColors.Amber.toInt(), BrandColors.Orange.toInt())
        const val DEFAULT_CORNER_DP = 32f
        const val GLOW_DP = 34f
        const val GLOW_BLUR_DP = 14f
        const val LINE_DP = 2.5f
        const val VOICE_GAIN = 0.35f
        const val VOICE_SPIN = 90f
        const val EASE_PER_SECOND = 6f
        const val MAX_FRAME_SECONDS = 0.05f
    }
}

/** How bright and how fast the edge flows for each assistant state. */
internal data class AuraStyle(val intensity: Float, val degreesPerSecond: Float) {
    companion object {
        fun of(mode: OverlayMode, listening: Boolean) = when {
            listening -> AuraStyle(0.85f, 40f)
            mode == OverlayMode.WAITING_FOR_USER -> AuraStyle(0.9f, 25f)
            mode == OverlayMode.PROCESSING -> AuraStyle(0.75f, 55f)
            mode == OverlayMode.PAUSING -> AuraStyle(0.55f, 15f)
            mode == OverlayMode.PAUSED -> AuraStyle(0.35f, 6f)
            else -> AuraStyle(0.6f, 20f)
        }
    }
}
