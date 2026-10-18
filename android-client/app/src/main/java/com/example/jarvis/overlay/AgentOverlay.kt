package com.example.jarvis.overlay

import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.content.res.ColorStateList
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.example.jarvis.R
import com.example.jarvis.ui.BrandColors

/**
 * Service-owned window: a bare orb by default; tapping it opens the panel with status, controls and
 * the text box. All calls, including state rendering and removal, run on the main thread.
 */
class AgentOverlay(
    private val context: Context,
    private val onInput: (AssistantInput) -> Boolean,
    private val onStop: () -> Unit,
    private val onPause: () -> Unit,
    private val onContinue: () -> Unit,
    private val onHide: () -> Unit,
    private val onMic: () -> Unit,
    level: () -> Float,
) {
    private val windows = context.getSystemService(WindowManager::class.java)
    private val keyboard = context.getSystemService(InputMethodManager::class.java)
    private val accent = BrandColors.Orange.toInt()
    private var state = OverlayState()
    private var expanded = false
    private var attached = false
    private var dismissing = false
    private var listening = false

    private val orb = OrbView(context, level).apply {
        setOnClickListener { setExpanded(!expanded) }
    }
    private val title = TextView(context).apply {
        setTextColor(BrandColors.Text.toInt())
        textSize = 16f
        letterSpacing = 0.04f
        setTypeface(typeface, Typeface.BOLD)
        maxLines = 1
        ellipsize = TextUtils.TruncateAt.END
        setPadding(dp(8), dp(12), dp(8), dp(12))
        setOnClickListener { setExpanded(!expanded) }
    }
    private val stop = button("Stop") { onStop() }.apply { setTextColor(BrandColors.Red.toInt()) }
    private val pause = button("Pause") { if (state.mode == OverlayMode.PAUSED) onContinue() else onPause() }
    private val controls = LinearLayout(context).apply {
        gravity = Gravity.END
        addView(pause)
        addView(stop)
    }
    private val prompt = TextView(context).apply {
        setTextColor(BrandColors.Muted.toInt())
        textSize = 14f
        setPadding(dp(8), dp(8), dp(8), dp(8))
    }
    private val promptScroll = ScrollView(context).apply { addView(prompt) }
    private val input = EditText(context).apply {
        setTextColor(BrandColors.Text.toInt())
        setHintTextColor(BrandColors.Muted.toInt() and 0x00FFFFFF or 0x99000000.toInt())
        background = null
        textCursorDrawable = GradientDrawable().apply { setColor(accent); setSize(dp(2), 0) }
        highlightColor = accent and 0x00FFFFFF or 0x55000000
        textSize = 16f
        // Wraps up to four lines while Enter stays Send (no multi-line input flag).
        inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
        setHorizontallyScrolling(false)
        maxLines = 4
        imeOptions = EditorInfo.IME_ACTION_SEND
        setPadding(0, dp(12), dp(8), dp(12))
        setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_SEND) { submit(); true } else false
        }
        setOnFocusChangeListener { _, focused -> pill.setStroke(dp(1), if (focused) accent else BrandColors.Border.toInt()) }
        addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(text: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(text: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(text: Editable?) = updateAction()
        })
    }
    private val pill = GradientDrawable().apply {
        setColor(BrandColors.Background.toInt())
        cornerRadius = dp(28).toFloat()
        setStroke(dp(1), BrandColors.Border.toInt())
    }
    private val actionBackground = GradientDrawable().apply { shape = GradientDrawable.OVAL }
    /** One round button: mic while the box is empty, stop while listening, send once there is text. */
    private val action = ImageButton(context).apply {
        background = actionBackground
        scaleType = ImageView.ScaleType.CENTER
        setOnClickListener { if (input.text.isBlank()) onMic() else submit() }
    }
    private val composer = LinearLayout(context).apply {
        gravity = Gravity.CENTER_VERTICAL
        background = pill
        minimumHeight = dp(56)
        setPadding(dp(20), dp(4), dp(8), dp(4))
        addView(input, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        addView(action, LinearLayout.LayoutParams(dp(40), dp(40)))
    }
    private val hide = button("×") { onHide() }.apply { contentDescription = "Hide assistant" }
    private val panelBackground = GradientDrawable(GradientDrawable.Orientation.TL_BR,
        intArrayOf(BrandColors.SurfaceRaised.toInt(), BrandColors.Surface.toInt())).apply {
        cornerRadius = dp(26).toFloat()
        setStroke(dp(1), BrandColors.Border.toInt())
    }
    private val root = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        val header = LinearLayout(context).apply {
            gravity = Gravity.CENTER_VERTICAL
            addView(orb, LinearLayout.LayoutParams(dp(ORB_DP), dp(ORB_DP)))
            addView(title, LinearLayout.LayoutParams(0, dp(48), 1f))
            addView(hide)
        }
        addView(header)
        addView(controls)
        addView(promptScroll, LinearLayout.LayoutParams(-1, dp(76)))
        addView(composer, LinearLayout.LayoutParams(-1, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(8)
        })
    }
    private val params = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
        y = dp(24)
        softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
        setTitle("Jarvis assistant")
    }

    /** Rises and scales up into place; showing again mid-dismiss reverses from where it is. */
    fun show() {
        if (attached) {
            if (dismissing) { dismissing = false; animateIn() }
            return
        }
        root.alpha = 0f
        root.scaleX = ENTER_SCALE
        root.scaleY = ENTER_SCALE
        root.translationY = dp(16).toFloat()
        windows.addView(root, params)
        attached = true
        render(state)
        animateIn()
    }

    private fun animateIn() {
        root.animate().alpha(1f).scaleX(1f).scaleY(1f).translationY(0f)
            .setDuration(ENTER_MS).setInterpolator(Motion.EnterEase).start()
    }

    /** Shrinks and fades out, then removes the window. */
    fun dismiss() {
        if (!attached || dismissing) return
        dismissing = true
        keyboard.hideSoftInputFromWindow(input.windowToken, 0)
        root.animate().alpha(0f).scaleX(EXIT_SCALE).scaleY(EXIT_SCALE).translationY(dp(12).toFloat())
            .setDuration(EXIT_MS).setInterpolator(Motion.ExitEase)
            .withEndAction { if (dismissing) close() }.start()
    }

    fun render(next: OverlayState) {
        val modeChanged = state.mode != next.mode
        // A draft answer is scoped to one question; it must not become an answer to a later one.
        if (state.questionId != next.questionId || state.mode != next.mode) input.text.clear()
        state = next
        if (modeChanged && next.mode == OverlayMode.PROCESSING) expanded = false
        orb.mode = next.mode
        orb.listening = listening
        title.text = if (listening) "Listening…" else when (next.mode) {
            OverlayMode.IDLE -> "JARVIS"
            OverlayMode.PROCESSING -> next.status ?: "Working…"
            OverlayMode.WAITING_FOR_USER -> "Needs your input"
            OverlayMode.PAUSING -> "Pausing…"
            OverlayMode.PAUSED -> "Paused"
        }
        title.contentDescription = "${title.text}. Tap to ${if (expanded) "collapse" else "expand"} assistant"
        orb.contentDescription = if (expanded) "Collapse Jarvis" else "Jarvis: ${title.text}. Tap to type"
        prompt.text = next.question ?: next.message ?: "How can I help?"
        input.hint = when {
            listening -> "Listening… or type"
            next.questionId == null -> "What should Jarvis do?"
            else -> "Your answer"
        }
        updateAction()
        controls.visibility = if (expanded && next.mode != OverlayMode.IDLE) View.VISIBLE else View.GONE
        pause.text = if (next.mode == OverlayMode.PAUSED) "Continue" else "Pause"
        pause.isEnabled = next.mode != OverlayMode.PAUSING
        title.visibility = if (expanded) View.VISIBLE else View.GONE
        hide.visibility = if (expanded) View.VISIBLE else View.GONE
        promptScroll.visibility = if (expanded) View.VISIBLE else View.GONE
        composer.visibility = if (expanded && next.acceptsInput) View.VISIBLE else View.GONE
        updateWindow()
    }

    fun setListening(value: Boolean) {
        listening = value
        render(state)
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
        dismissing = false
        root.animate().cancel()
        keyboard.hideSoftInputFromWindow(input.windowToken, 0)
        windows.removeView(root)
        attached = false
    }

    private fun setExpanded(value: Boolean) {
        val opening = value && !expanded
        expanded = value
        render(state)
        if (value && state.acceptsInput) input.requestFocus()
        // The orb grows into the panel rather than cutting to it.
        if (opening && attached && !dismissing) {
            root.scaleX = OPEN_SCALE
            root.scaleY = OPEN_SCALE
            root.animate().scaleX(1f).scaleY(1f).setDuration(ENTER_MS).setInterpolator(Motion.EnterEase).start()
        }
    }

    private fun updateWindow() {
        val editing = expanded && state.acceptsInput
        params.flags = WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            (if (editing) 0 else WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
        // Collapsed, the window is just the orb, so it neither covers nor intercepts the app behind it.
        root.background = if (expanded) panelBackground else null
        root.elevation = if (expanded) dp(12).toFloat() else 0f
        if (expanded) root.setPadding(dp(16), dp(8), dp(16), dp(12)) else root.setPadding(0, 0, 0, 0)
        orb.layoutParams = orb.layoutParams.apply {
            width = dp(if (expanded) 52 else ORB_DP)
            height = width
        }
        val available = windows.currentWindowMetrics.bounds.width() - dp(32)
        params.width = if (expanded) minOf(dp(400), available.coerceAtLeast(dp(120)))
            else WindowManager.LayoutParams.WRAP_CONTENT
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

    private fun updateAction() {
        val (icon, fill, tint, label) = when {
            input.text.isNotBlank() -> ActionLook(R.drawable.ic_send, accent, BrandColors.Background.toInt(), "Send")
            listening -> ActionLook(R.drawable.ic_stop, BrandColors.Red.toInt(), BrandColors.Text.toInt(), "Stop listening")
            else -> ActionLook(R.drawable.ic_mic, BrandColors.SurfaceRaised.toInt(), BrandColors.Amber.toInt(), "Speak")
        }
        action.setImageResource(icon)
        action.imageTintList = ColorStateList.valueOf(tint)
        actionBackground.setColor(fill)
        action.contentDescription = label
    }

    private data class ActionLook(val icon: Int, val fill: Int, val tint: Int, val label: String)

    private fun button(label: String, click: () -> Unit) = Button(context).apply {
        text = label
        isAllCaps = false
        setTextColor(accent)
        backgroundTintList = ColorStateList.valueOf(BrandColors.SurfaceRaised.toInt())
        minWidth = dp(56)
        minimumWidth = dp(56)
        setOnClickListener { click() }
    }

    private fun dp(value: Int) = (value * context.resources.displayMetrics.density).toInt()

    private companion object {
        const val ORB_DP = 104
        const val ENTER_SCALE = 0.8f
        const val EXIT_SCALE = 0.9f
        const val OPEN_SCALE = 0.92f
        const val ENTER_MS = 320L
        const val EXIT_MS = 260L
    }
}
