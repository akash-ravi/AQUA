package com.example.jarvis.agent

import com.example.jarvis.device.CommandException
import com.example.jarvis.device.CommandExecutor
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import org.json.JSONArray
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class AgentSocket(
    private val url: String,
    private val executor: CommandExecutor,
    private val scope: CoroutineScope,
    private val onFinished: (TaskOutcome, String) -> Unit,
) {
    private val client = OkHttpClient.Builder()
        .pingInterval(PING_INTERVAL_SECONDS, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private val incomingCommands = Channel<JSONObject>(Channel.UNLIMITED)
    private val finished = AtomicBoolean(false)
    private val ready = AtomicBoolean(false)
    private var webSocket: WebSocket? = null
    private var commandWorker: Job? = null
    private var commandCount = 0

    fun start(task: String, device: JSONObject) {
        commandWorker = scope.launch { for (command in incomingCommands) runCommand(command) }
        device.put("capabilities", JSONArray(listOf("spokenSecretsV1")))
        val startMessage = JSONObject().put("type", "start").put("task", task).put("device", device)
        webSocket = client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (finished.get()) { webSocket.cancel(); return }
                AgentState.log("connected to $url")
                webSocket.send(startMessage.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) = handleMessage(text)

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                finish("Connection closed. Check the task before trying again.")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e("JarvisConnection", "Task WebSocket failed", t)
                finish("Connection failed. Check the backend address and Wi-Fi before trying again.")
            }
        })
    }

    fun answer(questionId: String, text: String, voice: Boolean = false): Boolean {
        val question = AgentState.state.value.question ?: return false
        if (question.id != questionId) return false
        val message = JSONObject().put("type", "answer").put("id", questionId)
        if (question.secret) {
            if (text.trim().lowercase().trimEnd('.', '!', '?') in setOf("done", "entered", "finished")) {
                executor.cancelPendingSecrets()
                message.put("text", "done")
            } else {
                if (!voice) return false
                val ref = executor.storeSecret(questionId, text) ?: return false
                message.put("source", "voice").put("secret_ref", ref)
            }
        } else message.put("text", text).put("source", if (voice) "voice" else "text")
        if (webSocket?.send(message.toString()) != true) {
            executor.cancelPendingSecrets()
            return false
        }
        AgentState.clearQuestion()
        AgentState.log(if (question.secret) "> Secret entry submitted" else "> Answer submitted")
        return true
    }

    fun stop() {
        if (finished.get()) return
        webSocket?.send(JSONObject().put("type", "stop").toString())
        webSocket?.close(NORMAL_CLOSE, null)
        finish("Stopped", TaskOutcome.STOPPED)
    }

    // No "stop" frame: the backend must log a lost phone, not a user Stop.
    fun disconnect(reason: String) {
        if (finished.get()) return
        closeWith(reason, TaskOutcome.FAILED)
    }

    fun pause() {
        if (ready.get() && !finished.get() && webSocket?.send(JSONObject().put("type", "pause").toString()) == true) {
            AgentState.requestPause()
            executor.cancelPendingSecrets()
        }
    }

    fun resume() {
        if (ready.get() && !finished.get()) webSocket?.send(JSONObject().put("type", "resume").toString())
    }

    private fun handleMessage(text: String) {
        if (finished.get()) return
        val message = try {
            JSONObject(text)
        } catch (e: Exception) {
            AgentState.log("ignored malformed message")
            return
        }
        when (message.optString("type")) {
            "command" -> incomingCommands.trySend(message)
            "ask" -> scope.launch {
                if (!finished.get()) {
                    val id = message.optString("id")
                    val secret = message.optString("kind") == "secret"
                    executor.cancelPendingSecrets()
                    if (secret) {
                        try { executor.prepareSecret(id, message.optJSONObject("secretTarget")) }
                        catch (error: Exception) { AgentState.log("Secret voice entry unavailable; enter it directly on the phone") }
                    }
                    AgentState.ask(Question(id, message.optString("question"), secret))
                }
            }
            "progress" -> when (message.optString("state")) {
                "paused" -> AgentState.setPaused(true)
                "running" -> {
                    AgentState.setPaused(false)
                    val status = message.optString("text")
                    if (status.isNotBlank()) {
                        AgentState.log("Jarvis: $status")
                        AgentState.status(status)
                    }
                }
            }
            "done" -> {
                closeWith(message.optString("summary"),
                    TaskOutcome.fromWire(message.optString("outcome", "completed")))
            }
            "error" -> {
                closeWith(message.optString("message"), TaskOutcome.FAILED)
            }
            else -> AgentState.log("ignored message of type '${message.optString("type")}'")
        }
    }

    private suspend fun runCommand(command: JSONObject) {
        val name = command.optString("name")
        val args = command.optJSONObject("args") ?: JSONObject()
        AgentState.log(if (name == "setText") "setText [text hidden]" else "$name $args")
        val reply = JSONObject().put("type", "result").put("id", command.optString("id"))
        try {
            if (++commandCount > MAX_COMMANDS) throw CommandException("stepLimit", "limit of $MAX_COMMANDS commands reached")
            reply.put("ok", true).put("data", executor.execute(name, args))
        } catch (e: CancellationException) {
            throw e
        } catch (e: CommandException) {
            AgentState.log("  ${e.code}: ${e.message}")
            reply.put("ok", false).put("error", e.code).put("message", e.message)
        } catch (e: Exception) {
            AgentState.log("  actionFailed: ${e.message}")
            reply.put("ok", false).put("error", "actionFailed").put("message", e.message)
        }
        webSocket?.send(reply.toString())
        // Pause is available once the backend loop has reached its first observation.
        if (name == "readScreen" && reply.optBoolean("ok") && !finished.get()) ready.set(true)
    }

    private fun closeWith(message: String, outcome: TaskOutcome) {
        webSocket?.close(NORMAL_CLOSE, null)
        finish(message, outcome)
    }

    private fun finish(message: String, outcome: TaskOutcome = TaskOutcome.FAILED) {
        if (!finished.compareAndSet(false, true)) return
        ready.set(false)
        AgentState.log(message)
        incomingCommands.close()
        commandWorker?.cancel()
        executor.clearSecrets()
        onFinished(outcome, message)
    }

    companion object {
        private const val MAX_COMMANDS = 100
        private const val NORMAL_CLOSE = 1000
        private const val PING_INTERVAL_SECONDS = 20L
    }
}
