# Build Android GUI agent

> # Engineering Handoff Spec: Mobile GUI Autonomous Agent
>
> ## 1. Mission & Scope
>
> **Objective:** Build an autonomous GUI agent capable of navigating and executing complex tasks across arbitrary mobile applications. **Target OS:** Android (due to native background OS-level UI control). **Core Paradigm:** Hybrid Perception & Routing. The agent must avoid relying entirely on latency-heavy cloud VLMs. It will use a deterministic routing layer that bridges the Android OS accessibility tree with visual grounding (Set-of-Mark) to achieve high-speed, reliable task execution.
>
> ## 2. System Architecture Blueprint
>
> The agent operates on a continuous Observe-Plan-Execute-Verify loop, structured as a cyclic graph (manageable via LangGraph) to handle state recovery and retries:
>
> - **Perception Layer (State Extraction):** Uses an Android `AccessibilityService` to continuously read the UI tree (`AccessibilityNodeInfo`). This layer extracts bounding boxes, text, and resource IDs (e.g., `com.whatsapp:id/conversation_contact_name`).
> - **Grounding (Set-of-Mark Overlay):** For visual tasks, the parser strips non-interactive views and renders numbered bounding boxes directly onto a captured screenshot. The VLM only needs to output the integer ID (e.g., `tap(14)`), mapping back to precise coordinates locally.
> - **Cognitive Orchestrator (Router):**
>   - *Tier 0:* Android OS Intents / Deep Links (bypass UI entirely).
>   - *Tier 1:* Text-only LLM (parses the clean XML tree; handles \~70% of standard native apps). Can be optimized locally via Ollama or vLLM.
>   - *Tier 2:* Heavy VLM (e.g., UI-TARS or GPT-4o). Called only for unlabelled apps (Flutter/Unity/WebViews) or complex visual verification.
> - **Execution Layer:** Translates the planned action into OS-level execution using `dispatchGesture()` or by invoking global actions like `BACK` or `HOME`.
> - **Verification:** Listens for `TYPE_WINDOW_STATE_CHANGED` or `TYPE_WINDOW_CONTENT_CHANGED` events to confirm the screen state advanced before issuing the next command.
>
> ## 3. Technical Stack & References
>
> - **OS/Harness:** Android Native (Kotlin). `AccessibilityService` for tree traversal and `takeScreenshot()`. No root required.
> - **Agentic Framework:** Python-based state machine (LangGraph) for orchestration and memory, integrating with PyTorch for local fallback perception tasks.
> - **VLM Backbone:** UI-TARS (for state-of-the-art zero-shot GUI action space understanding) or Qwen2-VL for heavy reasoning.
> - **Prior Art / Baselines:** Incorporate exploration and demonstration learning patterns modeled after frameworks like AppAgent or Mobile-Agent-v3.
>
> ## 4. Exploration & Implementation Tracks
>
> ### Track A: The On-Device Android Harness
>
> 1. **Initialize the Service:** Create an Android app that requests Accessibility permissions and `SYSTEM_ALERT_WINDOW` (for a floating control widget).
> 2. **Tree Pruning:** Implement a recursive parsing function that flattens the `AccessibilityWindowInfo` root node into a clean JSON array: `[{id: 1, bounds: [x1,y1,x2,y2], text: "Send", clickable: true}]`.
> 3. **Visual Tagging:** Implement a method to draw numerical tags onto a bitmap based on those extracted bounding boxes.
> 4. **Gesture Injection:** Implement a `GestureDispatcher` wrapper around the `dispatchGesture()` API to synthesize taps and swipes from coordinate inputs safely.
>
> ### Track B: The Agentic Loop & State Machine
>
> 1. **State Diffing:** Build a structural hash of the UI tree. The agent must verify that an action resulted in a UI change. If a loading spinner or keyboard obscures the screen, the agent must wait.
> 2. **Fallback Visual Parsing:** For non-native apps where the accessibility tree is empty, implement a pipeline to pass the raw screenshot through an object detector (e.g., OmniParser or a lightweight YOLO model) to generate synthetic bounding boxes before routing to the VLM.
> 3. **Safety Gates:** Implement regex or heuristic traps for high-risk tokens (e.g., "Pay", "Delete", "Confirm"). When detected, the agent pauses and triggers a native floating overlay requesting user approval.
>
> ## 5. Known Risks & Failure Modes
>
> - **Dynamic UI Churn:** Ads, popups, or notifications can hijack the active window mid-task. The agent must continuously re-evaluate the active window state (`TYPE_WINDOW_STATE_CHANGED`) rather than executing blindly.
> - **VLM Coordinate Hallucination:** Strict enforcement of Set-of-Mark prompting is required. Avoid asking the model to generate raw `(X, Y)` pixel coordinates natively.
> - **Latency:** Sending high-resolution UI images to a cloud VLM takes 2-4 seconds per step. Aggressively cache app layouts or rely on the Tier 1 text-only XML planner whenever possible.

