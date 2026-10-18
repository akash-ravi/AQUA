package com.example.jarvis.agent

import com.example.jarvis.device.CommandException
import com.example.jarvis.device.CommandExecutor
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
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class AgentSocket(
    private val url: String,
    private val executor: CommandExecutor,
    private val scope: CoroutineScope,
    private val onFinished: () -> Unit,
) {
    private val client = OkHttpClient.Builder()
        .pingInterval(PING_INTERVAL_SECONDS, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private val incomingCommands = Channel<JSONObject>(Channel.UNLIMITED)
    private val finished = AtomicBoolean(false)
    private var webSocket: WebSocket? = null
    private var commandWorker: Job? = null
    private var commandCount = 0

    fun start(task: String, device: JSONObject) {
        commandWorker = scope.launch { for (command in incomingCommands) runCommand(command) }
        val startMessage = JSONObject().put("type", "start").put("task", task).put("device", device)
        webSocket = client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                AgentState.log("connected to $url")
                webSocket.send(startMessage.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) = handleMessage(text)

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = finish("connection closed")

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
                finish("connection failed: ${t.message}")
        })
    }

    fun answer(questionId: String, text: String) {
        AgentState.clearQuestion()
        AgentState.log("> $text")
        webSocket?.send(JSONObject().put("type", "answer").put("id", questionId).put("text", text).toString())
    }

    fun stop() {
        if (finished.get()) return
        webSocket?.send(JSONObject().put("type", "stop").toString())
        webSocket?.close(NORMAL_CLOSE, null)
        finish("stopped")
    }

    private fun handleMessage(text: String) {
        val message = try {
            JSONObject(text)
        } catch (e: Exception) {
            AgentState.log("ignored malformed message")
            return
        }
        when (message.optString("type")) {
            "command" -> incomingCommands.trySend(message)
            "ask" -> AgentState.ask(Question(message.optString("id"), message.optString("question")))
            "done" -> closeWith("done: ${message.optString("summary")}")
            "error" -> closeWith("server error: ${message.optString("message")}")
            else -> AgentState.log("ignored message of type '${message.optString("type")}'")
        }
    }

    private suspend fun runCommand(command: JSONObject) {
        val name = command.optString("name")
        val args = command.optJSONObject("args") ?: JSONObject()
        AgentState.log("$name $args")
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
    }

    private fun closeWith(message: String) {
        webSocket?.close(NORMAL_CLOSE, null)
        finish(message)
    }

    private fun finish(message: String) {
        if (!finished.compareAndSet(false, true)) return
        AgentState.log(message)
        incomingCommands.close()
        commandWorker?.cancel()
        onFinished()
    }

    companion object {
        private const val MAX_COMMANDS = 100
        private const val NORMAL_CLOSE = 1000
        private const val PING_INTERVAL_SECONDS = 20L
    }
}
