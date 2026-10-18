# Phone ↔ Jarvis protocol (version 1)

How the Android app (the **phone**) and the Jarvis backend on the Mac (the **backend**) talk.
The phone opens a WebSocket, reports what is on screen, and does what the backend asks, one command at a time.
The backend decides; the phone acts and reports honestly.

You can ship this in three parts (section 9). Part 1 is enough to run the first end-to-end test.

---

## 1. Connecting

| How | Address |
| --- | --- |
| Same Wi-Fi | `ws://<mac-ip>:8000/agent` |
| USB cable | run `adb reverse tcp:8000 tcp:8000` on the Mac, then `ws://127.0.0.1:8000/agent` |
| Is the server up? | open `http://<mac-ip>:8000/health` in the phone's browser → `ok` |

The backend prints both addresses when it starts. Make the address an editable setting in the app.

- Frames are UTF-8 JSON text, one object per frame. Every object has a `type`. Binary frames close the connection.
- Size limits: `result` up to 12 MB (screenshots), every other message up to 64 KB.
- **One task per connection.** The backend closes the socket (code 1000) right after `done`. Don't reconnect after a normal close.
- Don't send an `Origin` header. Browsers send one, native clients (OkHttp) don't; the backend refuses connections that have it.
- No password in version 1 (development on a trusted network, see DECISIONS.md).
- Keep the connection alive with OkHttp `pingInterval(15, SECONDS)`.

**Conventions**

- Keys are `snake_case`. Ids are strings. Pixels and milliseconds are whole numbers.
- Leave out fields that have no value. Don't send `null`. (Careful: Android's `org.json` `optString` returns the string `"null"` for JSON null.)
- Ignore fields and message types you don't recognize. The backend does the same.
- Booleans are `false` when left out, except `enabled`, which is `true` when left out.

---

## 2. Order of messages

```text
phone                                   backend
  hello  ──────────────────────────────►            within 10 s of connecting
  task  (or rejoin)  ──────────────────►            within 60 s
         ◄────────────────────────────── ready
         ◄────────────────────────────── command  read_screen
  result (with screen)  ───────────────►
         ◄────────────────────────────── progress / command / ask / ask_cancel
  result / answer / pause / resume / stop / update ──►
         ◄────────────────────────────── done     (then the backend closes)
```

- **At most one command is open at a time.** The backend sends the next command only after it has your `result` for the open one (or after it gave up waiting).
- Messages that arrive in the wrong stage get `error` with `fatal: false` and are ignored.

---

## 3. Phone → backend

### hello

```json
{
  "type": "hello",
  "protocol": 1,
  "device": {"id": "8f2d1c7e-5b0a-4c55-9a51-0c6a2b1d9e44", "name": "Pixel 9", "sdk_int": 36},
  "app_version": "0.1",
  "actions": ["read_screen", "tap", "type_text", "scroll", "go_back", "go_home", "open_app", "screenshot"],
  "apps": [
    {"label": "DoorDash", "package_name": "com.dd.doordash"},
    {"label": "Messages", "package_name": "com.google.android.apps.messaging"}
  ]
}
```

- `device.id`: a random UUID you create on first run and store. `sdk_int` is `Build.VERSION.SDK_INT`.
- `actions`: the commands this phone can run. The backend never sends anything else.
- `apps`: launcher apps from `queryIntentActivities(MAIN/LAUNCHER)`, up to 500. The AI names an app, the backend turns it into a `package_name`.

### task

```json
{"type": "task", "text": "Order a large pepperoni pizza from DoorDash"}
```

1 to 2000 characters. Typed or transcribed speech, it makes no difference to the backend.

### rejoin (part 3)

Continue the same task on a new connection after the old one dropped.

```json
{"type": "rejoin", "task_id": "t-5c1e…", "rejoin_key": "Qm9…", "last_result": {"command_id": "c-77a2…", "status": "ok"}}
```

- `task_id` and `rejoin_key` come from `ready`. Keep them (SharedPreferences) until `done`.
- `last_result`: the last command you executed and how it ended (finish one that is still running first). Leave it out if you executed none.
- Rejoin works for 30 minutes after the drop and only from the same `device.id`.

### result

```json
{
  "type": "result",
  "command_id": "c-3f9a…",
  "status": "ok",
  "settled": true,
  "settle_ms": 420,
  "acted_on": 41,
  "method": "action",
  "screen": {"screen_id": "s-82", "...": "see section 5"}
}
```

