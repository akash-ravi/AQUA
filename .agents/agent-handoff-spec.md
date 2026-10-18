# Coding-Agent Handoff: Personal Android Assistant

**Version:** 1.0 — October 17, 2026  
**Status:** Implementation handoff with explicit working defaults and staged questions  
**Primary scope:** Brain, agent engineering, backend, and integration with a separately developed Android harness

## 1. Instructions to the implementing coding agent

Build a personal assistant that accepts a natural-language goal and operates across Android applications through an existing accessibility-based phone harness. Implement an accessibility-first observe–decide–execute–verify loop, with visual input requested when needed.

This document consolidates the project discussion and supersedes the earlier build plan where they conflict. In particular:

- The supplied harness already describes screen reading and action primitives. Integrate with those capabilities early when their implementation is available.
- Screenshots are an on-demand source of missing information; screenshot input is not required for every decision.
- A large simulated phone is not a prerequisite. A small fake adapter and recorded fixtures are useful for independent development and failure tests.
- The user owns the brain and agent engineering. Their friend owns Android integration and phone control.
- Model selection, runtime placement, and integration details must remain replaceable.

Before changing an existing repository, inspect its instructions, architecture, and tests. Adapt the proposed structure to the actual code. Confirm the harness capabilities against code or a running device; the supplied inventory is a description, not execution evidence.

Proceed through the milestones in Section 12. Use the documented working defaults for reversible choices. Ask the questions in Section 13 when their decision point is reached, and continue independent work where possible. Do not treat every open question as a reason to stop implementation.

Do not invent credentials, claim unrun checks passed, silently route to a paid model, or describe model-dependent prototype behavior as general phone reliability.

## 2. Product vision

The assistant should accept direct commands and delegated goals:

- “Open WhatsApp.”
- “Find the address Maya sent me.”
- “Find that address, check the route, and prepare a message with my arrival time.”
- Eventually: visually dependent requests such as choosing a photo or comparing products by appearance.

It should understand the request, carry relevant information between applications, execute supported actions, verify the result, and recover or ask a useful question when blocked. The long-term ambition is broad capability across applications; actual coverage depends on OS permissions, app accessibility, available integrations, and model performance.

The brain consumes text instructions. Typed input and transcribed voice feed the same task interface. Speech recognition, wake words, and spoken output are separate, replaceable capabilities.

## 3. Decision status

### 3.1 User-established direction

| Decision | Meaning |
| --- | --- |
| Personal Android assistant | Initial use is personal, with broad phone capability as the ambition |
| Brain and phone work are split | User builds the agent; friend builds the Android harness |
| Input-independent brain | Develop with text; voice can be added around the same core |
| Replaceable intelligence | Preserve the ability to change models and providers |
| Deployment remains open | Hosted, on-device, and hybrid execution are future options |
| Free-access development | Prioritize models that can be tested without paid API usage; distinguish free quotas from unlimited local execution |
| GLM-5.3-Flash is a starting candidate | Validate the exact hosted route before depending on its capabilities |

### 3.2 Proposed defaults carried into this handoff

These are implementation recommendations from the discussion, not individually confirmed product requirements. Keep them configurable and record changes.

| Default | Reason |
| --- | --- |
| Accessibility-first perception | Harness exposes useful text, controls, bounds, and actions directly |
| Task-driven screenshot fallback | Visual information may be missing even when the tree is rich |
| One agent runtime initially | Establish a baseline before adding routing or specialist agents |
| Backend contains the agent runtime | Keeps orchestration and task state together while Android integration develops separately |
| One active task and one UI action in flight per device | Avoid competing UI control and simplify interruption and reconciliation |
| Phone initiates a persistent authenticated WebSocket | Supports commands and unsolicited device events over one bidirectional connection |
| JSON control messages; HTTPS for image assets | Keeps the control channel small and separates asset transfer |
| Python, FastAPI, Pydantic, SQLite; LangGraph for orchestration | Practical initial stack for a stateful, inspectable prototype |
| Prepare consequential changes and request scoped authorization before committing unless already authorized | Conservative starting behavior while the user's autonomy policy is being defined |

