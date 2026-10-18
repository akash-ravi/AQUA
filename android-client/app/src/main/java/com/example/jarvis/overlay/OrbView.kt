package com.example.jarvis.overlay

import android.content.Context
import android.graphics.BlendMode
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.Shader
import android.os.SystemClock
import android.view.View
import com.example.jarvis.ui.BrandColors
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

/**
 * Siri / ChatGPT-voice style orb: a glassy sphere filled with drifting warm light, inside a soft halo.
 * Its outline ripples and swells with the voice level. Animates only while attached and visible.
 */
class OrbView(context: Context, private val level: () -> Float) : View(context) {
    private class Blob(color: Long, val phase: Float, val speed: Float, val wobble: Float) {
        val shader = RadialGradient(0f, 0f, 1f,
            intArrayOf(color.toInt(), (color and 0x00FFFFFFL or 0x88000000L).toInt(), 0),
            floatArrayOf(0f, 0.5f, 1f), Shader.TileMode.CLAMP)
        var angle = phase
    }

    private val blobs = listOf(
        Blob(BrandColors.Orange, 0f, 0.9f, 1.3f),
        Blob(BrandColors.Red, 2.1f, 1.2f, 0.8f),
        Blob(BrandColors.Amber, 4.2f, 0.7f, 1.6f),
    )
    private val halo = radial(0f to tint(BrandColors.Orange, 0x99), 0.55f to tint(BrandColors.Red, 0x40), 1f to 0)
    private val depth = radial(0f to 0xFF3A1410.toInt(), 1f to 0xFF120806.toInt())
    private val core = radial(0f to BrandColors.Text.toInt(), 0.4f to tint(BrandColors.Amber, 0x66), 1f to 0)
    private val shine = radial(0f to 0x88FFFFFF.toInt(), 1f to 0)
    private val placement = Matrix()
    private val outline = Path()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val mask = Paint(Paint.ANTI_ALIAS_FLAG).apply { blendMode = BlendMode.DST_IN }
    private val rim = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = tint(BrandColors.Amber, 0x99)
    }
    private var animating = false
    private var smoothed = 0f
    private var time = 0f
    private var lastFrame = 0L

    var mode = OverlayMode.IDLE
        set(value) { field = value; invalidate() }
    var listening = false
        set(value) { field = value; invalidate() }

    override fun onVisibilityAggregated(isVisible: Boolean) {
        super.onVisibilityAggregated(isVisible)
        animating = isVisible
        lastFrame = 0L
        if (isVisible) invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        val now = SystemClock.uptimeMillis()
        val dt = if (lastFrame == 0L) 0f else ((now - lastFrame) / 1000f).coerceAtMost(0.05f)
        lastFrame = now
        smoothed += (level().coerceIn(0f, 1f) - smoothed) * min(1f, dt * 12f)
        val calm = mode == OverlayMode.PAUSED || mode == OverlayMode.PAUSING
        val energy = when {
            listening -> 0.5f
            mode == OverlayMode.PROCESSING -> 0.55f
            mode == OverlayMode.WAITING_FOR_USER -> 0.4f
            calm -> 0.1f
            else -> 0.3f
        } + smoothed * 0.6f
        val speed = (if (calm) 0.3f else if (mode == OverlayMode.PROCESSING) 1.6f else 1f) + smoothed * 2f
        time += dt * speed
        val cx = width / 2f
        val cy = height / 2f
        val half = min(cx, cy)
        // Worst case outline reaches ~0.85 of half, leaving the rest for the halo.
        val r = half * 0.7f * (1f + 0.07f * smoothed)

        paint.blendMode = null
        paint.alpha = ((if (calm) 0.35f else 0.55f + 0.45f * smoothed) * 255).toInt()
        fill(canvas, halo, cx, cy, half)

        traceOutline(cx, cy, r, 0.012f + 0.03f * energy + 0.06f * smoothed)
        val layer = canvas.saveLayerAlpha(0f, 0f, width.toFloat(), height.toFloat(), if (calm) 170 else 255)
        paint.alpha = 255
        fill(canvas, depth, cx, cy, r)
        paint.blendMode = BlendMode.PLUS
        for (blob in blobs) {
            blob.angle += dt * blob.speed * speed
            val offset = r * (0.25f + 0.3f * energy)
            val x = cx + cos(blob.angle) * offset
            val y = cy + sin(blob.angle * blob.wobble) * offset
            fill(canvas, blob.shader, x, y, r * (0.85f + 0.1f * sin(blob.angle * 1.7f)))
        }
        fill(canvas, core, cx, cy, r * (0.4f + 0.3f * smoothed))
        paint.blendMode = null
        fill(canvas, shine, cx - r * 0.35f, cy - r * 0.4f, r * 0.45f)
        canvas.drawPath(outline, mask)
        canvas.restoreToCount(layer)

        rim.strokeWidth = r * 0.04f
        canvas.drawPath(outline, rim)
        if (animating) postInvalidateOnAnimation()
    }

    /** Two travelling ripples around the edge, so the sphere looks liquid rather than bouncy. */
    private fun traceOutline(cx: Float, cy: Float, r: Float, ripple: Float) {
        outline.rewind()
        for (i in 0..POINTS) {
            val theta = i * 2f * PI.toFloat() / POINTS
            val radius = r * (1f + ripple * (0.6f * sin(3 * theta + time * 1.3f) + 0.4f * sin(5 * theta - time * 1.9f)))
            val x = cx + cos(theta) * radius
            val y = cy + sin(theta) * radius
            if (i == 0) outline.moveTo(x, y) else outline.lineTo(x, y)
        }
        outline.close()
    }

    private fun fill(canvas: Canvas, shader: Shader, x: Float, y: Float, radius: Float) {
        placement.setScale(radius, radius)
        placement.postTranslate(x, y)
        shader.setLocalMatrix(placement)
        paint.shader = shader
        canvas.drawCircle(x, y, radius, paint)
    }

    private companion object {
        const val POINTS = 96

        fun tint(color: Long, alpha: Int) = (color and 0x00FFFFFFL).toInt() or (alpha shl 24)

        fun radial(vararg stops: Pair<Float, Int>) = RadialGradient(0f, 0f, 1f,
            stops.map { it.second }.toIntArray(), stops.map { it.first }.toFloatArray(), Shader.TileMode.CLAMP)
    }
}