| `status` | Meaning |
| --- | --- |
| `ok` | Done. |
| `rejected` | Not tried; nothing happened (stale screen, bad target, paused …). |
| `failed` | Tried, but it didn't work. |
| `unknown` | It may or may not have happened (gesture cancelled, no callback). |

- When not `ok`, add `"error": {"code": "stale_target", "message": "…"}` (codes in section 7).
- **Every result carries a fresh `screen`**, read after acting (also for `rejected` and `failed`), unless the screen can't be read at all.
- `settled`: `true` if the screen went quiet before the cap (rule 8). `settle_ms`: how long you waited.
- `acted_on` and `method` (`action` or `gesture`): for `tap`, which node you actually clicked and how.
- `image`: only for `screenshot`, see rule 7.

### answer

```json
{"type": "answer", "ask_id": "q-19c0…", "text": "Large"}
{"type": "answer", "ask_id": "q-19c0…", "approved": true}
```

- For `kind: "question"` send `text` (a choice or what the user typed).
- For `kind: "confirm"` send `approved: true` (Yes) or `approved: false` (No, or the dialog was closed).
  If the user types something instead (for example "use the other card"), send it as `text`; the backend treats it as a correction.
- Answer only the open `ask_id`. Late answers are ignored.

### pause, resume, stop (stop in part 2, pause/resume in part 3)

```json
{"type": "pause"}
{"type": "resume"}
{"type": "stop"}
```

### update (part 3)

A correction while the task runs.

```json
{"type": "update", "text": "make it a medium instead"}
```

---

## 4. Backend → phone

### ready

```json
{"type": "ready", "task_id": "t-5c1e…", "rejoin_key": "Qm9…", "revision": 0, "rejoined": false}
```

### command

All fields sit flat on the message. `action` says which command it is.

```json
{"type": "command", "command_id": "c-3f9a…", "action": "tap", "revision": 0, "timeout_ms": 10000, "screen_id": "s-81", "target": 41}
```

| `action` | Extra fields |
| --- | --- |
| `read_screen` | — |
| `tap` | `screen_id`, `target`; after an approval also `confirmed: true`, `ask_id`, `must_see` |
| `type_text` | `screen_id`, `target`, `text`, `submit` |
| `scroll` | `direction` (`down`, `up`, `left`, `right`); optional `screen_id` + `target` |
| `go_back`, `go_home` | — |
| `open_app` | `package_name` |
| `screenshot` | `max_side` (default 1280) |

- `command_id` is unique forever (a UUID). The backend never sends the same `command_id` twice.
- `revision` goes up by one with every `update` the backend accepts (rule 10).
- `timeout_ms`: how long the backend waits for your result (it adds 5 s of grace). Defaults: 10 s, `open_app` 15 s, `screenshot` 8 s, `read_screen` 5 s.

### ask

```json
{"type": "ask", "ask_id": "q-19c0…", "kind": "question", "text": "Which size?", "choices": ["Small", "Medium", "Large"], "expires_in": 600}
{"type": "ask", "ask_id": "q-2a71…", "kind": "confirm", "text": "Place order at Tony's Pizzeria?\n1 × Large Pepperoni Pizza\nTotal: $31.20", "choices": ["Yes", "No"], "expires_in": 120}
```

- `question`: show the text, the choices as buttons, and a text box. "Please sign in on the phone, then reply done" is also a question: the user does it, then answers.
- `confirm`: the safety check before anything that can't be undone (paying, sending, deleting). Show the text exactly as sent, with Yes and No.
- The question must be answerable while another app (DoorDash) is in front. See rule 12.

### ask_cancel

```json
{"type": "ask_cancel", "ask_id": "q-2a71…", "reason": "expired"}
```

Close that question on screen. Reasons: `expired`, `replaced`, `stopped`, `paused`.

### progress

```json
{"type": "progress", "state": "running", "text": "Adding the pepperoni pizza", "step": 7, "revision": 0}
```

States: `running`, `thinking`, `waiting_for_user`, `waiting_for_approval`, `paused`. `text` is up to 120 characters; show it as "Jarvis: …".

### done

```json
{"type": "done", "task_id": "t-5c1e…", "outcome": "completed", "summary": "Ordered 1 Large Pepperoni Pizza from Tony's Pizzeria. Total $31.20.", "proof": ["Order placed!", "Order #4512"]}
```

