package com.example.jarvis.overlay

import android.content.Context
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.BlendMode
import android.graphics.BlurMaskFilter
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.SweepGradient
import android.os.Build
import android.os.SystemClock
import android.view.Gravity
import android.view.RoundedCorner
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import com.example.jarvis.ui.BrandColors
import kotlin.concurrent.thread
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Display-sized, non-interactive window, independent of the panel and keyboard bounds.
 *
 * Gemini-style invocation: a thin line lights every edge at once, then a soft glow blooms in
 * from the bezel, pooling at the bottom over a halftone dot field while light blobs drift along
 * the sides. Dismissal retracts the glow back out through the top and sides, bottom last.
 * The glow and line are rendered once (off the main thread) into alpha masks; each frame only
 * tints them with a rotating warm gradient.
 */
class AuraRing(context: Context, private val level: () -> Float) {
    private val windows = context.getSystemService(WindowManager::class.java)
    private val density = context.resources.displayMetrics.density
    private var attached = false
    private var mode = OverlayMode.IDLE
    private var listening = false
    private val radii = FloatArray(4) { DEFAULT_CORNER_DP * density }
    private var masks: Masks? = null
    private var pendingSpec: MaskSpec? = null
    private var maskGeneration = 0
    private var shader: SweepGradient? = null
    private val rotation = Matrix()
    private val placement = Matrix()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val blobPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val blob = RadialGradient(0f, 0f, 1f,
        intArrayOf(BrandColors.Text.toInt(), (BrandColors.Amber and 0x00FFFFFFL or 0x99000000L).toInt(), 0),
        floatArrayOf(0f, 0.35f, 1f), Shader.TileMode.CLAMP)
    private var angle = 0f
    private var drift = 0f
    private var intensity = 0f
    private var lastFrame = 0L
    /** Linear time through the current entry or exit; [reveal] is that time through its curve. */
    private var progress = 0f
    private var reveal = 0f
    private var showing = false
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
            requestMasks()
            return insets
        }

        override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
            shader = SweepGradient(w / 2f, h / 2f, FLOW, null)
            requestMasks()
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

    /** Size and insets often change together; coalesce them into one build. */
    private val buildMasks = Runnable {
        val spec = MaskSpec(view.width, view.height, radii.toList())
        if (spec.width <= 0 || spec.height <= 0 || spec == masks?.spec || spec == pendingSpec) return@Runnable
        pendingSpec = spec
        val generation = ++maskGeneration
        // Large blurs take a while in software; building here would stall the entry animation.
        thread(name = "JarvisAuraMask") {
            val built = Masks.build(spec, density)
            view.post {
                if (generation != maskGeneration) return@post
                masks = built
                pendingSpec = null
                view.invalidate()
            }
        }
    }

    private fun requestMasks() {
        view.removeCallbacks(buildMasks)
        view.post(buildMasks)
    }

    /** The display reports the panel's true corner radii (API 31+); insets are a fallback, then a default. */
    private fun readCorners(insets: WindowInsets) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        CORNERS.forEachIndexed { index, position ->
            val corner = view.display?.getRoundedCorner(position) ?: insets.getRoundedCorner(position)
            corner?.radius?.takeIf { it > 0 }?.let { radii[index] = it.toFloat() }
        }
    }

    private fun drawFrame(canvas: Canvas, width: Int, height: Int) {
        val current = masks ?: return
        val currentShader = shader ?: return
        val now = SystemClock.uptimeMillis()
        // The reveal clock starts on the first drawn frame, so a slow mask build never skips the entry.
        val dt = if (lastFrame == 0L) 0f else ((now - lastFrame) / 1000f).coerceAtMost(MAX_FRAME_SECONDS)
        lastFrame = now
        progress = if (showing) min(1f, progress + dt / ENTER_SECONDS) else max(0f, progress - dt / EXIT_SECONDS)
        reveal = curve(showing)(progress)

        val voice = level().coerceIn(0f, 1f)
        val style = AuraStyle.of(mode, listening)
        val target = (style.intensity + voice * VOICE_GAIN).coerceAtMost(1f)
        intensity += (target - intensity) * min(1f, dt * EASE_PER_SECOND)
        angle = (angle + dt * (style.degreesPerSecond + voice * VOICE_SPIN)) % 360f
        drift += dt * (style.degreesPerSecond / 40f + voice * 1.5f)
        rotation.setRotate(angle, width / 2f, height / 2f)
        currentShader.setLocalMatrix(rotation)
        paint.shader = currentShader

        // Glow slides in from beyond the bezel; pivoting on the bottom edge makes it pool there first
        // and leave through the top and sides first.
        val travel = (1f - reveal) * GLOW_TRAVEL_DP * density
        placement.setScale((width + 2 * travel) / width, (height + 2 * travel) / height, width / 2f, height.toFloat())
        paint.alpha = (255 * intensity * reveal).toInt()
        canvas.drawBitmap(current.glow, placement, paint)
        drawBlob(canvas, width + travel, height * (0.56f + 0.16f * sin(drift * 0.9f)), BLOB_DP, voice)
        drawBlob(canvas, -travel, height * (0.72f + 0.12f * sin(drift * 0.6f + 2f)), BLOB_DP * 0.7f, voice)

        // The line lights all edges at once, ahead of the glow, and outlasts it on exit.
        paint.alpha = (255 * intensity * min(1f, reveal * 3f)).toInt()
        canvas.drawBitmap(current.line, 0f, 0f, paint)

        if (!showing && progress <= 0f) view.post { if (!showing) close() }
    }

    private fun drawBlob(canvas: Canvas, x: Float, y: Float, sizeDp: Float, voice: Float) {
        val radius = sizeDp * density * (1f + 0.35f * voice)
        placement.setScale(radius, radius)
        placement.postTranslate(x, y)
        blob.setLocalMatrix(placement)
        blobPaint.shader = blob
        blobPaint.alpha = (BLOB_ALPHA * intensity * reveal).toInt()
        canvas.drawCircle(x, y, radius, blobPaint)
    }

    /** Switch direction without a jump: resume the new curve where the old one left the glow. */
    private fun setShowing(value: Boolean) {
        if (showing == value) return
        showing = value
        val next = curve(value)
        var low = 0f
        var high = 1f
        repeat(20) { val mid = (low + high) / 2; if (next(mid) < reveal) low = mid else high = mid }
        progress = (low + high) / 2
    }

    private fun curve(entering: Boolean): (Float) -> Float =
        if (entering) Motion.EnterEase::getInterpolation else { p -> 1f - Motion.ExitEase.getInterpolation(1f - p) }

    private fun syncBounds() {
        val bounds = windows.maximumWindowMetrics.bounds
        params.width = bounds.width()
        params.height = bounds.height()
        if (attached) windows.updateViewLayout(view, params)
    }

    /**
     * Shows the aura while [shown] and there is something to show; otherwise retracts it.
     * [captureHidden] hides it instantly (screenshots) and keeps its place in the animation.
     */
    fun render(next: OverlayMode, shown: Boolean, listening: Boolean, captureHidden: Boolean) {
        mode = next
        this.listening = listening
        view.visibility = if (captureHidden) View.INVISIBLE else View.VISIBLE
        if (!shown || (next == OverlayMode.IDLE && !listening)) { dismiss(); return }
        if (!attached) {
            syncBounds()
            intensity = 0f
            windows.addView(view, params)
            attached = true
            view.requestApplyInsets()
        }
        setShowing(true)
        view.invalidate()
    }

    /** Plays the exit, then removes the window. */
    fun dismiss() {
        if (!attached) return
        setShowing(false)
        view.invalidate()
    }

    /** Removes the window immediately. */
    fun close() {
        lastFrame = 0L
        showing = false
        progress = 0f
        reveal = 0f
        if (attached) { windows.removeView(view); attached = false }
    }

    private data class MaskSpec(val width: Int, val height: Int, val radii: List<Float>)

    private class Masks(val spec: MaskSpec, val glow: Bitmap, val line: Bitmap) {
        companion object {
            fun build(spec: MaskSpec, density: Float): Masks {
                val w = spec.width.toFloat()
                val h = spec.height.toFloat()
                val full = RectF(0f, 0f, w, h)
                fun edge(inset: Float) = Path().apply {
                    addRoundRect(RectF(inset, inset, w - inset, h - inset),
                        FloatArray(8) { (spec.radii[it / 2] - inset).coerceAtLeast(0f) }, Path.Direction.CW)
                }
                fun stroke(widthDp: Float, blurDp: Float, strength: Int) = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    style = Paint.Style.STROKE
                    strokeWidth = widthDp * density
                    maskFilter = BlurMaskFilter(blurDp * density, BlurMaskFilter.Blur.NORMAL)
                    alpha = strength
                }
                fun fade(fromY: Float, fromAlpha: Int) = Paint().apply {
                    shader = LinearGradient(0f, fromY, 0f, h, fromAlpha shl 24, 0xFF000000.toInt(), Shader.TileMode.CLAMP)
                    blendMode = BlendMode.DST_IN
                }

                val glow = Bitmap.createBitmap(spec.width, spec.height, Bitmap.Config.ALPHA_8)
                val canvas = Canvas(glow)
                // Every edge, thinning toward the status bar. Centred on the physical edge, so the
                // outer half is off-glass and the inner half fades inward.
                canvas.saveLayer(full, null)
                canvas.drawPath(edge(0f), stroke(GLOW_DP, GLOW_BLUR_DP, 150))
                canvas.drawRect(full, fade(0f, TOP_STRENGTH))
                canvas.restore()
                // A wider, softer pool and a faint wash in the bottom third.
                canvas.saveLayer(full, null)
                canvas.drawPath(edge(0f), stroke(POOL_DP, POOL_BLUR_DP, 170))
                canvas.drawRect(full, Paint().apply { alpha = WASH_STRENGTH })
                canvas.drawRect(full, fade(h * (1f - POOL_REACH), 0))
                canvas.restore()
                // Halftone dots, as if lit by the glow.
                val spacing = DOT_SPACING_DP * density
                val dot = Paint(Paint.ANTI_ALIAS_FLAG).apply { alpha = DOT_STRENGTH }
                var y = h - spacing / 2
                var row = 0
                while (y > h * (1f - DOT_REACH)) {
                    var x = spacing / 2 + if (row % 2 == 1) spacing / 2 else 0f
                    while (x < w) {
                        val radius = DOT_DP * density * sqrt(halftoneWeight(x, y, w, h, DOT_REACH, DOT_FALLOFF_DP * density))
                        if (radius >= MIN_DOT_DP * density) canvas.drawCircle(x, y, radius, dot)
                        x += spacing
                    }
                    y -= spacing * 0.866f
                    row++
                }

                val line = Bitmap.createBitmap(spec.width, spec.height, Bitmap.Config.ALPHA_8)
                Canvas(line).drawPath(edge(LINE_DP * density / 2f), Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    style = Paint.Style.STROKE
                    strokeWidth = LINE_DP * density
                    maskFilter = BlurMaskFilter(density, BlurMaskFilter.Blur.NORMAL)
                })
                return Masks(spec, glow, line)
            }
        }
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
        const val TOP_STRENGTH = 0x60
        const val POOL_DP = 90f
        const val POOL_BLUR_DP = 36f
        const val POOL_REACH = 0.4f
        const val WASH_STRENGTH = 0x30
        const val DOT_SPACING_DP = 6f
        const val DOT_DP = 1.3f
        const val MIN_DOT_DP = 0.3f
        const val DOT_FALLOFF_DP = 70f
        const val DOT_REACH = 0.4f
        const val DOT_STRENGTH = 150
        const val LINE_DP = 2.5f
        const val GLOW_TRAVEL_DP = 48f
        const val BLOB_DP = 90f
        const val BLOB_ALPHA = 150
        const val ENTER_SECONDS = 0.45f
        const val EXIT_SECONDS = 0.5f
        const val VOICE_GAIN = 0.35f
        const val VOICE_SPIN = 90f
        const val EASE_PER_SECOND = 6f
        const val MAX_FRAME_SECONDS = 0.05f
    }
}

/**
 * Halftone dot strength in 0..1 at ([x], [y]) on a [width]×[height] screen: strongest at the
 * bottom corners, weaker along the bottom edge, and none above the bottom [reach] of the screen.
 */
internal fun halftoneWeight(x: Float, y: Float, width: Float, height: Float, reach: Float, falloff: Float): Float {
    val bottom = ((y / height - (1f - reach)) / reach).coerceIn(0f, 1f)
    val side = min(x, width - x).coerceAtLeast(0f)
    val edge = min(side, (height - y).coerceAtLeast(0f))
    return bottom * bottom * exp(-edge / falloff) * (0.55f + 0.45f * exp(-side / (falloff * 2f)))
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
