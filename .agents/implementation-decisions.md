# Initial implementation decisions

Recorded October 17, 2026, from the user's implementation choices.

## Backend and phone connection

Run the backend on the developer's Mac. For phone testing, listen on a LAN-accessible interface and connect the Android executor to `ws://<Mac-LAN-IP>:<port>/agent`. `localhost` is usable only from the Mac itself. Use the supplied Phone Executor Protocol as the initial wire contract: one task per socket, one command in flight, JSON text frames, and explicit results. Unauthenticated development access stays on the trusted LAN.

The Android executor is expected shortly. A minimal fake executor using the same contract can unblock development; it does not establish real-device compatibility.

## Model access

Use a free hosted model. NVIDIA's hosted API catalog is the first candidate to evaluate; the exact model is unselected. Verify account access, free quota, tree-based decisions, structured/tool responses, screenshot support, and latency before selecting a route. Keep the provider replaceable. Paid fallback is disabled; exhausted free access must produce an explicit error or use another verified free route.

## First end-to-end acceptance task

Open DoorDash, select and configure a pizza, complete checkout, and verify that the order was placed. Include account sign-in and payment setup when needed and supported. Use focused questions for missing restaurant, pizza, delivery, tip, or payment choices. Authentication challenges, OTPs, and protected payment interfaces may require user interaction; test those capabilities on the actual device rather than assuming they work.

Build incrementally toward this same acceptance task: navigation and selection, checkout preparation, then confirmed purchase and order verification. Do not call the full task successful merely because checkout was reached or the place-order button was clicked.

The supplied executor protocol requires a fresh `ask` before irreversible effects. Present the specific order and total, obtain a matching affirmative `answer`, and then issue the corresponding action with `confirmed: true`. A changed order or total requires renewed authorization. Preserve uncertain purchase outcomes for observation/reconciliation rather than blindly retrying.

## Data routing

The user permits task-relevant phone content, including screen text and screenshots, to be sent to the selected hosted model as needed. This resolves the handoff's initial personal-content routing question. Request images when the task needs them. This permission does not require retaining raw screens indefinitely or committing personal content to Git. Keep provider credentials out of model context and logs.

## Remaining setup dependencies

- An API key for an available free provider, supplied through local environment configuration.
- Exact model selection after capability probes with that account.
- Android connection details and an actual executor smoke test.
- Order-specific choices and account/payment interaction, requested during the live task as needed.

## Backend implementation decisions

Recorded October 17, 2026, while building `backend/`.

- **Python 3.12, FastAPI, plain async loop.** The handoff proposed LangGraph; a plain loop (`brain.py`) is easier to read and avoids relying on framework replay for exactly-once actions. Flat files with plain names replace the handoff's folder tree.
- **Phone protocol v1 defined in `PROTOCOL.md`.** The "supplied Phone Executor Protocol" was not found anywhere, so it was written from the rules above: one task per WebSocket at `/agent`, one command in flight, JSON text frames, explicit `ok/failed/rejected/unknown` results, `ask` + approved `answer` before anything irreversible, then `confirmed: true` plus `must_see` amounts the phone re-checks. Screenshots travel inline as base64 JPEG for this one-phone prototype.
- **Default AI: GLM-5.3-Flash on NVIDIA's free endpoint** (`z-ai/glm-5.3-flash`). The handoff's OpenRouter `:free` route had no providers on this date. Claude, Groq, Z.ai and local Ollama are one `.env` change away.
- **No paid fallback.** Paid routes (Claude, unknown hosts, OpenRouter without `:free`) refuse to start unless `ALLOW_PAID_MODELS=true`. A used-up quota stops the task with a message. Claude runs with `fallbacks: "default"`; a log line says when a fallback model answered.
- **Task notebooks instead of SQLite.** Each task is `backend/data/tasks/<id>.jsonl`, written (and synced to disk) before each command is sent. After a restart, unanswered commands are marked unknown. A phone can rejoin within 30 minutes; an irreversible action with an unknown result is never repeated automatically.
- **Safety:** irreversible actions need `confirm_and_tap`, a summary whose amounts are on screen including the total, and a fresh Yes; the backend re-reads the screen after the Yes. Finishing needs proof copied from the current screen, after the order step. Secrets (passwords, codes, card numbers) are never typed by the AI or stored.
- **No authentication on the LAN** (per the earlier decision), but connections with a browser `Origin` header are refused and rejoining needs a per-task key.
- **Open decision:** NVIDIA's free-trial terms forbid personal, financial and card data. Decide before the real checkout pass: accept for testing, use Groq/Ollama for it, or use Claude.