| `outcome` | Meaning |
| --- | --- |
| `completed` | Done, with proof seen on screen. |
| `unconfirmed` | It may have happened, but there is no proof (for example the connection dropped right after "Place order"). |
| `failed` | Could not do it; `summary` says why. |
| `stopped` | The user pressed Stop. |
| `needs_user` | Waiting on the user (a question went unanswered). |

`last_action` (`{"label": "tap \"Place order\"", "status": "unknown"}`) is added when the last command matters for the outcome.

### error

```json
{"type": "error", "code": "unexpected_message", "message": "answer has no open question", "fatal": false}
```

Codes: `bad_message`, `unexpected_message`, `protocol_version`, `timeout`, `busy` (another task runs on this device; includes `task_id`), `task_not_found`, `task_finished`, `server_error`.
After `fatal: true` the backend closes the connection.

---

## 5. Screen

Sent inside every `result` (and the backend asks with `read_screen` at the start).

```json
{
  "screen_id": "s-81",
  "at": 1760749200123,
  "package_name": "com.dd.doordash",
  "title": "Tony's Pizzeria",
  "width": 1080,
  "height": 2400,
  "rotation": 0,
  "keyboard_top": 1450,
  "loading": false,
  "nodes": [
    {"index": 1, "text": "Back", "class_name": "android.widget.ImageButton", "bounds": [0, 96, 144, 240], "clickable": true},
    {"index": 4, "class_name": "androidx.recyclerview.widget.RecyclerView", "bounds": [0, 420, 1080, 2300], "scrollable": true, "scroll": ["down"]},
    {"index": 5, "parent": 4, "bounds": [0, 420, 1080, 700], "clickable": true},
    {"index": 6, "parent": 5, "text": "Pepperoni Pizza", "bounds": [48, 450, 700, 510]},
    {"index": 7, "parent": 5, "text": "$18.99", "bounds": [48, 520, 300, 570]},
    {"index": 8, "parent": 5, "text": "Add", "class_name": "android.widget.Button", "bounds": [860, 520, 1032, 600], "clickable": true}
  ]
}
```

**Screen fields**

| Field | Meaning |
| --- | --- |
| `screen_id` | Your id for this reading. Make a new one for every reading. |
| `at` | Capture time, ms since 1970. |
| `package_name` | App in front (`rootInActiveWindow.packageName`). |
| `title` | Window or pane title if there is one (`AccessibilityWindowInfo.title`). |
| `width`, `height`, `rotation` | Full display in the current rotation, same space as `getBoundsInScreen`. |
| `keyboard_top` | Top edge of the keyboard window; leave out when the keyboard is hidden. |
| `loading` | `true` if a progress spinner is visible. |
| `truncated` | `true` if you hit the 1500-node cap. |
| `note` | `"no_window"` if `rootInActiveWindow` stayed null (send `nodes: []`). |

**Node fields**

| Field | From | Notes |
| --- | --- | --- |
| `index` | your numbering | Unique within this screen. Number depth-first, parents before children. |
| `parent` | your numbering | Index of the nearest **kept** ancestor. Leave out at the top level. |
| `text` | `text` | Leave out when `isShowingHintText` is true (the hint is not real text) and for password fields. Cut at 500 characters. |
| `description` | `contentDescription` | |
| `hint` | `hintText` | Placeholder of an empty field. |
| `resource_id` | `viewIdResourceName` | |
| `class_name` | `className` | |
| `bounds` | `getBoundsInScreen` | `[left, top, right, bottom]` in pixels. |
| `clickable`, `long_clickable`, `editable`, `scrollable`, `checkable`, `checked`, `selected`, `focused`, `heading`, `password` | same-named getters | Leave out when false. |
| `enabled` | `isEnabled` | Send only `"enabled": false`. |
| `scroll` | action list | Directions the node can move right now: `ACTION_SCROLL_FORWARD` → `"down"` (or `"right"` for horizontal lists), `ACTION_SCROLL_BACKWARD` → `"up"` (or `"left"`), plus the directional scroll actions if offered. |
| `state` | `stateDescription` | For example "On", "Selected", "2 of 5". |
| `error` | `error` | Field error text, for example "Choose a size". |

**Which nodes to keep.** Walk the tree of the active window. Skip nodes that are not `isVisibleToUser` or have empty bounds (keep walking their children). Keep a node if it

