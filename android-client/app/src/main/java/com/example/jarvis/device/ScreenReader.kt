package com.example.jarvis.device

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

fun AccessibilityNodeInfo.label(): String = (text ?: contentDescription)?.toString().orEmpty()

class ScreenReader(private val service: AccessibilityService) {

    private class Snapshot(val screenId: String, val nodes: List<AccessibilityNodeInfo>)

    private var snapshot: Snapshot? = null
    private var snapshotCounter = 0

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
        val current = snapshot
        if (current == null || current.screenId != screenId) {
            throw CommandException("staleScreen", "screenId $screenId is not the latest screen, call readScreen again")
        }
        return current.nodes.getOrNull(index) ?: throw CommandException("nodeNotFound", "no node at index $index")
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
        return JSONObject()
            .put("index", index)
            .put("class", node.className?.toString()?.substringAfterLast('.'))
            .put("text", node.text?.toString())
            .put("desc", node.contentDescription?.toString())
            .put("id", node.viewIdResourceName)
            .put("bounds", JSONArray(listOf(bounds.left, bounds.top, bounds.right, bounds.bottom)))
            .put("clickable", node.isClickable)
            .put("editable", node.isEditable)
            .put("scrollable", node.isScrollable)
            .put("checked", if (node.isCheckable) node.isChecked else JSONObject.NULL)
    }
}
