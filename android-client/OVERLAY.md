# Floating assistant overlay

Enable the Jarvis accessibility service. Tap the floating bar to type a task or answer
the current question. Send collapses the panel; Stop cancels the task.

## Structure

- `PhoneUseService` owns the window and collects `AgentState.state` on the main thread.
  Task generations prevent an old socket callback from ending a newer task.
- `overlay/OverlayState.kt` maps task state to Idle, Processing, or Waiting for user.
  `routeInput` rejects busy submissions, blank/oversized text, and stale answers.
- `overlay/AgentOverlay.kt` renders a bottom-centered Android Views panel using
  `TYPE_ACCESSIBILITY_OVERLAY`. It takes keyboard focus only while editing and releases
  it before the phone resumes acting. The service removes the window when destroyed.

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
Dragging/docking, chat history, process recovery, and screenshot exclusion are not implemented;
the panel can still cover controls near the bottom.
