package com.example.jarvis.overlay

import android.content.Context
import android.graphics.BlendMode
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.os.SystemClock
import android.view.View
import com.example.jarvis.ui.BrandColors
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

/**
 * Siri-like orb: three warm, additive blobs orbit each other and swell with the voice level.
 * Animates only while attached and visible, so a hidden panel costs no frames.
 */
class OrbView(context: Context, private val level: () -> Float) : View(context) {
    private class Blob(color: Long, val phase: Float, val speed: Float, val wobble: Float) {
        val shader = RadialGradient(0f, 0f, 1f,
            intArrayOf(color.toInt(), (color and 0x00FFFFFFL or 0x66000000L).toInt(), 0),
            floatArrayOf(0f, 0.55f, 1f), Shader.TileMode.CLAMP)
        var angle = phase
    }

    private val blobs = listOf(
        Blob(BrandColors.Orange, 0f, 1.0f, 1.3f),
        Blob(BrandColors.Red, 2.1f, 1.35f, 0.9f),
        Blob(BrandColors.Amber, 4.2f, 0.8f, 1.7f),
    )
    private val core = RadialGradient(0f, 0f, 1f,
        intArrayOf(BrandColors.Text.toInt(), (BrandColors.Amber and 0x00FFFFFFL or 0x55000000L).toInt(), 0),
        floatArrayOf(0f, 0.4f, 1f), Shader.TileMode.CLAMP)
    private val placement = Matrix()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { blendMode = BlendMode.PLUS }
    private var animating = false
    private var smoothed = 0f
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
        val cx = width / 2f
        val cy = height / 2f
        // Largest blob extent is ~1.47r, so this keeps the orb inside its bounds.
        val r = min(cx, cy) * 0.66f
        paint.alpha = if (calm) 150 else 255
        for (blob in blobs) {
            blob.angle += dt * blob.speed * speed
            val offset = r * 0.28f * energy
            val x = cx + cos(blob.angle) * offset
            val y = cy + sin(blob.angle * blob.wobble) * offset
            val radius = r * (0.78f + 0.12f * sin(blob.angle * 1.7f) + 0.25f * smoothed)
            drawGlow(canvas, blob.shader, x, y, radius)
        }
        drawGlow(canvas, core, cx, cy, r * (0.45f + 0.2f * smoothed))
        if (animating) postInvalidateOnAnimation()
    }

    private fun drawGlow(canvas: Canvas, shader: Shader, x: Float, y: Float, radius: Float) {
        placement.setScale(radius, radius)
        placement.postTranslate(x, y)
        shader.setLocalMatrix(placement)
        paint.shader = shader
        canvas.drawCircle(x, y, radius, paint)
    }
}