## 4. Assumptions and rationale

| ID | Assumption | Why it was made | How to validate / response if false |
| --- | --- | --- | --- |
| A1 | The supplied harness inventory reflects implemented or near-ready functionality | User supplied concrete primitive descriptions | Inspect actual code and run capability checks; stub only missing integration pieces |
| A2 | Many initial tasks expose enough accessibility information | Screen reading returns text/descriptions and interactive nodes | Test representative apps; measure missing information instead of asserting a percentage of coverage |
| A3 | Screen indices are temporary | The parser assigns indices during tree traversal; UI content can change | Bind targets to observations and invalidate stale mappings; confirm existing harness behavior |
| A4 | The brain can run on a development backend initially | User's work and phone integration are separate; deployment choice is deferred | Confirm the first reachable backend location; keep transport separate from agent logic |
| A5 | GLM-5.3-Flash's free hosted route may be suitable | It is listed with multimodal capabilities and a free route | Probe current account access, images, tool calls, output validation, quotas, and latency |
| A6 | Free hosted access will sometimes be unavailable or rate-limited | Free endpoints have quotas and provider-dependent availability | Handle errors explicitly; use verified free alternatives or local development mode |
| A7 | Early testing can use one foreground phone task at a time | Shared screen control can invalidate decisions | Validate user-interruption behavior; pause or cancel on takeover according to the agreed policy |
| A8 | Quiet UI events are a settling hint | Harness describes a roughly 300 ms idle window and 5 s timeout | Report idle/timeout separately; verify semantic outcomes from subsequent observations |
| A9 | Personal preferences can wait; task memory cannot | Cross-app tasks require retaining facts during the current run | Build working memory and checkpoints first; add durable preferences after agreeing retention and editing behavior |
| A10 | Existing development hardware can host only small local fallbacks | Inspected development Mac is an M1 MacBook Air with 8 GB RAM | Verify available memory and measured performance; do not attempt server-scale GLM models locally |
| A11 | This is a personal prototype, not a Play Store launch | User identified the personal-assistant direction | Revisit distribution before public release; personal installation does not remove OS security restrictions |

Portability means separating responsibilities and data, not guaranteeing identical quality, latency, or implementation effort across a hosted model and a phone-sized model. On-device deployment will require its own runtime and resource validation.

## 5. Existing Android harness inventory

Transcribed from the user-provided primitive table. Verify these behaviors before relying on them.

| Primitive | Described behavior |
| --- | --- |
| `readScreen(): ScreenState` | Walk accessibility tree; retain nodes with text, description, or clickable/editable/scrollable behavior; assign index and bounds; return foreground package |
| `click(index)` | Try `ACTION_CLICK` on the node or a clickable ancestor; fall back to a gesture tap at the center of bounds |
| `setText(index, text)` | Use `ACTION_SET_TEXT` |
| `scroll(index, up/down)` | Use accessibility scroll action, with swipe fallback |
| `back()`, `home()` | Use global accessibility actions |
| `openApp(name)` | Match launcher app label and launch using a package launch intent |
| `awaitIdle()` | Wait for window-change events to stop for approximately 300 ms, with approximately 5 s timeout |
| `screenshot()` | Existing capability, currently described as used when the tree is sparse |

### 5.1 Integration requirements to inspect or add

- An observation ID and device-maintained UI generation/revision suitable for checking target freshness.
- Active package/window context, capture time, display geometry, and keyboard/dialog context where available.
- Node properties: enabled, selected, checked/checkable, focused, visible/editable/scrollable, supported actions, and useful labels/descriptions/resource IDs where exposed.
- Enough parent/group relationships to associate repeated buttons with the correct content. Flattening must not erase essential context.
- Available-app discovery or a validated app catalogue so the model can disambiguate app names.
- Explicit execution results and errors; stale target, unsupported action, timeout, and unknown outcome must be distinguishable.
- Action completion followed by a fresh observation. Bundle execution, local settling, and screen read into one backend interaction where practical.
- Local stop/pause behavior, capability advertisement, connection health, and command deduplication.

Do not assume that all fields or actions are available. The harness advertises capabilities; the brain exposes only supported tools to the model.

