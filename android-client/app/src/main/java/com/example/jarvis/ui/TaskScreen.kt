package com.example.jarvis.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.example.jarvis.agent.AgentUiState

@Composable
fun TaskScreen(
    state: AgentUiState,
    onSubmitTask: (String) -> Unit,
    onStop: () -> Unit,
    onAnswer: (questionId: String, text: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var task by rememberSaveable { mutableStateOf("") }
    val submit = { if (task.isNotBlank() && !state.running) onSubmitTask(task.trim()) }

    Column(
        modifier = modifier.fillMaxSize().imePadding().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        OutlinedTextField(
            value = task,
            onValueChange = { task = it },
            label = { Text("What should Jarvis do?") },
            enabled = !state.running,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { submit() }),
            modifier = Modifier.fillMaxWidth(),
        )
        if (state.running) {
            OutlinedButton(onClick = onStop) { Text("Stop") }
        }
        state.question?.let { question ->
            Text(question.text)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { onAnswer(question.id, "yes") }) { Text("Yes") }
                OutlinedButton(onClick = { onAnswer(question.id, "no") }) { Text("No") }
            }
        }
        LazyColumn(modifier = Modifier.weight(1f)) {
            items(state.log) { line -> Text(line) }
        }
    }
}
