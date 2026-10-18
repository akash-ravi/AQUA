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
