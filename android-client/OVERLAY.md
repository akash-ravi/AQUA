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

The app, panel, and aura share the orange/red `BrandColors` palette; wallpaper colors do
not override it. The aura uses maximum display bounds, extends into system-bar/cutout areas,
ignores keyboard resizing, and follows the screen's reported corner radii. It updates on
rotation. The app's activity log is collapsed by default.

Overlay views and metrics use a display-bound accessibility window context. If attaching
the optional aura fails, it is disabled for the service session and logged under `JarvisAura`
so task startup can continue without the effect.

Submitting a task intentionally backgrounds the main activity so the agent can operate
other apps. A connection or backend failure must not dismiss the panel. If startup itself
fails, the main activity stays in front and the exception is logged under `JarvisTask`.

Both assistant entry points go through `AssistantActivity`, which releases its invocation
window before opening the composer. If accessibility is not connected, the app opens with
a pending overlay request that is retried when you return from accessibility settings.
When a task completes, the service dismisses both the panel and aura; accessibility stays enabled.
Errors and unexpected disconnects keep an explanation visible instead of silently closing
the assistant. If a backend supplies an `outcome` in its final `done` frame, failed,
unconfirmed, and needs-user outcomes also keep the panel open. A `done` frame without an
outcome is treated as completed for compatibility with the existing backend protocol.
For call requests, the backend is instructed to finish on outgoing-call evidence for the
intended contact, without waiting for an answer or ending the call. A clicked Call button
alone is not proof of completion.

## Structure

- `PhoneUseService` owns the window and collects `AgentState.state` on the main thread.
  Task generations prevent an old socket callback from ending a newer task.
- `overlay/OverlayState.kt` maps task state to Idle, Processing, or Waiting for user.
  `routeInput` rejects busy submissions, blank/oversized text, and stale answers.
- `overlay/AgentOverlay.kt` renders a bottom-centered Android Views panel using
  `TYPE_ACCESSIBILITY_OVERLAY`. It takes keyboard focus only while editing and releases
  it before the phone resumes acting. The service removes the window when destroyed.
- `overlay/AuraRing.kt` owns the separate edge-effect window and stops animation when hidden.
- `assistant/JarvisAssistantService.kt` provides the system-bound digital assistant service
  and session, registered through `@xml/voice_interaction_service` with `supportsAssist=true`.
  Invoking it opens the existing text overlay (or the app if accessibility is disabled).
  `AssistantActivity` remains an `ACTION_ASSIST` entry point. Neither path records audio.

After installing the updated APK, open **Settings → Apps → Default apps → Digital assistant app**
and choose Jarvis. The app's **Set default assistant** button opens assistant settings rather
than requesting the assistant role through a dialog. If Jarvis is absent, verify the installed
APK contains `JarvisAssistantService` and its `android.voice_interaction` metadata; rebuilding
from an older branch will not include this registration. Pixel 9a / Android 17 validation is pending.

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