- has text, a description, a hint or a state description, or
- is clickable, long-clickable, editable, scrollable or checkable, or
- has at least two children that contain kept nodes (a group, such as a menu row). Send it with no text.

The group rule is what keeps "Add" tied to "Pepperoni Pizza". Never include the Jarvis app's own windows or overlay. Cap: 1500 nodes (`truncated: true`).

---

## 6. Phone rules

1. **One worker.** Run commands one at a time, in arrival order, on one background thread. OkHttp calls `onMessage` on its reader thread; hand the work off. Don't settle on the reader thread.

2. **Stale check, before every command that has a `target`** (`tap`, `type_text`, `scroll` with a target). When you send a screen, remember for that `screen_id`: the active window id (`rootInActiveWindow.windowId`) and, per index, the `AccessibilityNodeInfo` plus a fingerprint (class, resource id, text, description, bounds, and the text of its group). Before acting:
   - `screen_id` is not the latest screen you sent → `rejected` / `stale_screen`
   - the active window id changed (a dialog, a sheet, another activity or app came to the front; the keyboard opening does **not** count) → `rejected` / `stale_screen`
   - the index isn't in that screen → `rejected` / `bad_target`
   - `node.refresh()` is false, the node is no longer visible, or the fingerprint differs (bounds moved more than 8 px) → `rejected` / `stale_target`

   Never look a target up again by its number in a newer tree. Every rejection carries a fresh screen.

3. **tap.** `ACTION_CLICK` on the target if it is clickable, otherwise on its nearest clickable ancestor. If `performAction` returns false: a 50 ms gesture tap at the center of the refreshed bounds, only if that point is on screen and above `keyboard_top`. No gesture fallback for `confirmed: true` commands. A gesture that is cancelled or not answered within 2 s → `unknown`. Report `acted_on` and `method`.

4. **type_text.** `set_text` replaces the field's whole content; it does not type key by key.
   Refresh the target. Not editable → `rejected` / `not_editable`. Disabled → `rejected` / `disabled`. Not focused → `ACTION_FOCUS` (or `ACTION_CLICK` if that fails). Then `ACTION_SET_TEXT` with the full text.
   If `submit` is true: refresh, then `ACTION_IME_ENTER` only if it is in `node.actionList`; otherwise `failed` / `cant_submit` ("text entered; Enter not available here").

5. **scroll.** `down` means *show what is further down* (the finger moves up). With a target: use `ACTION_SCROLL_DOWN/UP/LEFT/RIGHT` if offered, else `down`/`right` → `ACTION_SCROLL_FORWARD` and `up`/`left` → `ACTION_SCROLL_BACKWARD`. The node offers scrolling but not that way → `rejected` / `cant_scroll`. It offers none → swipe inside its bounds (75% → 25% over about 400 ms). Without a target: the largest visible scrollable node, or a swipe in the middle of the screen.

6. **open_app.** `getLaunchIntentForPackage(package_name)` plus `FLAG_ACTIVITY_NEW_TASK`, then `startActivity` from the accessibility service. Null intent → `rejected` / `app_not_found`. An app that is already running opens where it was left.

7. **screenshot.** `takeScreenshot` → copy the hardware bitmap to ARGB_8888 → scale so the long side ≤ `max_side` → JPEG quality 75 → `Base64.NO_WRAP` → close the `HardwareBuffer`. Typically 60–200 KB.
   Result: `"image": {"format": "jpeg", "data": "<base64>", "width": 720, "height": 1600}` plus a `screen` read right after the capture; the image belongs to that `screen_id`.
   Error 3 (too soon after the last one): wait 1 s, try once more. Other errors → `failed` / `screenshot_failed` with the Android code in `message`. If 98% or more of a small pixel sample is black (protected screens such as card entry come back black) → `failed` / `screenshot_blocked`.

8. **Settle, after every action.** Wait until the app in front has been quiet for 300 ms. Count only `TYPE_WINDOW_STATE_CHANGED`, `TYPE_WINDOWS_CHANGED`, `TYPE_WINDOW_CONTENT_CHANGED`, `TYPE_VIEW_SCROLLED` and `TYPE_VIEW_TEXT_CHANGED` from that app's package; ignore `com.android.systemui` and the Jarvis app. Cap: `min(5000, timeout_ms − 1500)` ms. Report `settled` and `settle_ms`. `read_screen` also settles (cap 2 s). Animated screens may never settle; `settled: false` is fine.