## 6. Perception and screenshot policy

### 6.1 Default behavior

Start with the current accessibility observation. Use its labels, relationships, states, and supported actions to reason and select targets. A tree is a structured view of exposed UI semantics, not a complete representation of every visible pixel or every item in the app.

Request a screenshot when the current task needs information the observation does not contain, or when verification remains visually ambiguous. Low node count is one signal, not the complete routing rule.

| Task family | Tree-first path | Reason to request visual information |
| --- | --- | --- |
| Navigation, forms, settings | Labelled controls, fields, and states | Missing or ambiguous controls/state |
| Messages and cross-app information transfer | Exposed message text and labels | Relevant information is in a picture or attachment |
| Photo selection | Metadata, dates, labels | User identifies the photo by its contents |
| Shopping | Names, prices, accessible options | Appearance comparison or unlabelled variants |
| Maps and charts | Addresses and exposed data | Visual positions, shapes, or trends absent from tree |
| Receipts / scanned documents | Accessible extracted text if present | Content exists only as pixels |
| Custom controls / canvas / some web content | Whatever semantics the app exposes | Missing regions or a single node covering multiple controls |

Framework names alone do not determine accessibility quality. Test the actual app and screen.

### 6.2 Grounding and execution

Prefer observation-bound node IDs or numbered marks. If visual reasoning identifies an area absent from the tree, `click(index)` alone cannot target it. A visual fallback needs both:

1. A grounding mechanism that produces an observed target region with a defined coordinate transform.
2. An executor capability that can act on that region.

Working default: represent grounded visual regions as temporary synthetic target IDs tied to the visual observation. Choose the grounding implementation at the visual milestone. Do not fabricate accessibility indices or assume screenshot capture provides action grounding.

If coordinate execution is added, validate bounds, rotation, scaling, active window, and observation freshness locally. Keep coordinate conversion outside the planner. Missing grounding should produce a clear unsupported/needs-user result.

### 6.3 Capture consistency and failure

Associate screenshots with capture time, display size/rotation, and UI revision. Tree and screenshot capture are not inherently atomic; detect or flag intervening UI changes rather than treating mismatched captures as one coherent observation.

Protected windows or capture failures return explicit results. Do not attempt to bypass platform protections. When an original document/image is available through an authorized file integration, source content may be preferable to a screenshot; that integration is optional scope.

## 7. Architecture and ownership

```text
User: typed request or transcribed speech
                 |
         Android interaction UI
                 |
     Authenticated bidirectional connection
                 |
Backend: task/session service + agent runtime + persistence
          |                               |
    Model adapter                    Phone adapter
          |                               |
 Hosted or local model          Android harness primitives
                                          |
                                  Fresh observations
```

The backend is the service boundary. The agent runtime is a component inside it. Model reasoning, tool dispatch, device control, and user communication are separate responsibilities.

| Responsibility | Primary owner |
| --- | --- |
| Goal interpretation, task state, model calls, action choice | Brain developer / coding agent |
| Verification, recovery, clarification, user-facing summaries | Brain developer / coding agent |
| Backend API, connection/session handling, persistence, traces | Brain developer, coordinated with Android developer |
| Accessibility parsing, gesture/action execution, screenshots | Android developer / friend |
| Local freshness checks, stop controls, execution ledger | Android developer, with jointly agreed semantics |
| Wire contract, reconnect behavior, approvals, integration tests | Shared |

Use a small fake phone adapter for independent tests if transport is not yet ready. Use actual harness integration early when available. Avoid rebuilding the Android layer in the backend.

## 8. Communication design

### 8.1 User and assistant

Working default: a chat UI with one active task, text entry, concise progress, focused questions, and visible Pause/Stop controls. Transcribed voice submits the same request/update messages. A development console can exercise the same backend interface before phone UI is complete.

Distinguish:

- New task request.
- Follow-up correction to the active task.
- Answer to a particular question.
- Authorization for a particular proposed consequential action.
- Pause, resume, and cancellation.

Represent questions with question IDs and task revisions. Interpret a reply in that context. A correction invalidates queued decisions based on the previous request. On resume, observe the phone again and reevaluate pending actions.

