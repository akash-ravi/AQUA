# Floating assistant overlay

Enable the Jarvis accessibility service once. In the app, tap **Show assistant** to open
the text composer. It can start a task or answer the current question. Send collapses it.

- **×** hides the panel and aura without disabling accessibility or stopping the task.
- **Pause** waits for the current command to finish; **Continue** appears after server
  acknowledgement and resumes the same task after reading a fresh screen.
- **Stop** ends the task. It does not restart automatically; type a new task to work again.
- Tap the running-task notification or **Show assistant** to bring a hidden panel back.
- **Set default assistant** opens Android's assistant chooser. Choose Jarvis yourself;
  its assistant gesture invokes the text composer. Accessibility must also remain enabled.

The aura pulses while working, becomes steady when waiting/paused, and disappears when
idle or hidden. Both windows are temporarily hidden before screenshot capture. The aura
is not touchable and cannot take keyboard focus. The panel is shown on demand, not every
time the accessibility service starts.

## Structure

- `PhoneUseService` owns the window and collects `AgentState.state` on the main thread.
  Task generations prevent an old socket callback from ending a newer task.
- `overlay/OverlayState.kt` maps task state to Idle, Processing, or Waiting for user.
  `routeInput` rejects busy submissions, blank/oversized text, and stale answers.
- `overlay/AgentOverlay.kt` renders a bottom-centered Android Views panel using
  `TYPE_ACCESSIBILITY_OVERLAY`. It takes keyboard focus only while editing and releases
  it before the phone resumes acting. The service removes the window when destroyed.
- `overlay/AuraRing.kt` owns the separate edge-effect window and stops animation when hidden.
- `AssistantActivity` handles `ACTION_ASSIST`. No microphone or voice service is required
  for the text-only assistant entry point.

Deploy the backend and Android changes together: the executor protocol now accepts
`{"type":"pause"}` and `{"type":"resume"}` and reports acknowledged state using
`{"type":"progress","state":"paused"|"running",...}`. Pause does not replay a
command that was already dispatched, and it cancels a pending question; resuming requires
fresh observation and any necessary new approval. A disconnected/terminated session cannot
be resumed by this version.

## Future voice input

Keep microphone capture and streaming separate from the overlay and executor socket.
Partial transcripts can fill the draft; the user reviews and submits the final text
through `AssistantInput`. Add audio session/cancellation handling and microphone permissions
when voice is implemented. No audio-specific code is needed for the text phase.

## Validation

`OverlayStateTest` covers task routing, busy input, and stale questions. Run:

```sh
./gradlew :app:testDebugUnitTest :app:assembleDebug
```

The full build requires this project's JDK 25 / SDK 37 toolchain. Device checks remain
for keyboard placement, app switching, Stop, service restart, rotation, and font scaling.
Dragging/docking, chat history, and process recovery are not implemented;
the panel can still cover controls near the bottom.
