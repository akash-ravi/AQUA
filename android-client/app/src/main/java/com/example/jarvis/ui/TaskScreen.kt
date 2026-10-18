package com.example.jarvis.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.jarvis.agent.AgentUiState
import com.example.jarvis.agent.MAX_INPUT_CHARS

@Composable
fun TaskScreen(
    state: AgentUiState,
    onSubmitTask: (String) -> Unit,
    onStop: () -> Unit,
    onPause: () -> Unit,
    onContinue: () -> Unit,
    onShowAssistant: () -> Unit,
    onSetDefaultAssistant: () -> Unit,
    onAnswer: (questionId: String, text: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var task by rememberSaveable { mutableStateOf("") }
    var answer by rememberSaveable(state.question?.id) { mutableStateOf("") }
    var showLog by rememberSaveable { mutableStateOf(false) }
    val colors = MaterialTheme.colorScheme
    val canSubmit = !state.running && task.isNotBlank() && task.trim().length <= MAX_INPUT_CHARS
    val submit = { if (canSubmit) onSubmitTask(task.trim()) }
    val canAnswer = answer.isNotBlank() && answer.trim().length <= MAX_INPUT_CHARS
    val status = when {
        state.failed -> "CHECK TASK"
        !state.running -> "READY"
        state.pauseRequested -> "PAUSING"
        state.paused -> "PAUSED"
        state.question != null -> "NEEDS YOU"
        else -> "WORKING"
    }

    LazyColumn(
        modifier = modifier.fillMaxSize().imePadding(),
        contentPadding = PaddingValues(24.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Box(Modifier.size(48.dp).background(
                    Brush.linearGradient(listOf(colors.primary, colors.secondary)), RoundedCornerShape(16.dp)),
                    contentAlignment = Alignment.Center) {
                    Text("J", color = colors.onPrimary, fontSize = 26.sp, fontWeight = FontWeight.Black)
                }
                Column(Modifier.weight(1f)) {
                    Text("AQUA", fontWeight = FontWeight.Bold, letterSpacing = 4.sp, fontSize = 16.sp)
                    Text("A little less doing.", color = colors.onSurfaceVariant, fontSize = 12.sp)
                }
                Surface(color = colors.primary.copy(alpha = 0.12f), shape = RoundedCornerShape(50)) {
                    Text(status, Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                        color = colors.tertiary, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(if (state.running) "Consider it\nin motion." else "Your day.\nOne request away.",
                    fontSize = 38.sp, lineHeight = 43.sp, fontWeight = FontWeight.Bold,
                    color = colors.onBackground)
                Text("Find it. Open it. Get it done.", color = colors.onSurfaceVariant)
            }
        }
        item {
            Card(shape = RoundedCornerShape(28.dp),
                colors = CardDefaults.cardColors(containerColor = colors.surface)) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Text(if (state.running) "CURRENT TASK" else "WHAT CAN I DO FOR YOU?",
                        color = colors.tertiary, fontSize = 11.sp, letterSpacing = 1.5.sp,
                        fontWeight = FontWeight.Bold)
                    if (state.running) {
                        Text(state.message ?: "Working on your request", style = MaterialTheme.typography.titleMedium)
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Button(onClick = if (state.paused) onContinue else onPause,
                                enabled = !state.pauseRequested) {
                                Text(if (state.paused) "Continue" else "Pause")
                            }
                            OutlinedButton(onClick = onStop) { Text("Stop", color = colors.secondary) }
                        }
                    } else {
                        OutlinedTextField(
                            value = task, onValueChange = { task = it },
                            placeholder = { Text("Find headphones on Amazon…") },
                            minLines = 2, maxLines = 4, shape = RoundedCornerShape(18.dp),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                            keyboardActions = KeyboardActions(onSend = { submit() }),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Button(onClick = submit, enabled = canSubmit,
                            shape = RoundedCornerShape(16.dp),
                            modifier = Modifier.fillMaxWidth()) { Text("Start task  ↗", Modifier.padding(6.dp)) }
                    }
                }
            }
        }
        if (!state.running) {
            item {
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("Find a product" to "Search Amazon for ",
                        "Order food" to "Open DoorDash and find ",
                        "Call a contact" to "Call ").forEach { (label, draft) ->
                        OutlinedButton(onClick = { task = draft }) { Text(label, fontSize = 12.sp) }
                    }
                }
            }
        }
        state.question?.let { question ->
            item(key = question.id) {
                Card(shape = RoundedCornerShape(24.dp)) {
                    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("Over to you", color = colors.tertiary, fontWeight = FontWeight.Bold)
                        Text(question.text)
                        OutlinedTextField(value = answer, onValueChange = { answer = it },
                            label = { Text("Your answer") }, shape = RoundedCornerShape(16.dp),
                            modifier = Modifier.fillMaxWidth())
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { onAnswer(question.id, answer.trim()) }, enabled = canAnswer) { Text("Send") }
                            TextButton(onClick = { onAnswer(question.id, "yes") }) { Text("Yes") }
                            TextButton(onClick = { onAnswer(question.id, "no") }) { Text("No") }
                        }
                    }
                }
            }
        }
        if (!state.running && state.message != null) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("LAST UPDATE", color = colors.tertiary, fontSize = 11.sp, letterSpacing = 1.5.sp)
                    Text(state.message, color = colors.onSurfaceVariant)
                }
            }
        }
        item {
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onShowAssistant, modifier = Modifier.fillMaxWidth()) { Text("Open floating assistant") }
                TextButton(onClick = onSetDefaultAssistant) { Text("Set as default assistant") }
                if (state.log.isNotEmpty()) {
                    TextButton(onClick = { showLog = !showLog }) { Text(if (showLog) "Hide activity log" else "View activity log") }
                }
            }
        }
        if (showLog) {
            item {
                Card(shape = RoundedCornerShape(20.dp)) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        state.log.takeLast(20).forEach { Text(it, fontSize = 11.sp, color = colors.onSurfaceVariant) }
                    }
                }
            }
        }
    }
}