### 8.2 Phone and backend

Proposed default: a phone-initiated WebSocket carrying versioned JSON control messages. Use WSS and authenticated device pairing for networked operation; document the local development connection setup separately. Bind each connection to the authenticated device, rather than trusting a client-supplied device ID alone.

Use HTTPS for setup/history operations and screenshot uploads where useful. Send screenshot references over the control channel; the model adapter handles the provider's actual image format. A single-device prototype may use bounded binary WebSocket frames behind the asset adapter if that materially simplifies setup, but record the choice.

Send full observations initially: at task start, after actions, on explicit observation requests, and after reconnect. During active work, send meaningful window/state-change notifications that can invalidate decisions. Avoid streaming every raw accessibility event or invoking the model for every event. Add deltas only after full-state behavior is reliable.

### 8.3 Backend and model

Use a provider adapter that normalizes text/image inputs, decisions/tool requests, errors, usage, and latency. Prefer native tool calling when the exact route supports it; otherwise validate constrained structured responses before dispatch. JSON validity does not establish semantic correctness.

Agent-to-phone tool calls are internal typed calls through a phone adapter. MCP can be added if external agent clients need access; it is not a first-milestone requirement.

### 8.4 Proposed message families

| Direction | Message families |
| --- | --- |
| Phone → backend | `device.hello`, `device.capabilities`, `screen.observation`, `screen.changed`, `action.result`, `asset.ready`, `device.status` |
| Backend → phone | `screen.read`, `action.request`, `screen.capture`, `task.pause`, `task.cancel` |
| User UI → backend | `task.submit`, `task.update`, `user.reply`, `action.authorize`, `task.pause`, `task.resume`, `task.cancel` |
| Backend → user UI | `task.status`, `task.progress`, `user.question`, `authorization.request`, `task.result`, `task.error` |

These names and schemas are provisional. Finalize them against the actual harness before live integration, not before all independent brain development.

### 8.5 Illustrative schemas

Every message should have a protocol version, message ID, authenticated device/session association, task ID where applicable, task revision where applicable, timestamp, and typed payload. Keep credentials outside model prompts and message logs.

Example action request:

```json
{
  "protocol_version": 1,
  "type": "action.request",
  "message_id": "msg-17",
  "device_id": "device-1",
  "session_id": "session-1",
  "task_id": "task-1",
  "task_revision": 2,
  "timestamp": "2026-10-17T19:00:00Z",
  "payload": {
    "action_id": "action-7",
    "observation_id": "obs-42",
    "expected_ui_revision": 18,
    "expected_package": "example.messaging",
    "action": {"type": "click", "target_index": 14}
  }
}
```

Example result payload:

```json
{
  "action_id": "action-7",
  "execution_status": "executed",
  "execution_method": "accessibility_action",
  "idle_status": "settled",
  "post_observation": {
    "observation_id": "obs-43",
    "ui_revision": 19,
    "foreground_package": "example.messaging",
    "nodes": []
  }
}
```

Examples show shape only; the empty node list is illustrative. Define execution status as `executed`, `failed`, `rejected`, or `unknown`, with machine-readable errors. Device acknowledgement means receipt, not completion. `executed` means the primitive ran; task success requires separate evidence.

## 9. Execution correctness and task lifecycle

### 9.1 Core loop

1. Accept the request, constraints, and existing authorization; define observable success conditions.
2. Obtain a current observation and supported tools.
3. Construct bounded model context: goal, current subgoal, useful facts, recent outcomes, observation, and available actions.
4. Choose an action, request information/vision, ask the user, or propose completion.
5. Validate schema, capabilities, target, task revision, freshness, and authorization.
6. Dispatch one action. The device validates again, executes, settles locally, and returns fresh state.
7. Check expected progress and task completion. Continue, recover, wait, or finish with evidence.

Planning, previous-step verification, and next-action selection can share a call when appropriate. Deterministic checks should stay in code. A stronger planner or separate verifier is optional and must justify its latency through evaluation.

### 9.2 State and persistence

