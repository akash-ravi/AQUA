package com.example.jarvis.device

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.text.InputType
import android.util.Base64
import android.view.Display
import android.view.KeyEvent
import android.view.inputmethod.EditorInfo
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class CommandExecutor(
    private val service: AccessibilityService,
    private val clock: UiActivityClock,
    private val beforeScreenshot: suspend () -> Unit = {},
    private val afterScreenshot: () -> Unit = {},
) {
    private val secrets = SecretVault()
    private val screenReader = ScreenReader(service, secrets)
    private val gestures = Gestures(service)
    private val appLauncher = AppLauncher(service)

    suspend fun execute(name: String, args: JSONObject): JSONObject = when (name) {
        "readScreen" -> {
            settle()
            screenReader.read()
        }
        "listApps" -> JSONObject().put("apps", JSONArray(appLauncher.listApps().map(::describeApp)))
        "screenshot" -> {
            if (secrets.hasSensitiveData() || screenReader.hasSensitiveFields()) {
                throw CommandException("secureWindow", "Screenshots are disabled while secret content may be visible")
            }
            if (args.has("screenId")) screenReader.requireSnapshot(args.getString("screenId"))
            try {
                beforeScreenshot()
                screenshot(args.optInt("maxWidth", DEFAULT_SCREENSHOT_WIDTH))
            } finally {
                afterScreenshot()
            }
        }
        "wait" -> {
            delay(args.optLong("ms").coerceIn(0, MAX_WAIT_MS))
            JSONObject()
        }
        else -> act(name, args).also { settle() }.also(::attachScreen)
    }

    private fun attachScreen(result: JSONObject) {
        // The backend reads the screen after every action; sending it now saves a round trip.
        runCatching { screenReader.read() }.onSuccess { result.put("screen", it) }
    }

    private suspend fun act(name: String, args: JSONObject): JSONObject = when (name) {
        "click" -> click(args)
        "longClick" -> longClick(args)
        "tap" -> tap(args)
        "setText" -> setText(args)
        "typeFocused" -> typeFocused(args.optString("text"), args.optBoolean("submit", false))
        "scroll" -> scroll(args)
        "swipe" -> swipe(args)
        "back" -> globalAction(AccessibilityService.GLOBAL_ACTION_BACK)
        "home" -> globalAction(AccessibilityService.GLOBAL_ACTION_HOME)
        "openApp" -> describeApp(appLauncher.open(args.optString("name")))
        else -> throw CommandException("badCommand", "unknown command '$name'")
    }

    private suspend fun click(args: JSONObject): JSONObject {
        val node = nodeFrom(args)
        val target = ancestorsAndSelf(node).firstOrNull { it.isClickable }
        requireConfirmation(args, listOfNotNull(node.label(), target?.label()))
        if (target?.performAction(AccessibilityNodeInfo.ACTION_CLICK) != true) {
            throw CommandException("actionFailed", "Click was not acknowledged; check the result before retrying")
        }
        return JSONObject()
    }

    private fun longClick(args: JSONObject): JSONObject {
        val node = nodeFrom(args)
        val target = ancestorsAndSelf(node).firstOrNull { it.isLongClickable }
        requireConfirmation(args, listOfNotNull(node.label(), target?.label()))
        if (target?.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK) != true) {
            throw CommandException("actionFailed", "long click was not accepted")
        }
        return JSONObject()
    }

    private suspend fun tap(args: JSONObject): JSONObject {
        val x = args.getInt("x")
        val y = args.getInt("y")
        requireConfirmation(args, screenReader.labelsAt(x, y))
        gestures.tap(x, y)
        return JSONObject()
    }

    private fun setText(args: JSONObject): JSONObject {
        val node = nodeFrom(args)
        val submit = args.optBoolean("submit", false)
        if (!node.refresh()) throw CommandException("staleScreen", "The text field changed; read the screen again")
        val value = if (args.has("secretRef")) {
            if (submit) throw CommandException("badCommand", "Secret entry cannot submit the field")
            secrets.consume(args.getString("secretRef"), screenReader.packageName(), screenReader.fieldKey(node))
                ?: throw CommandException("badCommand", "Secret reference is expired, used, or does not match this field")
        } else {
            if (screenReader.isSensitive(node)) throw CommandException("badCommand", "This field needs user-supplied secret entry")
            args.optString("text")
        }
        if (submit) node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val text = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
        }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, text)) {
            throw CommandException("actionFailed", "the field rejected the text")
        }
        if (submit) {
            // ACTION_SET_TEXT only edits the field; it does not invoke the IME's Search/Go action.
            // Refresh this node after editing instead of resolving an old screen index again.
            if (!node.refresh() ||
                !node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
            ) {
                throw CommandException(
                    "cantSubmit",
                    "text entered, but the field could not submit; read the screen and use its Search button or a matching suggestion",
                )
            }
            return JSONObject().put("submitted", true)
        }
        return JSONObject()
    }

    private fun typeFocused(text: String, submit: Boolean): JSONObject {
        val field = service.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.takeIf { it.isEditable }
            ?: return typeWithInputMethod(text, submit)
        if (screenReader.isSensitive(field)) throw CommandException("badCommand", "This field needs user-supplied secret entry")
        val value = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
        if (!field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, value)) return typeWithInputMethod(text, submit)
        if (submit && !(field.refresh() && field.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id))) {
            throw CommandException("cantSubmit", "text entered, but the field could not submit")
        }
        return JSONObject()
    }

    // Apps that hide their views (Lyft) still take text through the accessibility input method.
    private fun typeWithInputMethod(text: String, submit: Boolean): JSONObject {
        val inputMethod = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) service.inputMethod else null
        val connection = inputMethod?.currentInputConnection
            ?: throw CommandException("actionFailed", "No text box has the cursor; tap the box first")
        val editor = inputMethod.currentInputEditorInfo
        if (editor != null && isPasswordInput(editor.inputType)) {
            throw CommandException("badCommand", "This field needs user-supplied secret entry")
        }
        connection.performContextMenuAction(android.R.id.selectAll)
        connection.commitText(text, 1, null)
        if (submit) {
            val action = (editor?.imeOptions ?: 0) and EditorInfo.IME_MASK_ACTION
            if (action > EditorInfo.IME_ACTION_NONE) {
                connection.performEditorAction(action)
            } else {
                connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER))
                connection.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER))
            }
        }
        return JSONObject()
    }

    private fun isPasswordInput(inputType: Int): Boolean {
        val variation = inputType and InputType.TYPE_MASK_VARIATION
        return when (inputType and InputType.TYPE_MASK_CLASS) {
            InputType.TYPE_CLASS_TEXT -> variation == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
                variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD ||
                variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD
            InputType.TYPE_CLASS_NUMBER -> variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD
            else -> false
        }
    }

    private fun scroll(args: JSONObject): JSONObject {
        val action = when (args.optString("direction")) {
            "down", "right" -> AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            "up", "left" -> AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            else -> throw CommandException("badCommand", "direction must be up, down, left or right")
        }
        val scrollable = ancestorsAndSelf(nodeFrom(args)).firstOrNull { it.isScrollable }
            ?: throw CommandException("actionFailed", "node has no scrollable container")
        return JSONObject().put("moved", scrollable.performAction(action))
    }

    private suspend fun swipe(args: JSONObject): JSONObject {
        val context = screenReader.contextLabels()
        if (Regex("\\b(slide|swipe) to\\b", RegexOption.IGNORE_CASE).containsMatchIn(context)
            && ConfirmationGuard.needsConfirmation(context)) {
            throw CommandException("needsConfirmation", "Complete payment sliders directly on the phone")
        }
        gestures.swipe(
            args.getInt("x1"), args.getInt("y1"), args.getInt("x2"), args.getInt("y2"),
            args.optLong("durationMs", DEFAULT_SWIPE_MS),
        )
        return JSONObject()
    }

    private fun globalAction(action: Int): JSONObject {
        if (!service.performGlobalAction(action)) throw CommandException("actionFailed", "global action rejected")
        return JSONObject()
    }

    private suspend fun screenshot(maxWidth: Int): JSONObject = suspendCancellableCoroutine { continuation ->
        val callback = object : AccessibilityService.TakeScreenshotCallback {
            override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                val hardwareBitmap = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                val bitmap = hardwareBitmap?.copy(Bitmap.Config.ARGB_8888, false)
                result.hardwareBuffer.close()
                if (bitmap == null) {
                    continuation.resumeWithException(CommandException("actionFailed", "screenshot had no pixels"))
                } else {
                    continuation.resume(encodeScreenshot(bitmap, maxWidth))
                }
            }

            override fun onFailure(errorCode: Int) {
                val code = if (errorCode == AccessibilityService.ERROR_TAKE_SCREENSHOT_SECURE_WINDOW) "secureWindow" else "actionFailed"
                continuation.resumeWithException(CommandException(code, "screenshot failed with error $errorCode"))
            }
        }
        service.takeScreenshot(Display.DEFAULT_DISPLAY, service.mainExecutor, callback)
    }

    private fun encodeScreenshot(bitmap: Bitmap, maxWidth: Int): JSONObject {
        val scaled = if (bitmap.width > maxWidth) {
            Bitmap.createScaledBitmap(bitmap, maxWidth, bitmap.height * maxWidth / bitmap.width, true)
        } else {
            bitmap
        }
        val png = ByteArrayOutputStream().also { scaled.compress(Bitmap.CompressFormat.PNG, 100, it) }
        return JSONObject()
            .put("png", Base64.encodeToString(png.toByteArray(), Base64.NO_WRAP))
            .put("width", scaled.width)
            .put("height", scaled.height)
    }

    private suspend fun settle() {
        val startedAt = SystemClock.uptimeMillis()
        while (true) {
            val now = SystemClock.uptimeMillis()
            val quietForMs = now - maxOf(clock.lastEventUptimeMs, startedAt)
            if (quietForMs >= SETTLE_QUIET_MS || now - startedAt >= SETTLE_TIMEOUT_MS) return
            delay(SETTLE_POLL_MS)
        }
    }

    private fun nodeFrom(args: JSONObject) = screenReader.nodeFor(args.optString("screenId"), args.optInt("index", -1))

    private fun ancestorsAndSelf(node: AccessibilityNodeInfo) = generateSequence(node) { it.parent }

    private fun requireConfirmation(args: JSONObject, labels: List<String>) {
        val sensitive = labels.firstOrNull { ConfirmationGuard.needsConfirmation(it, screenReader.contextLabels()) } ?: return
        if (!args.optBoolean("confirmed")) {
            throw CommandException("needsConfirmation", "'$sensitive' needs the user's approval, then retry with confirmed=true")
        }
    }

    fun prepareSecret(questionId: String, target: JSONObject?) {
        secrets.cancelPending()
        if (target == null) return
        val node = screenReader.nodeFor(target.getString("screen_id"), target.getInt("target"))
        if (!node.refresh() || !node.isEditable || !screenReader.isSensitive(node) || screenReader.packageName() != target.getString("package_name")) {
            throw CommandException("badCommand", "Secret question does not match the current field")
        }
        val numeric = (node.inputType and InputType.TYPE_MASK_CLASS) == InputType.TYPE_CLASS_NUMBER ||
            Regex("\\bpin\\b|otp|one.?time|\\bcode\\b|cvv|cvc|card.?number", RegexOption.IGNORE_CASE)
                .containsMatchIn(listOf(node.viewIdResourceName, node.hintText, node.contentDescription).joinToString(" "))
        secrets.prepare(questionId, screenReader.packageName(), screenReader.fieldKey(node), numeric)
    }

    fun storeSecret(questionId: String, value: String): String? = secrets.store(questionId, value)
    fun cancelPendingSecrets() = secrets.cancelPending()
    fun clearSecrets() = secrets.clear()

    private fun describeApp(app: InstalledApp) = JSONObject().put("label", app.label).put("package", app.packageName)

    companion object {
        private const val SETTLE_QUIET_MS = 300L
        private const val SETTLE_TIMEOUT_MS = 5000L
        private const val SETTLE_POLL_MS = 50L
        private const val MAX_WAIT_MS = 5000L
        private const val DEFAULT_SWIPE_MS = 300L
        private const val DEFAULT_SCREENSHOT_WIDTH = 720
    }
}
