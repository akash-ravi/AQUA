# Jarvis backend

The brain of Jarvis. It runs on your Mac. The Android app connects to it over a WebSocket, sends what is on the
screen, and Jarvis asks Claude for the next action, checks it, and tells the phone to do it, one action at a time,
until the task is done with proof on screen. The message format the phone must speak is in [`../.agents/phone-protocol.md`](../.agents/phone-protocol.md).

## Setup (once)

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
cp .env.example .env          # then put your Anthropic key in .env (or run `ant auth login`)
```

`.env` also picks the model (`JARVIS_MODEL`, default `claude-opus-5-5`) and effort (`JARVIS_EFFORT`, default `low`).
Settings use a `JARVIS_` prefix because Claude Code exports its own `CLAUDE_*` variables.

## Run it with the real phone

```bash
.venv/bin/jarvis              # same as: .venv/bin/python -m src.app
.venv/bin/jarvis --debug      # also log each command, AI call (with tokens) and turn with its latency
```

Every task ends with a timing line (total, AI, phone, time waiting on you), also written to the task's notebook as a
`timing` event. Each `step_result` in the notebook carries the command's round-trip `ms`.

To see every run's journey and latency in the browser:

```bash
.venv/bin/python -m pip install -e '.[dashboard]'
.venv/bin/streamlit run dashboard.py   # http://localhost:8501, refreshes live while a task runs
```

Addresses to put in the phone app (get the Mac's Wi-Fi IP with `ipconfig getifaddr en0`):

```
Phone (Wi-Fi): ws://<mac-ip>:8000/agent
Phone (USB):   ws://127.0.0.1:8000/agent  after: adb reverse tcp:8000 tcp:8000
Check:         http://<mac-ip>:8000/health
```

- Same Wi-Fi: use the Wi-Fi address. Open the health link in the phone's browser first; it should say `ok`.
- Campus Wi-Fi often blocks phone-to-laptop traffic. Then plug in USB, run `adb reverse tcp:8000 tcp:8000`, and use the USB address.
- The phone app needs the setup in `.agents/phone-protocol.md` section 8 (internet permission, plain `ws://` allowed, and so on) or it can't connect.

**The DoorDash test**, in three passes on the real phone:
1. "Search DoorDash for pizza" — navigation only.
2. "Order a large pepperoni pizza from DoorDash" — answer **No** when it asks "Place order …?". Nothing is bought.
3. Same request, answer **Yes**. It counts as done only when the order confirmation shows on screen.

Every task's events are logged to `data/tasks/<task_id>.jsonl`, one JSON line per event.

## How it works

```
src/
  app.py             start the server (`jarvis`)
  config.py          settings from .env
  server/
    api.py           FastAPI: /health and the phone WebSocket at /agent, one task per connection
  phone/
    link.py          one phone conversation: send a command and wait for its result, ask the user, notice Stop
    messages.py      message models and validation
    wire.py          translation to and from the Android app's JSON
  agent/
    brain.py         the loop: look at the screen, ask Claude for one action, check it, send it, note what changed
    screen.py        turns the phone's screen into numbered lines Claude can read
    screenshots.py   screenshots when Claude asks or the text isn't enough, with numbered boxes drawn on them
    prompt.py        Claude's rules and the message it gets each step
    checks.py        is the action possible on this screen
    safety.py        anything that can't be undone (order, pay, send) needs your Yes first; secrets stay hidden
    task.py          what we know so far, and the notebook: data/tasks/<task_id>.jsonl, written before each command
  ai/
    model.py         the model call (strict tool use, one action per call) and its errors
    tools.py         the actions Claude can choose from
```

## Privacy note

Screen text and screenshots go to the Anthropic API; the DoorDash checkout screen shows your address and card. Screens
and screenshots are never saved to disk.

## Troubleshooting

- Search text appears but results never load: the phone answers `cantSubmit` when a field can't submit; Jarvis then
  uses a visible Search button or suggestion, or asks you to press Search.
- Phone can't connect: same Wi-Fi? health link works in the phone browser? Mac firewall allows port 8000? Try USB.
- "too many requests" / "out of credits": the Anthropic account is rate limited or out of credit. Wait, or top up.