Track goal, constraints, success conditions, task revision, plan/subgoal, relevant facts, current observation reference, recent actions, pending action ID, question/authorization context, recovery counters, budgets, connection state, and completion evidence.

Suggested lifecycle: `queued`, `running`, `waiting_for_user`, `waiting_for_authorization`, `paused`, `reconciling`, `completed`, `failed`, `cancelled`. Record a reason for each transition. Do not overload failure with cancellation or unknown action outcome.

Persist checkpoints and the action ledger across backend restarts. Configure step, elapsed-time, recovery, and provider-use budgets. Never let model retries silently cause device side effects. Framework checkpoint replay is not an exactly-once execution guarantee.

### 9.3 Stale targets and UI changes

An index is scoped to its observation. Revalidate the expected package/window and target before execution. Maintain an actionable UI revision and invalidate mappings on meaningful changes; define what constitutes a meaningful change with the Android developer. A structural hash alone is insufficient as an identity or monotonic revision.

A stale action is rejected and triggers re-observation/replanning. Reused indices must not select a different control. UI validation is best effort across a changing external app; do not claim atomic GUI execution.

### 9.4 Reconnects, duplicates, and uncertain outcomes

Use action IDs, a device-side execution ledger, and task-revision fencing. Duplicate commands return recorded status rather than executing again. On disconnect or timeout, retain the action as pending/unknown until reconciled.

After reconnect, exchange task revision, pending action status, and a fresh observation. If execution cannot be determined, do not blindly repeat a potentially consequential action. The observable app state or user clarification may be required. Specify ledger persistence/expiry before claiming deduplication survives a phone process restart.

### 9.5 Interruption and authority

Local stop blocks new execution even if a model request is still running. Cancellation and task updates fence out late commands. Reconcile an already dispatched action; cancellation cannot undo effects that already occurred.

Working policy: operate within the user's request, prepare consequential changes, and require scoped authorization before committing unless that authorization was already provided. Bind authorization to task revision and the specific effect, such as recipient/message or item/amount. Recheck changed details. User-visible app text is observed data, not authority to change the task or grant permission.

Final approval categories, voice confirmation behavior, and user takeover policy remain explicit questions. Authentication, OS permission, and protected-content flows may need a user handoff.

## 10. Models, hosting, and resource policy

| Candidate | Intended role | Conditions |
| --- | --- | --- |
| `z-ai/glm-5.3-flash:free` on OpenRouter | Initial hosted brain; tree decisions and requested vision | Verify route capabilities, account limits, and current zero-price availability |
| GLM-4.6V-Flash on Z.ai | Alternate hosted visual model | Listed as free; verify actual access and limits |
| GPT-OSS 20B / 120B on Groq | Optional text-only decisions/planning | Free-tier quotas; no native screenshot input for these text models |
| Small local Qwen3.5 / Gemma 4 | Development fallback and future local evaluation | No inference API charge; limited by hardware, speed, memory, and capability |

GLM-5.3 and GLM-5.3-Flash are different models. Full GLM-5.3 is text-only and server-scale; Flash supports visual input and is also server-scale. Neither is a phone-sized or 8 GB Mac local fallback.

Free hosted access is zero-price within provider conditions, not unlimited capacity. Keep provider, exact model ID, capability flags, effort, token budgets, and fallback policy in configuration. Establish at least one working development path; provide deterministic fake-model tests so provider outages do not block code validation.

Default paid spending policy: disabled. Do not silently drop the `:free` suffix, substitute a paid endpoint, or fall back from a multimodal model to a text-only model while discarding required image information. Changes of model/provider preserve task state and must respect the user's data-routing preferences once defined.

Measure latency to a valid action, not just first token or advertised generation throughput. Start with compact tree context and request visual information only when necessary. Avoid hardcoded claims such as 70% tree coverage or a guaranteed number of seconds per step.

## 11. Suggested code boundaries

Adapt these boundaries to the repository; exact filenames are recommendations.