9. **Confirmed commands** (`confirmed: true`, part 2). The backend only sends these right after the user approved the matching `ask`. Check:
   - `ask_id` is a confirm the user answered Yes to on this connection, and it hasn't been used yet → otherwise `rejected` / `not_approved`
   - every string in `must_see` (for example `"$31.20"`) appears, ignoring case, in some node's text or description in the active window → otherwise `rejected` / `must_see_missing` (the total changed; the backend will ask again)
   - then tap with `ACTION_CLICK` only (no gesture fallback).

10. **Pause, Stop and corrections** (part 2 and 3).
    - After the user presses **Stop**: send `stop`, then reject every command with `rejected` / `stopped`. A command that is already running finishes and its result is sent. Wait up to 3 s for `done`, then close.
    - After **Pause**: send `pause`, reject every command with `rejected` / `paused` until the user resumes; then send `resume`.
    - When you send an **update**, remember `min_revision = last revision you saw + 1`, and reject commands with a lower `revision` as `rejected` / `old_revision`. `ready`, `progress` and `command` all carry the current revision.

11. **Never run a command twice.** Remember the last 200 `command_id`s with their results. If one arrives again, send the stored result without acting. (Memory is enough for now; after an app restart the backend treats the outcome as unknown.)

12. **Talking to the user while another app is in front.** Keep the WebSocket inside the accessibility service (it stays alive while DoorDash is in front; the activity doesn't). Show Pause/Stop and confirm Yes/No in a small `TYPE_ACCESSIBILITY_OVERLAY` window with `FLAG_NOT_FOCUSABLE`, parked in a corner, so it never becomes the active window. For questions with free text, bring the Jarvis activity forward, then `moveTaskToBack(true)` after the answer. Leave the overlay out of screens. After an answer, the backend always reads the screen again before acting.

13. **Screen off or locked.** Before acting, check `PowerManager.isInteractive` and `KeyguardManager.isKeyguardLocked` → `rejected` / `screen_locked`. Keep the screen on during a task (`FLAG_KEEP_SCREEN_ON` on the overlay).

14. **Reconnecting** (part 3). If the connection drops before `done` and the user didn't press Stop: reconnect with backoff (1, 2, 4 … 30 s) for up to 5 minutes, then send `hello` and `rejoin`. Show "Reconnecting…" meanwhile.

---

## 7. Error codes

| Status | Code | When |
| --- | --- | --- |
| `rejected` | `stale_screen` | Not the latest screen, or a different window is in front. |
| `rejected` | `stale_target` | The node is gone or changed. |
| `rejected` | `bad_target` | No such index in that screen. |
| `rejected` | `not_editable` | `type_text` on a node that isn't a text field. |
| `rejected` | `disabled` | The node is disabled. |
| `rejected` | `cant_scroll` | The list can't move that way. |
| `rejected` | `app_not_found` | No launch intent for that package. |
| `rejected` | `not_approved` | `confirmed: true` without a matching Yes. |
| `rejected` | `must_see_missing` | An approved amount is no longer on screen. |
| `rejected` | `paused` / `stopped` / `old_revision` | Rule 10. |
| `rejected` | `screen_locked` | Rule 13. |
| `rejected` | `unsupported` | Action not supported by this phone. |
| `failed` | `cant_submit` | Text entered, but Enter isn't available. |
| `failed` | `screenshot_failed` / `screenshot_blocked` | Rule 7. |
| `failed` | `internal` | Anything else; put details in `message`. |
| `unknown` | `gesture_unconfirmed` | A gesture was sent but Android never confirmed it. |

---

## 8. Android setup checklist

On the `android` branch today the phone can't reach the backend yet. Needed:

1. `AndroidManifest.xml`: `<uses-permission android:name="android.permission.INTERNET"/>`.
2. Allow unencrypted `ws://` for development: `android:usesCleartextTraffic="true"` on `<application>` (or a debug-only network security config). With targetSdk 37, OkHttp otherwise fails with "CLEARTEXT communication … not permitted".
3. Android 17 phones: `<uses-permission android:name="android.permission.ACCESS_LOCAL_NETWORK"/>` and ask for it at runtime before the first connect. Without it the connection just times out. On a connect timeout show "Can't reach Jarvis: same Wi-Fi? Local network permission?".
4. Package visibility for the app list and `open_app`: `<queries><intent><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent></queries>`.
5. `res/xml/accessibility_service_config.xml`: `android:accessibilityFlags="flagDefault|flagReportViewIds|flagRetrieveInteractiveWindows"` (needed to see the keyboard and dialog windows) and `android:isAccessibilityTool="true"` (on Android 14+ some pay/confirm buttons are hidden from services without it; fine for a personal build). Keep `canRetrieveWindowContent`, `canPerformGestures` and `canTakeScreenshot`. Add `flagIncludeNotImportantViews` only if menu rows come out flat.
6. WebSocket client: OkHttp 4.12 with `org.json` is enough.
7. If the phone can't reach the Mac over Wi-Fi (campus networks often block device-to-device traffic), use the USB route in section 1, or a phone hotspot. Allow port 8000 in the Mac firewall.

---

## 9. Shipping in parts

| Part | Phone sends | Phone handles | Enough for |
| --- | --- | --- | --- |
| 1 | `hello`, `task`, `result` | `ready`, `command` (`read_screen`, `tap`, `type_text`, `scroll`, `go_back`, `go_home`, `open_app`), `progress`, `done`, `error`; rules 1–6, 8, 11 | First end-to-end test, any task with no purchase |
| 2 | `answer`, `stop` | `ask`, `ask_cancel`, confirmed taps; rules 9, 10 (Stop), 12, 13 | The DoorDash order with approval |
| 3 | `pause`, `resume`, `update`, `rejoin` | `screenshot`; rules 7, 10 (pause and revisions), 14 | Corrections, reconnecting, pictures |

The backend reads `hello.actions`, so it only uses what the phone says it supports.

## 10. What the backend promises

- One command open at a time; a `command_id` is never reused.
- After a lost result it reads the screen instead of re-sending, and it never repeats an action that can't be undone on its own.
- `confirmed: true` only right after the user's Yes to that exact `ask_id` (within 2 minutes) and only for the button and total that were shown.
- After `stop` it sends no new commands; it waits for the running one, then sends `done`.
- Screen text is treated as information, never as instructions from the user.

---

## 11. Showing it in the app

Everything the app shows during a task comes from the messages above. The app never decides anything; it shows what
the backend sends, word for word, and passes the user's taps back.

| What the backend sends | What the app shows | What the user can do |
| --- | --- | --- |
| `ready` | A small floating panel (rule 12) over the app in front: "Jarvis is working" | Pause, Stop |
| `progress` | The panel's status line: `Jarvis: <text>` (max 120 chars) and "step N". `state` picks the look: `running` / `thinking` (spinner), `waiting_for_user` / `waiting_for_approval` (attention), `paused` (paused look) | Pause / Resume, Stop |
| `ask`, `kind: "question"` | A question card: the text, one button per choice, and a text box. Keep it answerable while DoorDash is in front. Optional countdown from `expires_in` | Tap a choice or type, then send `answer` with `text` |
| `ask`, `kind: "confirm"` | A clear confirm dialog with the text **exactly as sent** (several lines: summary, button name, total) and two big buttons, Yes and No. Vibrate. Never answer it automatically | Yes → `approved: true`. No, back, or closing it → `approved: false`. Typing a change instead (e.g. "use the other card") → send it as `text` |
| `ask_cancel` | Close that question or dialog | — |
| `done` | A result card. `outcome` picks the badge: `completed` green "Done", `unconfirmed` amber "Check before trying again", `failed` red "Couldn't finish", `stopped` grey "Stopped", `needs_user` blue "Needs you". Below it: `summary`, the `proof` lines ("Seen on screen: Order placed!"), and when `last_action` is present a warning line: `Last action: tap "Place order" (unknown)` | New task |
| `error` with `fatal: true` | The `message` as an error card, then the connection ends. | — |
| `error` with `fatal: false` | Nothing for the user (log it) | — |
| connection dropped before `done` | "Reconnecting…" banner while you retry `rejoin` (rule 14) | Stop |

Buttons and what they send: **Pause** → `pause`, **Resume** → `resume`, **Stop** → `stop` (then rule 10),
a typed correction while running → `update` with the text.

## 12. HTTP endpoints for the app

Same address and port as the WebSocket. Only one for now:

| Request | Gives |
| --- | --- |
| `GET /health` | `ok` (plain text) — the connection test button |

History, task details and an HTTP stop button were removed until the app needs them; a task's full record is in
`backend/data/tasks/<task_id>.jsonl` (read it with `python -m jarvis.dev.trace`).