What would you like me to do with this spec?

- Review the architecture for technical gaps and risks.
- Expand it into an implementation-ready handoff with interfaces, milestones, and acceptance tests.
- Build an initial Android harness and orchestration scaffold.

> I want to explore the idea and make some decisions

<details><summary>2 previous messages</summary>

> I’ll check the Android constraints that shape this design, then we can work through the decisions that matter most: who it’s for, where it runs, and how much autonomy it should have.
>
> <details><summary>Searched the web</summary>
>
> Searched the web for `site.developer.android.com accessibility service takeScreenshot dispatchGesture capabilities ...`
>
> </details>
</details>

Let’s start with **what kind of product you want**, because that will drive more of the architecture than model choice.

The hybrid tree-plus-vision approach is a reasonable starting hypothesis. I’d treat “70% handled by text” and “2–4 seconds per visual step” as assumptions to measure, rather than design guarantees.

One constraint changes the product options: **Google Play explicitly prohibits general autonomous planning and execution through the Accessibility API**, with an exception for qualifying disability-support tools. That makes distribution an early decision. [Google Play policy](https://support.google.com/googleplay/android-developer/answer/10964491?hl=en)

I see three useful directions:

| Direction | What you would build | Main tradeoff |
|---|---|---|
| Personal agent | An agent you install on your own Android phone | Fast path to learning; distribution remains unresolved |
| Business automation | An agent operating dedicated phones for specific workflows | More controlled environment; narrower initial use cases |
| Consumer assistant | A broadly distributed app for everyday phone tasks | Distribution and user trust need resolving early |

My recommendation for exploring the idea is **a personal prototype with a few real workflows**. Keep arbitrary-app operation as the ambition, but test whether it can reliably complete tasks across three apps before investing in a general platform.

I would also change the order of engineering priorities:

- **Define success before routing.** A changed screen does not prove the intended action succeeded.
- **Measure task reliability before optimizing latency.** A fast agent that needs frequent rescue will be frustrating.
- **Make permissions task-specific.** “Find a product,” “prepare an order,” and “place an order” should carry different authority; keyword traps alone won’t capture that.

Which direction is closest to your intent—and what’s one concrete task you would want this agent to complete on a phone?

> personal assistant

Then I’d design around **delegating a task on your own phone**: you give it a goal, it works through the apps, and it returns with a result or a specific question.

My proposed defaults for the first version:

| Decision | Starting point |
|---|---|
| When it acts | You explicitly start a task |
| Phone interaction | It takes over the screen during execution; you can interrupt immediately |
| Autonomy | It navigates, searches, and prepares actions; asks before sending, purchasing, or deleting |
| Scope | A few useful workflows across apps, expanding as reliability improves |

The central product question is **what you want to delegate**. Different tasks expose different challenges:

- **“Find something.”** Search across apps and return information. Tests navigation and understanding.
- **“Prepare something.”** Draft a message, assemble a shopping cart, or fill a form. Tests interaction while keeping the result reviewable.
- **“Handle something.”** Complete an authorized task end to end. Tests judgment, verification, and recovery.

I’d start with **prepare something**: it provides useful assistance and lets you inspect whether the agent understood your intent.

What’s a task you regularly do on your phone that you’d most like to hand off?

> I want this to be like a super system which you can control with your voice this can do anything on your phone

<details><summary>2 previous messages</summary>

> You’re describing a voice-controlled layer over the whole phone. I’ll check what Android allows for voice activation and control across apps so we can separate the experience you want from the platform limits.
>
> <details><summary>Searched the web</summary>
>
> Searched the web for `site:developer.android.com VoiceInteractionService always running hotword background ...`
>
> </details>
</details>

You want **a voice-operated assistant for the whole phone**—one that understands a goal, moves between apps, and completes the work.

For example:

> “Find the address Priya sent me, check how long it takes to get there, and tell her when I’ll arrive.”

That requires understanding the conversation, finding information, using maps, composing a message, and checking the result. Your original architecture covers the phone interaction; we need to add voice, conversational context, and personal memory.

I’d shape the system around four capabilities:

| Capability | What it does |
|---|---|
| **Voice conversation** | Understands requests and follow-ups such as “use the other address” or “stop” |
| **Planning** | Breaks a goal into steps, chooses apps, and handles unexpected situations |
| **Phone control** | Uses direct Android actions where available, then accessibility and vision |
| **Personal memory** | Remembers preferences and routines you choose to teach it |

**Broad phone control is a sensible ambition; universal control cannot be guaranteed.** Some apps block screenshots, which limits visual understanding on those screens. [Android documentation](https://developer.android.com/about/versions/14/features/screenshot-detection)

For hands-free activation, we should explore making it the phone’s selected voice assistant. Android provides a service for that role, including support for background voice activation; the actual wake-word experience needs testing on your device. [Android voice interaction service](https://developer.android.com/reference/android/service/voice/VoiceInteractionService)

The next important decision is **where its intelligence runs**. My starting recommendation is phone-based listening and control, with cloud reasoning for difficult tasks. That lets us test capability before trying to fit everything onto the phone.

**Are you comfortable with relevant screen content and voice requests being sent to a cloud model, or do you want everything to stay on your phone?**

> we can let that aspect be for now
> based on decisions I can change or be swappable later

Agreed. **We can defer where the intelligence runs and design the components to be replaceable.** We should commit to clear boundaries now.

| Component | Responsibility | Can change later |
|---|---|---|
| Voice | Convert speech to requests and responses to audio | Speech provider, voice, wake word |
| Planner | Understand the goal and choose the next step | Model, local/cloud execution |
| Perception | Understand the current screen | Accessibility parser, vision model |
| Phone controller | Execute actions and report results | Control methods and app integrations |
| Memory | Store preferences and task context | Storage and retrieval approach |

The key architectural decision I recommend: **the model proposes actions; the phone controller validates, executes, and verifies them.** That keeps phone control consistent when we replace models. Swapping providers will still require testing because their capabilities and latency differ.

So far, we have:

- **Personal Android assistant**
- **Voice as the main interface**
- **Broad tasks across apps**
- **Replaceable models and services**
- **Local versus cloud left open**

For the experience, I recommend supporting both **direct commands**—“scroll down,” “open WhatsApp”—and **delegated goals**—“find the photos from yesterday and send them to Maya.”

The next meaningful decision is **initiative**: should it act only when you ask, or should it also notice situations and offer help? I’d start with acting on your requests, while leaving room for proactive assistance later.

> for now lets decide on how I can build the brian
> me and my friend are building this
> he said he will take care of the integration and the phone use for now

<details><summary>3 previous messages</summary>

> I’ll focus on the brain you’ll build and the interface to your friend’s phone controller. I’ll check the current orchestration options, then suggest a starting design that keeps the models replaceable.
>
> <details><summary>Searched the web</summary>
>
> Searched the web for `site:docs.langchain.com oss python langgraph overview durable execution interrupts ...`
>
> Searched the web for `https://docs.langchain.com/oss/python/langgraph/overview`
>
> </details>
>
> I need to work on the brain and the agent engineering
</details>

Then **your part is the decision-making system**: understanding the request, deciding what to do next, checking the result, and recovering when something goes wrong. Your friend provides the phone’s observations and executes actions.

I recommend building it around an existing model, with your own control loop, memory, and evaluation. Training a model can wait until you have evidence that an existing one cannot do the job.

**Start with this architecture:**

```text
User request
     ↓
Understand goal + define success
     ↓
Read current phone state
     ↓
Choose next action
     ↓
Validate action → Your friend's phone controller
                         ↓
                   New observation
                         ↓
                  Verify progress
                         ↓
             Continue / Recover / Ask / Finish
```

Keep the overall plan flexible, but execute **one phone action at a time** initially. The next screen can invalidate the rest of a planned sequence.

Your brain needs these six pieces:

| Piece | What you build |
|---|---|
| **Task understanding** | Turn the request into a goal, constraints, and observable completion conditions |
| **Working state** | Track the current subtask, relevant facts, recent actions, and unresolved questions |
| **Action selection** | Use the current screen and task state to choose the next supported action |
| **Verification** | Decide whether the intended result occurred; a successful tap alone is insufficient |
| **Recovery** | Handle unexpected screens, stale targets, failed actions, and repeated lack of progress |
| **Conversation** | Ask necessary questions, accept corrections, and handle cancellation |

These can be modules within **one agent**. You can introduce specialist agents later if measurements justify the extra complexity.

**My recommended starting stack is Python, LangGraph, typed schemas, and SQLite.** LangGraph fits the explicit loop and supports saved state and pauses for user input. Keep model calls behind a small adapter so the provider remains replaceable. [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview), [pause and resume](https://docs.langchain.com/oss/python/langgraph/interrupts)

The most important thing you and your friend should agree on first is the **brain–phone interface**:

| Phone sends you | You send the phone |
|---|---|
| Observation ID, timestamp, active app | Unique action ID |
| UI elements with IDs and supported actions | Action type and arguments |
| Screenshot associated with that observation | Target element and observation ID |
| Execution status and errors | Cancellation or observation request |
| Fresh observation after execution | Any required authorization |

Element IDs should belong to a particular observation. If the screen changes, the controller should reject a stale target. Also distinguish **failed** from **outcome unknown**—after a connection failure, blindly repeating “send” could send twice.

For the model, start with **one capable multimodal model** that can consume screen information and request structured actions. Tool calling provides that request–execute–return loop; your software still owns execution. Add the cheaper text-only routing from your original spec after you have a reliable baseline to compare against. [Tool-calling mechanics](https://ai.google.dev/gemini-api/docs/tools)

**You can begin before the phone integration is ready:**

1. Define the observation, action, and result schemas together.
2. Build a simulated phone with several screens and action-driven transitions.
3. Implement the loop, including recovery, cancellation, and completion checks.
4. Test successful paths alongside popups, ambiguous targets, and uncertain action outcomes.
5. Replace the simulated controller with your friend’s integration.

Your first milestone should be: **given a request and a sequence of phone observations, the brain completes the task, asks a necessary question, or stops with an accurate explanation.** Track task completion, incorrect success claims, user interventions, and time per task. Those measurements will tell you what to improve next.

<details><summary>4 previous messages</summary>

> I’ll keep the brain independent of voice input and defer the phone integration details. I’ll compare current models for visual understanding, planning, and latency, then recommend a starting setup.
>
> <details><summary>Explored a file</summary>
>
> - Read `~/.codex/skills/.system/openai-docs/SKILL.md`
>
> </details>
>
> I’m using the OpenAI Docs skill for OpenAI’s model guidance and checking other providers’ official documentation for alternatives. I’ll distinguish documented capabilities from latency and reliability we’d need to measure ourselves.
>
> <details><summary>Explored a file, searched the web</summary>
>
> Searched the web for `site:developers.openai.com models low latency vision reasoning computer use ...`
>
> Searched the web for `https://developers.openai.com/api/docs/guides/reasoning`
>
> <details><summary>Explored a file</summary>
>
> - Read `~/.codex/skills/.system/openai-docs/references/model-selection.md`
>
> </details>
>
> Searched the web for `https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash`
>
> </details>
</details>

**My recommendation is a fast multimodal executor, backed by a stronger planner for difficult decisions.** That gives you a practical starting architecture while keeping both models replaceable.

First, your corrections:

- **The brain should accept text instructions**, whether typed or transcribed from voice. Voice becomes an input/output layer we can add independently. :codex-annotation{index="1"}
- **We’ll defer the phone integration contract.** You can develop the reasoning loop against sample screenshots and simulated actions while your friend builds the controller. :codex-annotation{index="2"}
- By **“schemas,” I meant data formats**, rather than another use case. For example: the phone reports “button 14 is Send”; the brain requests “tap button 14”; the controller reports what happened. We can formalize those formats later. :codex-annotation{index="4"}

For your combination of latency, visual understanding, and planning, I would test this setup first: :codex-annotation{index="3"}

| Role | Starting model | Why I’d choose it |
|---|---|---|
| **Frequent screen decisions** | **Gemini 3.8 Flash** | Supports image input, function calling, and adjustable reasoning. Google currently recommends it for computer use, including an Android-oriented mobile environment. |
| **Complex planning and recovery** | **GPT-6 Astra** | A candidate for interpreting complicated goals, resolving ambiguity, and rebuilding a plan when execution gets stuck. OpenAI positions it for demanding reasoning and multistep computer use. |

Sources: [Gemini capabilities](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash), [Gemini mobile computer use](https://ai.google.dev/gemini-api/docs/computer-use), [official OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model).

**Claude Sonnet 5.5 would be my comparison candidate** for the executor role: Anthropic positions it for a balance of speed, visual understanding, and agentic tool use. [Claude model selection](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model)

These are candidates based on documented capabilities. Their actual latency and reliability on your phone tasks need measurement.

The important engineering choice is **when each model runs**:

```text
Simple request ──────────────────→ Fast executor
Complex request → Strong planner → Fast executor
                                       ↓
                             Observe → Decide → Act
                                       ↓
                         Stuck or plan invalid?
                                       ↓
                                Strong planner
```

The planner gives the executor a compact task brief: the goal, constraints, current subgoal, and evidence needed for success. The executor uses that brief and the current screen to choose the next action.

For example, deciding **which information to find across several apps** may deserve deeper reasoning. Selecting the clearly labelled search button usually needs much less.

To keep latency down, I’d make these design choices:

- **Combine screen understanding and next-action selection in one executor call.** A separate vision call followed by a separate action call adds a network round trip.
- **Reuse the plan across steps.** Replan when observations contradict it or progress stalls.
- **Keep working context compact.** Carry the current subgoal, relevant facts, and recent outcomes instead of accumulating every screenshot.
- **Use lower reasoning effort for routine steps**, then increase effort or escalate when necessary.
- **Measure time until a valid action is ready**, alongside task success and recovery frequency.

You also have a simpler baseline worth testing: **Gemini 3.8 Flash alone, with different reasoning settings for planning and execution**. Compare that with the two-model setup. A second model earns its place if it improves completion or recovery enough to justify the added latency.

Your immediate work can therefore focus on **task planning, working memory, the executor loop, escalation, and evaluation**. Start with saved screens and small simulated workflows; use them to compare those two configurations before committing to a larger model collection.

> no we cant use those models the suggessions are valid but there is a possibility of making everything on device so gemma 4 type
> but for now we can make everything with freely accessible models like groq or glm, which do you suggest for speed

<details><summary>3 previous messages</summary>

> I’ll compare Groq’s free-tier options with GLM’s fast models, including which can read screenshots. I’ll also explain how the data formats let you switch between hosted and on-device models later.
>
> <details><summary>Searched the web</summary>
>
> Searched the web for `site:console.groq.com/docs/models vision speed free plan ...`
>
> Searched the web for `GLM free GLM-4.6V-Flash docs Z AI ...`
>
> Searched the web for `https://docs.z.ai/guides/overview/pricing`
>
> Searched the web for `'Free'`
>
> </details>
>
> Groq looks promising for speed, but its free token quota may limit how often the agent can send screenshots. GLM has a free vision model too. I’d compare both using time to a usable action, since token-generation speed alone doesn’t capture image processing or network delay.
</details>

**For speed, I’d test Groq first, using open models on its free tier.** We can build the brain around replaceable models and keep an on-device Gemma version as a future target.

One distinction: **Groq is an inference provider; GLM and Gemma are model families.** We’re choosing both a model and where it runs.

These are the candidates I’d use:

| Purpose | Model and provider | Speed evidence |
|---|---|---|
| Fast decisions from UI text | **GPT-OSS 20B on Groq** | Groq lists roughly **1,000 output tokens/sec** |
| More difficult text planning | **GPT-OSS 120B on Groq** | Roughly **500 tokens/sec** |
| Screenshot understanding and actions | **Qwen3.8-27B on Groq** | Roughly **450 tokens/sec**; supports images, tool use, and adjustable reasoning |
| Alternative free visual brain | **GLM-4.6V-Flash through Z.ai** | Supports visual input and multimodal tool use; I couldn’t verify a comparable latency figure |

Those Groq figures are published generation speeds. Image processing, prompt length, reasoning, and network delay also contribute to time until an action is ready. Qwen is currently a preview model. [Groq model specifications](https://console.groq.com/docs/models), [vision support](https://console.groq.com/docs/vision). Z.ai currently lists GLM-4.6V-Flash as free. [GLM capabilities](https://docs.z.ai/guides/vlm/glm-4.6v), [pricing](https://docs.z.ai/guides/overview/pricing).

**My starting configuration would be:**

- **Qwen on Groq** for the first complete visual agent loop.
- **GPT-OSS 20B** for steps where the accessibility text provides enough information.
- **GPT-OSS 120B** available for difficult planning or recovery.
- **GLM-4.6V-Flash** as the comparison model for visual accuracy and actual response time.

You can add those routes incrementally. Starting with Qwen gives you a baseline before introducing routing complexity.

There’s one practical constraint with free access: Groq currently lists **8,000 tokens/minute** for these models, and its Qwen vision documentation counts each image as **2,048 input tokens**. That allows fewer than four fresh screenshot requests per minute once accompanying text and outputs are included. It suits development experiments, but sustained phone operation may hit the quota. Exact limits are account-specific. [Groq free limits](https://console.groq.com/docs/rate-limits), [image token accounting](https://console.groq.com/docs/vision).

For eventual on-device use, **Gemma 4 E2B and E4B are sensible candidates to evaluate**: Google targets those sizes at mobile hardware, and they support images and function calling. Actual speed, memory use, and planning quality will depend on the phone and quantization. Changing models preserves the architecture, but still requires evaluating behavior again. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4).

On the **schemas** question: think of them as the shared language inside your agent. Your use case describes what the assistant should accomplish; schemas describe how software represents what it sees and decides. :codex-annotation{index="1"}

There are three separate things:

| Representation | Meaning | Example |
|---|---|---|
| **Observation** | What the phone currently shows | Screen 42 contains a clickable “Search” element with ID 14 |
| **Action** | What the brain requests | Tap element 14 on screen 42 |
| **Execution result** | What the controller actually did | The tap executed, and a new observation is available |

An illustrative action could look like:

```json
{
  "action_id": "a7",
  "observation_id": "screen_42",
  "type": "tap",
  "target_id": 14
}
```

The useful part is the meaning behind those fields:

- `observation_id` ties the decision to the screen the model saw.
- `target_id` identifies an element in that observation.
- `action_id` lets you track execution and avoid accidental duplicate requests.

**Execution and task success remain separate.** “The tap executed” comes from the controller. “The search field opened” requires examining the next observation.

We can keep these examples provisional while your friend works. Inside your brain, a consistent action format lets Qwen, GLM, or Gemma propose the same kinds of actions—and lets your validation code reject missing fields or unsupported targets before anything reaches the phone.

> are these models smart enough?

<details><summary>2 previous messages</summary>

> I’ll check what evidence supports their planning and phone-use abilities. My earlier recommendation focused on speed and access; we still need to establish how reliably they can handle your tasks.
>
> <details><summary>Searched the web</summary>
>
> Searched the web for `site:huggingface.co Qwen3.8-27B model card agent GUI ...`
>
> Searched the web for `https://github.com/google-research/android_world`
>
> </details>
</details>

**They’re credible models for building a useful prototype. Their ability to reliably handle arbitrary phone tasks is still something we need to establish.**

My previous recommendation put too much weight on generation speed. A model producing 1,000 tokens per second can still choose the wrong action quickly.

“Smart enough” involves four separate abilities:

| Ability | What we need to establish |
|---|---|
| **Understanding** | Does it correctly interpret your request and constraints? |
| **Planning** | Can it break a goal into sensible steps across apps? |
| **Grounding** | Can it identify the exact control to interact with? |
| **Recovery** | Can it notice a mistake, change approach, and verify completion? |

The models have relevant capabilities: GPT-OSS supports reasoning and tool use; Qwen on Groq supports images and tools; GLM-4.6V supports visual interaction. **Those capabilities establish eligibility for testing, rather than demonstrated reliability on your assistant.** [GPT-OSS capabilities](https://console.groq.com/docs/model/openai/gpt-oss-20b), [Qwen vision](https://console.groq.com/docs/vision), [GLM visual interaction](https://docs.z.ai/guides/vlm/glm-4.6v).

I would adjust our starting choice:

- **GPT-OSS 120B as the initial planner.** Establish a capable planning baseline, then test whether 20B can handle particular decisions faster.
- **Qwen and GLM-4.6V-Flash as competing visual executors.** Compare their actual selections on the same phone screens.
- **Gemma E2B/E4B as a separate on-device experiment.** Its smaller size makes local execution attractive; its ability to handle the same tasks needs independent testing.

The difficult part is consistency across many steps. As a hypothetical example, if every step independently succeeds 95% of the time, a 20-step task with no recovery succeeds only about **36%** of the time. That’s why checking outcomes and recovering are central parts of the brain.

Schemas help communicate and validate actions, but a perfectly formatted action can still target the wrong button.

**My recommendation is to spend the first development effort establishing capability before optimizing speed.** Use a small set of representative tasks and deliberately include ambiguous screens, unexpected popups, and failed actions. Measure completed tasks, false claims of success, recovery, and elapsed time. AndroidWorld provides an existing environment for testing mobile agents when you’re ready for emulator integration. [AndroidWorld](https://github.com/google-research/android_world).

That will tell us whether the limitation is the model, the information it receives, or the agent engineering—and each requires a different fix.

> are these models free?

**Yes—the options we’re discussing can be used without paying, with different limits.**

| Option | What’s free? |
|---|---|
| **GPT-OSS 20B / 120B on Groq** | Free API tier with request and token quotas |
| **Qwen3.8-27B on Groq** | Free API tier with quotas; currently a preview model |
| **GLM-4.6V-Flash on Z.ai** | Currently listed as a free API model |
| **Gemma 4 on your device** | Downloadable weights and local inference, using your hardware |

Sources: [Groq free-tier limits](https://console.groq.com/docs/rate-limits), [Z.ai pricing](https://docs.z.ai/guides/overview/pricing), [Gemma deployment](https://ai.google.dev/gemma/docs/core).

**For your prototype, we can start at $0 in model API spending.** Groq’s free quotas may interrupt continuous testing, especially with screenshots. GLM’s exact access limits should be checked in your account. Paid usage would be a separate decision.

> see if there is any fallback that is truly free to test

<details><summary>2 previous messages</summary>

> I’ll look for a fallback you can test without API credits, including local options that avoid hosted free-tier quotas.
>
> <details><summary>Ran 2 commands, searched the web</summary>
>
> Searched the web for `site:ai.google.dev gemma 4 local AI Edge Gallery E2B E4B free ...`
>
> - Ran `system_profiler SPHardwareDataType SPDisplaysDataType`
>
> Searched the web for `https://ollama.com/library/gemma4`
>
> Searched the web for `https://ollama.com/library/qwen3.5`
>
> - Ran `command -v ollama`
>
> Searched the web for `https://ollama.com/library/qwen3.5:4b`
>
> </details>
</details>

**Yes. A local model is the most dependable fallback for testing without API credits or provider quotas.** You download it once and run it on your own computer; local requests need no API key. [Ollama local inference](https://docs.ollama.com/api/introduction).

I checked this Mac: it’s an **M1 MacBook Air with 8 GB RAM**. I’d shortlist:

| Local model | Why test it |
|---|---|
| **Qwen3.5 4B** | My first candidate for testing text, screenshots, and tool calls |
| **Qwen3.5 2B** | Smaller fallback if memory use or speed becomes a problem |
| **Gemma 4 E2B** | Useful comparison for your eventual on-device direction |

All three have downloadable local versions with image support. Actual runtime memory exceeds the download size, so we should start with short context and one model loaded. [Qwen downloads](https://ollama.com/library/qwen3.5), [Gemma downloads](https://ollama.com/library/gemma4).

**If you want a hosted fallback**, Z.ai currently lists these as free:

- **GLM-4.7-Flash** for text planning.
- **GLM-4.6V-Flash** for screenshots.

Their input and output pricing is listed as free, rather than introductory credits. Access and rate limits still depend on the service. [Official Z.ai pricing](https://docs.z.ai/guides/overview/pricing).

**My recommendation:** keep Groq for fast hosted experiments, use GLM as the alternate hosted provider, and keep **Qwen3.5 4B locally** so API quotas cannot stop development.

The local model gives you a way to keep testing the agent loop, memory, action formats, and recovery. Its suitability as the final autonomous brain—and its speed on this Mac—still needs measurement.

> what about glm 5.3

<details><summary>4 previous messages</summary>

> I’ll check GLM-5.3’s pricing, availability, and visual capabilities against what you need for the brain.
>
> <details><summary>Searched the web</summary>
>
> Searched the web for `site:docs.z.ai GLM-5.3 model vision thinking ...`
>
> Searched the web for `https://huggingface.co/zai-org/GLM-5.3`
>
> Searched the web for `"GLM-5.3" "free" API site:openrouter.ai ...`
>
> </details>
>
> I found a relevant distinction: GLM-5.3 is text-only, while GLM-5.3-Flash supports images. OpenRouter currently lists a free Flash endpoint, so I’m checking its limits and tool support before recommending it.
>
> <details><summary>Searched the web</summary>
>
> Searched the web for `https://openrouter.ai/z-ai/glm-5.3-flash:free`
>
> </details>
</details>

**Yes—GLM-5.3 deserves consideration, especially GLM-5.3-Flash. I missed a useful free hosted option earlier.**

The two models have different roles:

| Model | Fit for your brain | Free access |
|---|---|---|
| **GLM-5.3** | Text-based planning, reasoning, and tool use | Z.ai’s API is paid; NVIDIA currently advertises a free trial endpoint |
| **GLM-5.3-Flash** | Screenshot understanding, planning, and tool use in one model | OpenRouter currently lists a **zero-price API variant** |

GLM-5.3 is text-only. Flash accepts images, making it more directly relevant to your phone agent. [GLM-5.3 specifications](https://build.nvidia.com/z-ai/glm-5-3/modelcard), [Flash specifications](https://build.nvidia.com/z-ai/glm-5-3-flash/modelcard).

**For your free prototype, I would evaluate this exact OpenRouter model first:**

```text
z-ai/glm-5.3-flash:free
```

Its listing explicitly states that prompt and completion tokens are free. Hosted request limits still apply, and the regular paid variant is separate. [Free Flash endpoint](https://openrouter.ai/z-ai/glm-5.3-flash:free), [usage limits](https://openrouter.ai/docs/api-reference/limits).

For the full GLM-5.3, NVIDIA’s page offers a **free prototype API endpoint under trial terms**. That provides another way to test planning, subject to account access and service limits. [NVIDIA endpoint](https://build.nvidia.com/z-ai/glm-5-3).

These are server-scale models: GLM-5.3 has roughly **753B total parameters**, and Flash roughly **320B**. Local execution on your 8 GB Mac or a phone is impractical. The smaller local models remain useful fallbacks.

**My revised recommendation:** test GLM-5.3-Flash as the main hosted brain before assembling several models. It combines the capabilities you need and has a free route. We still need to measure its time to a valid action, visual accuracy, and recovery on your tasks; “Flash” alone doesn’t establish speed.

> ok so now that we have those decisions out of the way lets create an updated plan, high level and plan for me to build the agent

<details><summary>4 previous messages</summary>

> I’ll turn our decisions into a build plan for your agent work, with milestones you can complete while your friend develops the phone controller. I’ll keep the model choices replaceable and the integration details provisional.
>
> <details><summary>Explored a list, ran a command</summary>
>
> - Ran `pwd`
>
> <details><summary>Explored a list</summary>
>
> - Listed files
>
> </details>
>
> </details>
>
> The first milestone will be a complete agent loop in a simulated phone environment: understand a request, choose actions, inspect results, recover, and report completion accurately. That lets you test the brain before integration. I’m also including an early check that the free model endpoint accepts images and tool calls.
>
> <details><summary>Created a file, explored a search, ran a command</summary>
>
> - Ran `mkdir -p outputs`
>
> - Wrote `./outputs/agent-build-plan.md` (+174 -0)
>
> <details><summary>Explored a search</summary>
>
> - Searched for `^#|^\*\*Done when:|^An agent you can run` in `agent-build-plan.md`
>
> </details>
>
> Tool call
>
> Tool: open_in_codex
> Status: completed
>
> </details>
</details>

Created the [updated agent build plan](./outputs/agent-build-plan.md), with responsibilities, architecture, milestones, and completion criteria.

**Your responsibility is the brain:** understanding requests, planning, choosing actions, remembering task context, checking results, and recovering. Your friend owns observing and controlling the Android phone.

The high-level flow is:

```text
Request → Understand goal → Observe → Choose action → Execute
                              ↑                         ↓
                              └── Verify / Recover ─────┘
                                         ↓
                                Finish or ask the user
```

We’ll build around these decisions:

- Text instructions are the brain’s input; voice can feed into that later.
- GLM-5.3-Flash’s free endpoint is the initial model candidate.
- Models and providers remain replaceable.
- Local versus cloud deployment remains open.
- Final phone integration formats stay provisional until integration approaches.

**Recommended implementation:** Python, LangGraph for the control loop, Pydantic for internal validation, and SQLite for saved state and evaluation records. These are starting recommendations.

Your build sequence:

| Milestone | What you build | Completion condition |
|---|---|---|
| **1. Validate model access** | A model adapter that accepts text, screenshots, and available tools | You receive usable decisions and handle quota or capability errors |
| **2. Build the core agent** | Goal interpretation, working state, planning, and action selection in a simulated phone | It completes a multistep task and recognizes an impossible one |
| **3. Add visual understanding** | Screenshot input, element targeting, and outcome verification | It selects observed targets and distinguishes progress from completion |
| **4. Add recovery and conversation** | Popup handling, loop detection, questions, corrections, cancellation, and resume | It recovers or stops with an accurate explanation |
| **5. Evaluate and optimize** | Repeatable tasks and comparisons across models | You can measure success, incorrect completion claims, recovery, and latency |
| **6. Integrate the phone** | Replace the simulated environment with your friend’s controller | The same agent loop handles real apps and device interruptions |

**Your first concrete deliverable should be a complete agent running on your computer against a simulated phone.** Give it a request, let it navigate several screens, introduce a popup or failed action, and check whether it recovers and reports the actual outcome.

That gives you substantial independent work while your friend builds Android integration. Start with one model handling planning and execution; add routing or specialist models when evaluation shows a clear benefit.

The saved plan includes the detailed work and acceptance criteria for each milestone, plus what to defer: voice infrastructure, fine-tuning, multiple agents, advanced personal memory, and layout caching.

> create one for just the high level idea now

<details><summary>2 previous messages</summary>

> I’ll create a separate concept overview focused on the vision, user experience, and core capabilities.
>
> <details><summary>Created a file, explored a search</summary>
>
> - Wrote `./outputs/assistant-concept-overview.md` (+72 -0)
>
> <details><summary>Explored a search</summary>
>
> - Searched for `^#|^##|^You can hand` in `assistant-concept-overview.md`
>
> </details>
>
> Tool call
>
> Tool: open_in_codex
> Status: completed
>
> </details>
</details>

Created the [high-level concept overview](./outputs/assistant-concept-overview.md), covering the vision, user experience, core capabilities, and division of responsibilities.

> ok now what do I have to do
> lets look into that

**Your next step is to build the smallest working version of the brain:** give it a request and a phone state, and have it choose a sensible next action.

Let’s focus on that first milestone.

**1. Connect to the model**

Create a small Python program that calls the GLM-5.3-Flash free endpoint. Check three things:

- Can it understand a text request?
- Can it read a screenshot?
- Can it request an action in a format your program can validate?

Record how long each request takes. This establishes whether the endpoint is usable before you build around it.

**2. Give the brain the information it needs**

For each decision, supply:

| Input | Example |
|---|---|
| User’s goal | “Find Maya’s address in our messages” |
| Current screen | Screenshot and available UI descriptions |
| Available actions | Open an app, select an element, type, scroll, go back |
| Task context | What it has already found or attempted |

Its response should be one of:

- **An action:** “Open the messaging app.”
- **A question:** “Which Maya do you mean?”
- **A request for another observation:** “Wait for the screen to load.”
- **A proposed completion:** “I found the address,” accompanied by evidence.

These are provisional internal formats you can change as you learn.

**3. Turn that decision into a loop**

Create a tiny simulated environment with a few screens. When the brain chooses an action, the simulator returns the resulting screen.

```text
Goal + current screen
         ↓
Brain chooses an action
         ↓
Simulator changes state
         ↓
Brain checks the result and continues
```

Start with a simple navigation task. Then introduce one complication, such as an unexpected popup. This tests whether the brain can adapt instead of following a memorized sequence.

A small project could have these parts:

```text
brain/
  model_adapter.py    Connect to the selected model
  task_state.py       Track the goal, facts, and recent actions
  agent_loop.py       Observe, decide, act, and check
  simulated_phone.py  Supply screens and simulated action results
  evaluations/        Repeatable scenarios and expected outcomes
```

**Your first success condition:** you enter a request, the agent completes several steps in the simulator, and its final report matches what actually happened. An impossible request should produce an accurate explanation.

Once that works, you have a foundation for visual grounding, recovery, memory, and your friend’s phone integration.

**Start with the model connection and a single validated decision.** That is the first concrete piece of code to build.