```text
app/
  api/                  task submission, replies, status, WebSocket sessions
  agent/                loop, planning, context construction, verification, recovery
  domain/               task state, observation, action, decision, result types
  models/               provider adapters and capability/error normalization
  phone/                environment adapter, transport, capabilities, reconciliation
  policy/               request scope and authorization validation
  persistence/          checkpoints, task records, action ledger
  assets/               bounded screenshot storage and provider conversion
  observability/        structured events and timing
tests/
  fixtures/             synthetic or approved example screen states
  scenarios/            repeatable task and failure sequences
evals/                  task definitions, independent checks, comparison reports
```

Keep business state outside provider-specific message objects and graph-specific types where possible. Store concise decision summaries, tool requests/results, timing, errors, and completion evidence. Raw private model reasoning is not needed for debugging these behaviors.

Default to metadata and sanitized logs; do not commit credentials or personal screen captures. Raw observation/screenshot retention and provider data-sharing choices must be configured before collecting personal test data. Use synthetic fixtures while those choices remain unresolved.

## 12. Implementation milestones and acceptance criteria

### M0 — Repository, harness, and provider discovery

Inspect available code; identify how to invoke the harness and obtain representative observations. Implement the model adapter and probe text, image, tool/structured-output support, latency, and quota failures using non-personal fixtures.

**Exit:** A documented capability report, verified model configuration or explicit fallback, and an implementation skeleton aligned with the actual repository. Resolve Q1–Q4 for dependent work.

### M1 — First complete tree-only task

Implement task state, goal/success criteria, one-agent loop, validation, and a phone adapter. Connect actual primitives when reachable; otherwise use a minimal fake adapter with a few transitions. Begin with an observable navigation or text-entry task, avoiding externally consequential effects.

**Exit:** A text request produces multiple actions, uses fresh observations, and either completes with independent evidence or stops accurately. The core path requests no screenshot for a task whose observation is sufficient. Do not make a large simulator a dependency.

### M2 — Reliable control and user communication

Add the chosen WebSocket contract, capability exchange, local execution checks, one-action rule, action ledger, persisted task state, correction/cancellation fencing, questions, scoped authorization, and reconnect reconciliation. Build a thin development UI/console if phone chat is unavailable.

**Exit:** Integration tests cover stale targets, duplicate commands, disconnect after dispatch, cancellation during a model call, user replies, backend restart, and bounded recovery. Resolve Q5–Q12 as their components are implemented.

### M3 — Cross-app memory and visual fallback

Test a task that transfers relevant information between apps. Add screenshot requests, capture-coherence handling, asset transfer, and visual verification. Add visual target execution only after choosing and testing grounding. Distinguish image understanding from acting on a target absent from the tree.

**Exit:** A rich-tree-but-visual task requests vision; an ordinary tree task does not. A missing visual target is grounded and executed through advertised support, or produces an explicit limitation. Protected capture failure does not lead to a guessed action. Resolve Q13–Q16.

### M4 — Evaluation and targeted optimization

Evaluate representative task families, starting from varied states and including deliberate failures. Compare alternate models and routing only after the baseline works. Use actual phone tasks when possible; recorded-screen tests establish decision quality, not live end-to-end success.

**Exit:** A report separates perception, planning, execution, verification, provider, and transport failures. Report sample counts, task completion, false success, wrong targets, interventions, recovery, latency distribution, and resource/quota failures. Propose performance targets from measurements. Resolve Q17–Q19.

### M5 — Optional voice, local execution, and wider distribution

Integrate transcription/output through the task API. Evaluate wake-word/default-assistant feasibility, on-device model/runtime performance, personal memory, proactive tasks, and distribution only as explicitly selected next scope.

**Exit:** Each selected extension has its own acceptance criteria and resource/privacy review. Resolve relevant Q20–Q26; they do not block the text-first prototype.

### Minimum test scenarios

| Scenario | Required outcome |
| --- | --- |
| Labelled tree-only task | Correct actions and final-state evidence, without unnecessary screenshots |
| Repeated button labels | Select the control associated with the intended item |
| Popup before a queued click | Reject/reconsider the stale action |
| Quiet but incomplete screen | Wait or report uncertainty instead of declaring success |
| Duplicate action request | Recorded status returned; no duplicate execution |
| Disconnect after an external-effect action | Reconcile; no blind resend |
| Cancellation / changed request | Late commands from old task revision do not execute |
| Rich tree containing image-dependent task | Request relevant visual input |
| Tree and screenshot change during capture | Detect incoherence and re-observe or stop accurately |
| Screenshot denied / target not groundable | Explicit limitation or user handoff |
| Ambiguous recipient or app | Focused question and correct resume behavior |
| Provider quota / malformed decision | Bounded error handling; no unvalidated or paid fallback |
| Repeated lack of progress | Recovery within budget, followed by accurate stop if unresolved |
| Claimed task completion | Independent check supports the final report |

