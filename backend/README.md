# Jarvis backend

The brain of Jarvis. It runs on your Mac. The Android app connects to it over a WebSocket, sends what is on the
screen, and Jarvis asks Claude for the next action, checks it, and tells the phone to do it, one action at a time,
until the task is done with proof on screen. The message format the phone must speak is in [`../PROTOCOL.md`](../PROTOCOL.md).

## Setup (once)

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
cp .env.example .env          # then put your Anthropic key in .env (or run `ant auth login`)
```

`.env` also picks the model (`JARVIS_MODEL`, default `claude-opus-5-5`) and effort (`JARVIS_EFFORT`, default `medium`).
Settings use a `JARVIS_` prefix because Claude Code exports its own `CLAUDE_*` variables.

Check Claude answers before connecting the phone (three small paid calls):

```bash
.venv/bin/python -m jarvis.dev.check_ai
```

## Run it with the real phone

```bash
.venv/bin/python -m jarvis.server
```

It prints the addresses to put in the phone app:

```
Phone (Wi-Fi): ws://192.168.1.23:8000/agent
Phone (USB):   ws://127.0.0.1:8000/agent  after: adb reverse tcp:8000 tcp:8000
Check:         http://192.168.1.23:8000/health
```

- Same Wi-Fi: use the Wi-Fi address. Open the health link in the phone's browser first; it should say `ok`.
- Campus Wi-Fi often blocks phone-to-laptop traffic. Then plug in USB, run `adb reverse tcp:8000 tcp:8000`, and use the USB address.
- The phone app needs the setup in PROTOCOL.md section 8 (internet permission, plain `ws://` allowed, and so on) or it can't connect.

**First test with the real phone (no AI involved):** `.venv/bin/python -m jarvis.server --smoke`. Send any task from
the phone; the server reads the screen, goes home, opens DoorDash, goes back, and prints each screen as the AI would see
it. If that works, the phone side speaks the protocol correctly.

**The DoorDash test**, in three passes on the real phone:
1. "Search DoorDash for pizza" — navigation only.
2. "Order a large pepperoni pizza from DoorDash" — answer **No** when it asks "Place order …?". Nothing is bought.
3. Same request, answer **Yes**. It counts as done only when the order confirmation shows on screen.

Read what happened in any task: `.venv/bin/python -m jarvis.dev.trace` (last task) or `... trace <task_id>`.

## How it works

- `server.py` — accepts the phone at `/agent`, one connection per task.
- `phone.py` — one phone conversation: send a command and wait for its result, ask the user something, notice Stop.
- `brain.py` — the loop: look at the screen, ask the AI for one action, check it, send it, note what changed.
- `screen.py` — turns the phone's screen into numbered lines the AI can read.
- `prompt.py` — the AI's rules and the message it gets each step.
- `checks.py`, `safety.py` — is the action possible; anything that can't be undone (order, pay, send) needs your Yes first.
- `task.py` — what we know so far, and the notebook: `data/tasks/<task_id>.jsonl`, one line per event, written before
  each command is sent.
- `pictures.py` — screenshots only when the AI asks or the screen text isn't enough, with numbered boxes drawn on them.
- `claude.py`, `tools.py` — the Claude call and the actions it can choose from (strict tool use, one action per call).

## Privacy note

Screen text and screenshots go to the Anthropic API; the DoorDash checkout screen shows your address and card. Screens
and screenshots are never saved to disk.

## Troubleshooting

- Search text appears but results never load: the Android `setText` handler must honor `submit=true` by invoking the field's IME action. Older builds silently ignore it. The backend now checks submission acknowledgements and tells the AI to use a visible Search/Go button or matching suggestion; if none is exposed, it asks you to press Search. The Android change is in [`patches/android-search-submit.patch`](patches/android-search-submit.patch): apply it from the root of the Android checkout with `git apply /path/to/JARVIS/backend/patches/android-search-submit.patch`, rebuild, and install the updated app. A successful submit returns `data: {"submitted": true}`; failure after typing returns `cantSubmit`, so the AI does not retype the query.
- Phone can't connect: same Wi-Fi? health link works in the phone browser? Mac firewall allows port 8000? Try USB.
- "too many requests" / "out of credits": the Anthropic account is rate limited or out of credit. Wait, or top up.
