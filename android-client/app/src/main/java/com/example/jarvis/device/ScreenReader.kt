package com.example.jarvis.device

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

fun AccessibilityNodeInfo.label(): String = (text ?: contentDescription)?.toString().orEmpty()

class ScreenReader(private val service: AccessibilityService, private val secrets: SecretVault = SecretVault()) {

    private class Snapshot(val screenId: String, val nodes: List<AccessibilityNodeInfo>)

    private var snapshot: Snapshot? = null
    private var snapshotCounter = 0

    fun fieldKey(node: AccessibilityNodeInfo): String = listOf(node.packageName, node.viewIdResourceName,
        node.className, node.hintText, if (node.viewIdResourceName.isNullOrEmpty()) Rect().also { node.getBoundsInScreen(it) } else "").joinToString("|")

    fun isSensitive(node: AccessibilityNodeInfo): Boolean = node.isPassword || secrets.isProtected(fieldKey(node)) ||
        (node.isEditable && Regex("password|passcode|\\bpin\\b|otp|one.?time|\\bcode\\b|cvv|cvc|card.?number", RegexOption.IGNORE_CASE)
            .containsMatchIn(listOf(node.viewIdResourceName, node.hintText, node.contentDescription).joinToString(" ")))

    fun hasSensitiveFields(): Boolean {
        val root = service.rootInActiveWindow ?: return true
        fun contains(node: AccessibilityNodeInfo): Boolean = isSensitive(node) ||
            (0 until node.childCount).any { node.getChild(it)?.let(::contains) == true }
        return contains(root)
    }

    fun contextLabels(): String = snapshot?.nodes.orEmpty().filterNot(::isSensitive).joinToString(" ") { it.label() }

    fun packageName(): String = service.rootInActiveWindow?.packageName?.toString().orEmpty()

    fun read(): JSONObject {
        val root = service.rootInActiveWindow ?: throw CommandException("actionFailed", "no active window")
        val nodes = mutableListOf<AccessibilityNodeInfo>().also { collectUsefulNodes(root, it) }
        val current = Snapshot("s${++snapshotCounter}", nodes)
        snapshot = current
        return JSONObject()
            .put("screenId", current.screenId)
            .put("package", root.packageName?.toString())
            .put("nodes", JSONArray(nodes.mapIndexed { index, node -> describe(index, node) }))
    }

    fun nodeFor(screenId: String, index: Int): AccessibilityNodeInfo {
        requireSnapshot(screenId)
        val current = snapshot
        return current?.nodes?.getOrNull(index) ?: throw CommandException("nodeNotFound", "no node at index $index")
    }

    fun requireSnapshot(screenId: String) {
        if (snapshot?.screenId != screenId) throw CommandException("staleScreen", "Read the current screen again")
    }

    fun labelsAt(x: Int, y: Int): List<String> {
        val bounds = Rect()
        return snapshot?.nodes.orEmpty()
            .filter { node -> node.getBoundsInScreen(bounds); bounds.contains(x, y) }
            .map { it.label() }
    }

    private fun collectUsefulNodes(node: AccessibilityNodeInfo, out: MutableList<AccessibilityNodeInfo>) {
        if (!node.isVisibleToUser) return
        val useful = node.label().isNotEmpty() || node.isClickable || node.isEditable || node.isScrollable
        if (useful) out += node
        repeat(node.childCount) { index -> node.getChild(index)?.let { collectUsefulNodes(it, out) } }
    }

    private fun describe(index: Int, node: AccessibilityNodeInfo): JSONObject {
        val bounds = Rect().also { node.getBoundsInScreen(it) }
        val sensitive = isSensitive(node)
        return JSONObject()
            .put("index", index)
            .put("class", node.className?.toString()?.substringAfterLast('.'))
            .put("text", if (sensitive) "[hidden]" else secrets.redact(node.text?.toString()))
            .put("desc", if (sensitive) "Secret field" else secrets.redact(node.contentDescription?.toString()))
            .put("sensitive", sensitive)
            .put("id", node.viewIdResourceName)
            .put("bounds", JSONArray(listOf(bounds.left, bounds.top, bounds.right, bounds.bottom)))
            .put("clickable", node.isClickable)
            .put("editable", node.isEditable)
            .put("scrollable", node.isScrollable)
            .put("checked", if (node.isCheckable) node.isChecked else JSONObject.NULL)
    }
}