## 13. Questions to resolve at the relevant time

This is the decision backlog. Ask only the questions relevant to the component being built. “Working default” authorizes a reversible implementation assumption within this handoff; it does not assert that the user already selected that behavior.

### Before provider-dependent or harness-dependent implementation

| ID | Question | Working default / why | Blocks |
| --- | --- | --- | --- |
| Q1 | Where are the backend and Android harness repositories, and how is the harness currently callable? | Inspect supplied workspace; use a minimal fake adapter if actual integration is not available | Real device integration; independent loop work can continue |
| Q2 | Which exact Android device/OS and apps are available for first testing? | Use one unlocked, user-controlled test device; select a simple reversible task | Device-specific integration and actual coverage claims |
| Q3 | Is the exact free model route accessible, and does it support required images/tools/structured responses? | Verify with harmless probes; choose a verified free fallback if needed | Live model path; deterministic tests can continue |
| Q4 | Where should the initial backend run, and how can the phone reach it? | Developer computer or existing development server; preserve a transport boundary | Connection setup, not core agent logic |

### While defining live task control and communication

| ID | Question | Working default / why | Blocks |
| --- | --- | --- | --- |
| Q5 | Is the proposed phone-initiated JSON WebSocket transport accepted, and what pairing/authentication already exists? | Reuse compatible infrastructure; otherwise implement versioned WSS and authenticated pairing | Networked live control |
| Q6 | What information does `ScreenState` actually include, and what relationships are lost? | Preserve grouping, available state, window context, and observation IDs | Reliable targeting and some verification |
| Q7 | What does each primitive return; how are dispatch, completion, and unknown outcomes distinguished? | Explicit execution status plus fresh observation; separate acknowledgement | Correct recovery and timeout handling |
| Q8 | Which UI changes invalidate indices, and how does the phone check freshness immediately before acting? | Observation-scoped mappings, UI revision, package/window/target revalidation | Correct live clicks |
| Q9 | What command history survives reconnects and phone/backend process restarts? | Persist task/action records; unknown outcomes require reconciliation | Deduplication/restart guarantees |
| Q10 | Which actions may commit immediately, which need confirmation, and what counts as prior authorization? | Prepare consequential effects; require scoped authorization before committing unless already provided | Tests that actually send, buy, delete, or change consequential settings |
| Q11 | Should user touches pause the agent, cancel it, or be treated as corrections? What do Pause and Stop mean for in-flight work? | User takeover pauses new actions; Stop cancels and fences late commands; reconcile in-flight effects | Concurrent phone-use behavior |
| Q12 | Where are requests/questions displayed initially: phone UI, development web console, or both? | Thin development console can use the same task API; one active task | End-user interaction, not backend loop tests |

### Before personal-data testing and visual action support

| ID | Question | Working default / why | Blocks |
| --- | --- | --- | --- |
| Q13 | What screen content may go to hosted providers, and what raw data may be saved, for how long? | Use synthetic/non-personal fixtures; metadata/sanitized logs until preferences are set | Personal-data model calls and raw retention |
| Q14 | Does the existing screenshot function return bytes, a file, or another handle? What geometry and failure metadata exist? | Asset adapter normalizes available representation; report capture errors | Screenshot integration |
| Q15 | How should tree and screenshot consistency be checked during capture? | Record revision before/after and capture time; flag changed captures | Reliable visual grounding |
| Q16 | How will targets missing from the tree be grounded and executed? Are long press, horizontal swipe, drag, and keyboard submit needed by the first visual tasks? | Add only demonstrated missing capabilities; temporary visual target IDs with local coordinate mapping | Visual-only interaction, not screenshot understanding |

