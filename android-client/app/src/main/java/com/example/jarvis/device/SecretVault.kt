package com.example.jarvis.device

import java.util.UUID

/** Task-local values. Only opaque one-use references leave the phone; nothing is persisted. */
class SecretVault(private val now: () -> Long = { System.nanoTime() / 1_000_000 }) {
    private data class Prompt(val id: String, val packageName: String, val field: String, val expires: Long, val numeric: Boolean)
    private data class Entry(val ref: String, val value: CharArray, val expires: Long)
    private var prompt: Prompt? = null
    private var entry: Entry? = null
    private val redactions = mutableListOf<CharArray>()
    private val fields = mutableSetOf<String>()

    @Synchronized fun prepare(id: String, packageName: String, field: String, numeric: Boolean = false) {
        cancelPending()
        prompt = Prompt(id, packageName, field, now() + 600_000, numeric)
    }

    @Synchronized fun store(questionId: String, value: String): String? {
        val target = prompt ?: return null
        if (target.id != questionId || now() >= target.expires || value.isBlank() || value.length > 2000 || entry != null) return null
        val supplied = if (target.numeric) digits(value) ?: return null else value
        val ref = UUID.randomUUID().toString()
        entry = Entry(ref, supplied.toCharArray(), now() + 120_000)
        redactions += value.toCharArray()
        if (supplied != value) redactions += supplied.toCharArray()
        fields += target.field
        return ref
    }

    @Synchronized fun consume(ref: String, packageName: String, field: String): String? {
        val target = prompt
        val stored = entry
        // Any use consumes the reference, even a mismatched/expired attempt.
        prompt = null
        entry = null
        if (stored == null) return null
        return try {
            if (target == null || stored.ref != ref || target.packageName != packageName || target.field != field
                || now() >= stored.expires || now() >= target.expires) null else String(stored.value)
        } finally { stored.value.fill('\u0000') }
    }

    @Synchronized fun redact(value: String?): String? {
        var result = value ?: return null
        redactions.forEach { secret -> result = result.replace(String(secret), "[hidden]") }
        return result
    }

    @Synchronized fun isProtected(field: String) = field in fields
    @Synchronized fun hasSensitiveData() = redactions.isNotEmpty()

    @Synchronized fun cancelPending() {
        entry?.value?.fill('\u0000')
        entry = null
        prompt = null
    }

    @Synchronized fun clear() {
        cancelPending()
        redactions.forEach { it.fill('\u0000') }
        redactions.clear()
        fields.clear()
    }

    private fun digits(value: String): String? {
        val names = listOf("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
        val result = StringBuilder()
        for (token in value.lowercase().replace(Regex("[,.;:!?-]"), " ").trim().split(Regex("\\s+"))) {
            when {
                token.all { it in '0'..'9' } -> result.append(token)
                token == "oh" || token == "o" -> result.append('0')
                token in names -> result.append(names.indexOf(token))
                else -> return null
            }
        }
        return result.toString().ifEmpty { null }
    }
}
