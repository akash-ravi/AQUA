# Floating assistant overlay

Enable the Aqua accessibility service once. In the app, tap **Show assistant** (or use the
assistant gesture) to show the orb: a bare orb at the bottom of the screen, already listening.
Tap the orb to open the panel with the status, Pause/Stop and the text box; tap it again to
shrink back to the orb. The text box can start a task or answer the current question. Send
collapses it to the orb.

- **×** hides the panel and aura without disabling accessibility or stopping the task.
- **Pause** waits for the current command to finish; **Continue** appears after server
  acknowledgement and resumes the same task after reading a fresh screen.
- **Stop** ends the task. It does not restart automatically; type a new task to work again.
- Tap the running-task notification or **Show assistant** to bring a hidden panel back.
- **Set default assistant** opens Android's assistant chooser. Choose Aqua yourself;
  its assistant gesture invokes the text composer. Accessibility must also remain enabled.

The orb in the panel header and the screen-edge aura are both driven by assistant state and
live voice loudness: they flow while working, brighten while waiting or listening, slow and dim
when paused, and swell with your voice or Aqua's reply. The aura disappears when idle (unless
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
and choose Aqua. The app's **Set default assistant** button opens assistant settings rather
than requesting the assistant role through a dialog. If Aqua is absent, verify the installed
APK contains `JarvisAssistantService` and its `android.voice_interaction` metadata; rebuilding
from an older branch will not include this registration. Pixel 9a / Android 17 validation is pending.

Deploy the backend and Android changes together: the executor protocol now accepts
`{"type":"pause"}` and `{"type":"resume"}` and reports acknowledged state using
`{"type":"progress","state":"paused"|"running",...}`. Pause does not replay a
command that was already dispatched, and it cancels a pending question; resuming requires
fresh observation and any necessary new approval. A disconnected/terminated session cannot
be resumed by this version.

## Voice

Speech goes through [ElevenLabs](https://elevenlabs.io), streamed straight from the phone for the lowest
latency. The ElevenLabs key stays in `backend/.env`. The phone asks the Mac for a single-use token
(`POST /voice/listen` or `/voice/speak`, valid 15 minutes and good for one connection) and keeps one of each
fetched ahead, so a turn never waits on that round trip. If ElevenLabs can't speak (no key, no network), replies
fall back to the phone's own text-to-speech engine. The manifest declares the `TTS_SERVICE` query, without which
Android 11+ hides that engine. Without ElevenLabs there is no speech input; type instead.
Aqua says "Got it." after an answer and speaks questions and the final result. Step
statuses appear as text. The orb follows the loudness of the audio being played.

| Stage | Model | Why |
| --- | --- | --- |
| Speech to text | Scribe v2 Realtime (`scribe_v2_realtime`), English | ~150 ms; its VAD commits the transcript 1 s after you stop talking |
| Text to speech | Eleven v4 Turbo (`eleven_v4_turbo`), 24 kHz PCM, default voice | ~100 ms model time; audio plays as it streams in |

The text-to-speech WebSocket for the next reply opens while you are talking (and after each reply), so
speaking skips the connection handshake. An unused one closes after 180 s. Recording starts at once and audio
captured while the speech-to-text socket connects is sent as soon as the session opens, so nothing is lost.
Pick a different voice with `JARVIS_VOICE_ID` in `backend/.env`; default voices start fastest, professional clones slowest.

Showing the assistant starts listening; the text box stays as a fallback and the mic button
toggles listening. A heard utterance goes through the same `AssistantInput` routing as typed
text. While a question is spoken, the mic remains open if Android's acoustic echo cancellation
(AEC) is enabled for that recording. The first partial transcript interrupts playback, and the same
recording captures the full answer. This is duplex audio; the final text arrives when ElevenLabs commits it.

The microphone uses `VOICE_COMMUNICATION` with a session-bound `AcousticEchoCanceler`.
If AEC cannot be enabled, Aqua falls back to speaking the question before opening the mic;
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

- `voice/VoiceTokens.kt` fetches single-use tokens from the Mac and keeps one of each kind ready.
- `voice/Listener.kt` records 16 kHz mono PCM16 with `AudioRecord`, owns the session's AEC, and streams
  one utterance to Scribe v2 Realtime in 50 ms chunks.
- `voice/ElevenSpeaker.kt` streams Eleven v4 Turbo audio into a low-latency `AudioTrack` and keeps the next
  reply's connection open.
- `voice/SystemSpeaker.kt` wraps Android `TextToSpeech`, the fallback voice.
- `voice/VoiceTurn.kt` coordinates overlapping capture/playback, interruption, the no-AEC
  fallback, and cancellation independently of Android audio APIs.
- The accessibility service is bound with `BIND_INCLUDE_CAPABILITIES`, so it keeps
  microphone access without a foreground service. `RECORD_AUDIO` is still required.

## Guardrail protocol changes

Deploy the updated backend and Android client together. Initial approval is only for actions
that cost money; free sends/deletions/calls do not need blanket approval. Possible duplicate
effects still require result checks and explicit retry permission when uncertain. Payment
buttons, transaction context, and current quoted charges are rechecked after approval.

Click failures no longer trigger an immediate second gesture on the phone. A coordinate
fallback is a separate, counted retry after backend observation and any required approval.

Updated phones advertise `spokenSecretsV1` in `start.device.capabilities`. A secret-entry
question includes `kind: "secret"` and `secretTarget` with `package_name`, `screen_id`, and
`target`. The user can enter the value directly in the target app and reply "done", or speak
it during this question. Spoken answers send `source: "voice"` and `secret_ref`, with no
text. The backend uses `setText` with `secretRef`, a fresh `screenId`, and `index`.

The value stays in task-local Android memory, bound to the question/app/field, behind a
one-use reference that expires after two minutes. Pause, question replacement, Stop, and
disconnect invalidate pending references. Values are excluded from answer/command logs,
known secret fields are masked in screen reads, and screenshots are disabled while secret
content may be visible (for the rest of the task after supplying a secret). Numeric fields
accept individual spoken English digits; use direct manual entry if recognition is wrong.
Older phones retain direct manual entry; they cannot receive raw secrets from this backend.

Recognition still cannot establish whether unrelated background speech was intended for
Aqua. Wake words, speaker identification, and additional noise filtering remain deferred.

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
  Check that Aqua never interprets its own question as confirmation.
- Hide/Stop during speech and recognition, change the pending question, and answer by text:
  no cancelled answer should be submitted or microphone restarted by an old callback.
- Complete a call task: the overlay and microphone should stay dismissed during the call.

Dragging/docking, chat history, and process recovery are not implemented;
the panel can still cover controls near the bottom.
