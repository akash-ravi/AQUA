package com.example.jarvis.overlay

import android.accessibilityservice.AccessibilityService
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.content.res.ColorStateList
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.example.jarvis.ui.BrandColors

/** Service-owned window. All calls, including state rendering and removal, run on the main thread. */
class AgentOverlay(
    private val service: AccessibilityService,
    private val onInput: (AssistantInput) -> Boolean,
    private val onStop: () -> Unit,
    private val onPause: () -> Unit,
    private val onContinue: () -> Unit,
    private val onHide: () -> Unit,
) {
    private val windows = service.getSystemService(WindowManager::class.java)
    private val keyboard = service.getSystemService(InputMethodManager::class.java)
    private val accent = BrandColors.Orange.toInt()
    private var state = OverlayState()
    private var expanded = false
    private var attached = false

    private val title = TextView(service).apply {
        setTextColor(BrandColors.Text.toInt())
        textSize = 16f
        letterSpacing = 0.04f
        setTypeface(typeface, Typeface.BOLD)
        setPadding(dp(8), dp(12), dp(8), dp(12))
        setOnClickListener { setExpanded(!expanded) }
    }
    private val stop = button("Stop") { onStop() }.apply { setTextColor(BrandColors.Red.toInt()) }
    private val pause = button("Pause") { if (state.mode == OverlayMode.PAUSED) onContinue() else onPause() }
    private val controls = LinearLayout(service).apply {
        gravity = Gravity.END
        addView(pause)
        addView(stop)
    }
    private val prompt = TextView(service).apply {
        setTextColor(BrandColors.Muted.toInt())
        textSize = 14f
        setPadding(dp(8), dp(8), dp(8), dp(8))
    }
    private val promptScroll = ScrollView(service).apply { addView(prompt) }
    private val input = EditText(service).apply {
        setTextColor(BrandColors.Text.toInt())
        setHintTextColor(BrandColors.Muted.toInt())
        background = GradientDrawable().apply {
            setColor(BrandColors.Background.toInt())
            cornerRadius = dp(18).toFloat()
            setStroke(dp(1), BrandColors.Border.toInt())
        }
        backgroundTintList = null
        textSize = 16f
        isSingleLine = true
        inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
        imeOptions = EditorInfo.IME_ACTION_SEND
        setPadding(dp(12), dp(8), dp(12), dp(8))
        setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_SEND) { submit(); true } else false
        }
    }
    private val composer = LinearLayout(service).apply {
        gravity = Gravity.CENTER_VERTICAL
        setPadding(0, dp(8), 0, dp(4))
        addView(input, LinearLayout.LayoutParams(0, dp(56), 1f))
        addView(button("Send") { submit() }.apply {
            setTextColor(BrandColors.Background.toInt())
            backgroundTintList = ColorStateList.valueOf(accent)
        })
    }
    private val root = LinearLayout(service).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(8), dp(16), dp(12))
        elevation = dp(12).toFloat()
        background = GradientDrawable(GradientDrawable.Orientation.TL_BR,
            intArrayOf(BrandColors.SurfaceRaised.toInt(), BrandColors.Surface.toInt())).apply {
            cornerRadius = dp(26).toFloat()
            setStroke(dp(1), BrandColors.Border.toInt())
        }
        val header = LinearLayout(service).apply {
            gravity = Gravity.CENTER_VERTICAL
            addView(title, LinearLayout.LayoutParams(0, dp(48), 1f))
            addView(button("×") { onHide() }.apply { contentDescription = "Hide assistant" })
        }
        addView(header)
        addView(controls)
        addView(promptScroll, LinearLayout.LayoutParams(-1, dp(76)))
        addView(composer)
    }
    private val params = WindowManager.LayoutParams(
        dp(250), WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
        y = dp(24)
        softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
        setTitle("Jarvis assistant")
    }

    fun show() {
        if (attached) return
        windows.addView(root, params)
        attached = true
        render(state)
    }

    fun render(next: OverlayState) {
        val modeChanged = state.mode != next.mode
        // A draft answer is scoped to one question; it must not become an answer to a later one.
        if (state.questionId != next.questionId || state.mode != next.mode) input.text.clear()
        state = next
        if (modeChanged && next.mode == OverlayMode.PROCESSING) expanded = false
        title.text = when (next.mode) {
            OverlayMode.IDLE -> "●  JARVIS"
            OverlayMode.PROCESSING -> "●  Working…"
            OverlayMode.WAITING_FOR_USER -> "●  Needs your input"
            OverlayMode.PAUSING -> "●  Pausing…"
            OverlayMode.PAUSED -> "●  Paused"
        }
        title.contentDescription = "${title.text}. Tap to ${if (expanded) "collapse" else "expand"} assistant"
        prompt.text = next.question ?: next.message ?: "How can I help?"
        input.hint = if (next.questionId == null) "What should Jarvis do?" else "Your answer"
        controls.visibility = if (next.mode == OverlayMode.IDLE) View.GONE else View.VISIBLE
        pause.text = if (next.mode == OverlayMode.PAUSED) "Continue" else "Pause"
        pause.isEnabled = next.mode != OverlayMode.PAUSING
        promptScroll.visibility = if (expanded) View.VISIBLE else View.GONE
        composer.visibility = if (expanded && next.acceptsInput) View.VISIBLE else View.GONE
        updateWindow()
    }

    /** Collapse before the phone resumes acting; a keyboard must never steal the task app's focus. */
    fun collapse() = setExpanded(false)

    fun openComposer() {
        show()
        setExpanded(true)
    }

    fun hideForCapture(hidden: Boolean) {
        root.visibility = if (hidden) View.INVISIBLE else View.VISIBLE
    }

    fun close() {
        if (!attached) return
        keyboard.hideSoftInputFromWindow(input.windowToken, 0)
        windows.removeView(root)
        attached = false
    }

    private fun setExpanded(value: Boolean) {
        expanded = value
        render(state)
        if (value && state.acceptsInput) input.requestFocus()
    }

    private fun updateWindow() {
        val editing = expanded && state.acceptsInput
        params.flags = WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            (if (editing) 0 else WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
        val available = windows.currentWindowMetrics.bounds.width() - dp(32)
        params.width = minOf(dp(if (expanded) 400 else 250), available.coerceAtLeast(dp(120)))
        if (!editing) {
            keyboard.hideSoftInputFromWindow(input.windowToken, 0)
            input.clearFocus()
        }
        if (attached) windows.updateViewLayout(root, params)
    }

    private fun submit() {
        if (!state.acceptsInput || input.text.isBlank()) return
        // Capture input before closing focus; the service rechecks current task/question state.
        val event = AssistantInput(input.text.toString(), questionId = state.questionId)
        collapse()
        if (onInput(event)) input.text.clear()
    }

    private fun button(label: String, click: () -> Unit) = Button(service).apply {
        text = label
        isAllCaps = false
        setTextColor(accent)
        backgroundTintList = ColorStateList.valueOf(BrandColors.SurfaceRaised.toInt())
        minWidth = dp(56)
        minimumWidth = dp(56)
        setOnClickListener { click() }
    }

    private fun dp(value: Int) = (value * service.resources.displayMetrics.density).toInt()
}
