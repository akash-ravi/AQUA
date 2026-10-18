# Floating assistant overlay

Enable the Jarvis accessibility service once. In the app, tap **Show assistant** (or use the
assistant gesture) to show the orb: a bare orb at the bottom of the screen, already listening.
Tap the orb to open the panel with the status, Pause/Stop and the text box; tap it again to
shrink back to the orb. The text box can start a task or answer the current question. Send
collapses it to the orb.

- **×** hides the panel and aura without disabling accessibility or stopping the task.
- **Pause** waits for the current command to finish; **Continue** appears after server
  acknowledgement and resumes the same task after reading a fresh screen.
- **Stop** ends the task. It does not restart automatically; type a new task to work again.
- Tap the running-task notification or **Show assistant** to bring a hidden panel back.
- **Set default assistant** opens Android's assistant chooser. Choose Jarvis yourself;
  its assistant gesture invokes the text composer. Accessibility must also remain enabled.

The orb in the panel header and the screen-edge aura are both driven by assistant state and
live voice loudness: they flow while working, brighten while waiting or listening, slow and dim
when paused, and swell with your voice or Jarvis's reply. The aura disappears when idle (unless
listening) or hidden.

Invocation follows Gemini's overlay. A thin line lights every edge at once. The soft glow then
blooms in from beyond the bezel (about 450 ms, emphasized decelerate). It is thin near the status
bar and pools at the bottom over a faint wash and a halftone dot field, and two light blobs
drift along the side edges. The orb rises and scales up into place, and tapping it grows it into
the panel. On dismissal the glow retracts back out through the top and sides first, with the
bottom last, and the orb shrinks and fades. Showing it again mid-exit reverses from where it is. Both windows are temporarily hidden before screenshot capture. The aura
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
window before opening the composer. The popup is the default: if accessibility is enabled but
not yet connected (a cold start), it waits up to 2 s for the service. Only when accessibility
is off, or never connects, does the app open with a pending overlay request that is retried
when you return from accessibility settings.
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
  The glow (edge, bottom pool, wash, halftone dots) and the inner line are rendered on a
  background thread into two alpha masks (rebuilt only when size or corner radii change) and
  tinted each frame with a rotating warm `SweepGradient`. Entry and exit scale the glow mask
  about the bottom edge. Screenshot capture hides the window without restarting the entry. Corner radii
  come from `Display.getRoundedCorner`, then window insets, then a 32dp fallback.
- `overlay/OrbView.kt` draws the Siri-style orb: three additive warm blobs and a light core.
  It animates only while visible, so a hidden or capture-hidden panel costs no frames.
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

## Voice

Listening is on-device and open source, run through [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx).
Replies use the phone's own text-to-speech engine (Google's on Pixel), which starts talking within
a few hundred milliseconds. If no system engine with an English voice is usable, replies are skipped
but still reported as finished. The manifest declares the `TTS_SERVICE` query, without which Android 11+ hides the engine.
Jarvis says "On it." as soon as a task starts and "Got it." after an answer, then speaks each
step status, questions and the final result. With the system engine, the orb pulses on each word.

| Stage | Model | Why |
| --- | --- | --- |
| End of speech | Silero VAD | Ends the turn 0.6 s after you stop talking |
| Speech to text | Moonshine v2 base, English, quantized | ~0.1 real-time factor; decoding starts at end of speech |

Showing the assistant starts listening; the text box stays as a fallback and the mic button
toggles listening. A heard utterance goes through the same `AssistantInput` routing as typed
text. While a question is spoken, the mic remains open if Android's acoustic echo cancellation
(AEC) is enabled for that recording. Silero's speech detection interrupts playback before
transcription finishes, and the same recording captures the full answer. This is duplex audio;
Moonshine still returns the final text at end of speech, not partial transcripts.

The microphone uses `VOICE_COMMUNICATION` with a session-bound `AcousticEchoCanceler`.
If AEC cannot be enabled, Jarvis falls back to speaking the question before opening the mic;
tapping the mic can still interrupt playback. AEC availability does not guarantee effective
echo rejection on every device/audio route. Speakerphone, wired/headset, and Bluetooth paths
need device testing. The app does not change global call mode or force a speaker/Bluetooth route.

Listening times out after 8 s without speech, counting from the end of playback for a spoken
question. Hiding the assistant, stopping a task, or answering by text cancels recording and
playback. Answers are scoped to the question being recorded; cancelled callbacks are discarded.
Input retains the existing task routing: it starts tasks when idle or answers a pending question,
and does not modify an executing task through arbitrary spoken instructions.

The final result is spoken after the panel settles. Successful task completion still dismisses
the overlay and leaves the mic off, including after placing a call. A failure that leaves the
panel visible can accept a new task while its explanation is being spoken.

- `voice/SpeechModels.kt` loads the VAD and recognizer on separate background threads when the
  service connects. Audio recorded before loading finishes is buffered, not dropped.
- `voice/Listener.kt` records 16 kHz mono PCM16 with `AudioRecord`, converts to model floats,
  owns the session's AEC, and transcribes one utterance.
- `voice/SystemSpeaker.kt` wraps Android `TextToSpeech`, preferring the best offline English voice.
- `voice/VoiceTurn.kt` coordinates overlapping capture/playback, interruption, the no-AEC
  fallback, and cancellation independently of Android audio APIs.
- The accessibility service is bound with `BIND_INCLUDE_CAPABILITIES`, so it keeps
  microphone access without a foreground service. `RECORD_AUDIO` is still required.

The models are not in git. The `speechModels` Gradle task downloads pinned, SHA-256-verified
archives from sherpa-onnx releases into `android-client/.speech-models/` (git-ignored) and
packages them as uncompressed assets, about 270 MB. The first build needs network. Only
`arm64-v8a` native libraries are packaged.

## Validation

`OverlayStateTest` covers task routing, busy input, and stale questions. Run:

```sh
./gradlew :app:testDebugUnitTest :app:assembleDebug
```

The full build requires this project's JDK 25 / SDK 37 toolchain. Device checks remain
for keyboard placement, app switching, Stop, service restart, rotation, and font scaling.
`VoiceTurnTest` covers interruption before transcription, uninterrupted recording across
playback completion, the no-AEC fallback, permission/recorder failures, cancellation, and
stale callbacks. These tests do not verify hardware echo cancellation. On a physical device:

- Let a long question finish without speaking: no self-answer, with the response timeout
  starting after playback rather than expiring during the question.
- Interrupt at the beginning, middle, and end of a question, including a short "yes" or "no":
  playback should stop and the answer's first word should survive transcription.
- Repeat at high playback volume and with background noise, then test connected headsets.
  Check that Jarvis never interprets its own question as confirmation.
- Hide/Stop during speech and recognition, change the pending question, and answer by text:
  no cancelled answer should be submitted or microphone restarted by an old callback.
- Complete a call task: the overlay and microphone should stay dismissed during the call.

Dragging/docking, chat history, and process recovery are not implemented;
the panel can still cover controls near the bottom.
