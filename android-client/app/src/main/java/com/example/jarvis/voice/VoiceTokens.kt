package com.example.jarvis.voice

import android.os.SystemClock
import android.util.Log
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException

/** A single-use ElevenLabs token from the Mac (`POST /voice/<kind>`); [voiceId] is set for speech out. */
internal class VoiceToken(val token: String, val voiceId: String, val fetchedAt: Long = SystemClock.elapsedRealtime())

/**
 * Keeps one token of each kind fetched ahead, so a turn never waits on the Mac and ElevenLabs before it
 * can connect. The ElevenLabs key itself stays in backend/.env. Callbacks run on OkHttp threads.
 */
internal class VoiceTokens(agentUrl: String, private val client: OkHttpClient) {
    private val base = agentUrl.replaceFirst("ws", "http").toHttpUrl()
    private val ready = HashMap<String, VoiceToken>()
    private val inFlight = HashMap<String, MutableList<(VoiceToken?) -> Unit>>()

    fun prefetch(kind: String) = take(kind, keep = true) {}

    /** Hands over a ready token, or fetches one; null when the Mac has no key or can't be reached. */
    fun take(kind: String, onToken: (VoiceToken?) -> Unit) = take(kind, keep = false, onToken)

    private fun take(kind: String, keep: Boolean, onToken: (VoiceToken?) -> Unit) {
        val cached = synchronized(this) {
            val fresh = ready[kind]?.takeIf { SystemClock.elapsedRealtime() - it.fetchedAt < FRESH_MS }
            if (fresh != null && keep) return
            ready.remove(kind)
            if (fresh == null) {
                val waiting = inFlight.getOrPut(kind) { mutableListOf<(VoiceToken?) -> Unit>().also { fetch(kind) } }
                if (!keep) waiting += onToken
                return
            }
            fresh
        }
        onToken(cached)
        prefetch(kind)
    }

    private fun fetch(kind: String) {
        val request = Request.Builder().url(base.resolve("/voice/$kind")!!).post(ByteArray(0).toRequestBody()).build()
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.w(TAG, "Voice token request failed", e)
                deliver(kind, null)
            }

            override fun onResponse(call: Call, response: Response) {
                val token = response.use {
                    val body = it.body?.string().orEmpty()
                    if (!it.isSuccessful) {
                        Log.w(TAG, "Voice token refused (${it.code}): $body")
                        return@use null
                    }
                    runCatching { JSONObject(body) }.getOrNull()?.let { json ->
                        VoiceToken(json.optString("token"), json.optString("voice_id")).takeIf { t -> t.token.isNotEmpty() }
                    }
                }
                deliver(kind, token)
            }
        })
    }

    private fun deliver(kind: String, token: VoiceToken?) {
        val waiting = synchronized(this) {
            val waiting = inFlight.remove(kind).orEmpty()
            // The first waiter consumes it; with nobody waiting it is kept for the next turn.
            if (token != null && waiting.isEmpty()) ready[kind] = token
            waiting
        }
        waiting.firstOrNull()?.invoke(token)
        // Single-use: anyone else waiting gets a token of their own.
        waiting.drop(1).forEach { take(kind, it) }
        if (token != null && waiting.isNotEmpty()) prefetch(kind)
    }

    private companion object {
        const val FRESH_MS = 13 * 60_000L // tokens expire after 15 minutes
        const val TAG = "JarvisVoice"
    }
}