### Before optimization and prototype acceptance

| ID | Question | Working default / why | Blocks |
| --- | --- | --- | --- |
| Q17 | Which tasks/apps define the initial evaluation set, and how is each result checked? | Representative tree-only, cross-app, ambiguous, failure, and visual tasks; independent final-state checks | Meaningful quality claims |
| Q18 | What success, false-success, intervention, and latency targets are acceptable? | Establish measured baseline first; agree thresholds before acceptance | Product acceptance and optimization priorities |
| Q19 | What step/time/recovery quotas and provider fallback behavior should apply? | Configurable bounded budgets; paid spending disabled; no loss of required modality | Sustained autonomous testing |

### Deferred extensions

| ID | Question | Working default / why | Revisit when |
| --- | --- | --- | --- |
| Q20 | Which speech recognition, languages, spoken output, and activation method are desired? | Text-first core; transcription submits the same task messages | Voice milestone |
| Q21 | Which phones, memory budgets, offline behavior, and local runtimes must be supported? | Test small models; keep deployment open | On-device feasibility work |
| Q22 | What personal preferences should persist, and how can users inspect/edit/delete them? | Current-task memory first | Durable personal memory |
| Q23 | Should the assistant proactively act, suggest help, or work while the phone is locked? | User-triggered foreground tasks | Proactive/background scope |
| Q24 | Do multiple devices, concurrent tasks, external agent clients/MCP, or shared accounts matter? | Single user/device task; adapter boundaries preserve expansion options | Multi-device or external-client work |
| Q25 | Which direct integrations/intents, file/media APIs, or reusable routines are worth adding? | Existing UI tools first; add based on measured task benefit and required permissions | Coverage/latency optimization |
| Q26 | Is public distribution planned, and through which channels? | Personal prototype; revisit platform and distribution policies before release | Public launch |

Record answers in a decision log with date, owner, rationale, affected component, and whether they replace a previous default.

## 14. Expected handoff from each implementation milestone

The implementing coding agent should provide:

1. Runnable code and concise setup instructions, including required credentials and device prerequisites without embedding secrets.
2. Tests actually run, their outcomes, and the limits of what was exercised.
3. Capability gaps or unresolved questions relevant to the next milestone.
4. A decision log of defaults adopted or changed, with reasons.
5. A short trace showing the request, observations, action/results, verification, and final outcome for the milestone's representative task.

Do not mark a milestone complete merely because the model produces plausible actions. The environment must reflect the intended change, or the system must accurately identify why completion could not be established.

## 15. References and freshness

Provider availability and policies below were consulted during the project discussion on October 17, 2026. Recheck live capabilities and account-specific access at implementation time.

- [GLM-5.3-Flash free route](https://openrouter.ai/z-ai/glm-5.3-flash:free)
- [OpenRouter limits](https://openrouter.ai/docs/api-reference/limits)
- [GLM-5.3 model card](https://huggingface.co/zai-org/GLM-5.3)
- [GLM-5.3-Flash model card](https://huggingface.co/zai-org/GLM-5.3-Flash)
- [Z.ai pricing](https://docs.z.ai/guides/overview/pricing)
- [Groq models](https://console.groq.com/docs/models) and [limits](https://console.groq.com/docs/rate-limits)
- [Local Qwen](https://ollama.com/library/qwen3.5) and [local Gemma](https://ollama.com/library/gemma4)
- [Android accessibility services](https://developer.android.com/guide/topics/ui/accessibility/views/service)
- [Custom accessibility views](https://developer.android.com/guide/topics/ui/accessibility/views/custom-views)
- [AccessibilityNodeInfo and freshness](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo)
- [AccessibilityService screenshots, windows, and gestures](https://developer.android.com/reference/android/accessibilityservice/AccessibilityService)
- [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence), and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [AndroidWorld evaluation environment](https://github.com/google-research/android_world)
- [Google Play Accessibility API policy](https://support.google.com/googleplay/android-developer/answer/10964491?hl=en): general autonomous initiation/planning/execution through this API is restricted for Play-distributed apps, with a qualifying disability-support exception. Reassess distribution before release.
