import asyncio
import logging
import time
from dataclasses import asdict, dataclass, replace

from src.agent import checks, safety
from src.agent import screen as screens
from src.ai.model import AIError, AIUnavailable, InvalidReply, RateLimited
from src.phone.messages import Command, SecretTarget
from src.phone.link import Interrupted, PhoneDisconnected, new_command
from src.agent.screenshots import annotate_screenshot, is_blind, needs_screenshot, picture_to_screen
from src.agent.prompt import SYSTEM_PROMPT, build_step_message, describe_error, retry_note, status_line
from src.agent.task import Notebook, Step, Task
from src.agent.recovery import Attempt, yes_no

log = logging.getLogger("jarvis.brain")

PHONE_TOOLS = {"tap", "type_text", "scroll", "go_back", "go_home", "open_app"}
BLIND_TOOLS = {"tap_point", "type_focused"}
POST_COMMIT_TOOLS = {"scroll", "wait", "finish", "give_up", "look"}
STATUS_TOOLS = PHONE_TOOLS | BLIND_TOOLS | {"wait", "look"}
MAX_SCREENSHOTS = 25
MAX_STEPS = 50
EXTRA_STEPS_AFTER_COMMIT = 3
MAX_MODEL_CALLS = 80
MAX_WORK_MINUTES = 15
MAX_CONSECUTIVE_INVALID_REPLIES = 3
MAX_INVALID_REPLIES = 8
MAX_WAIT_SECONDS = 90
NOT_A_TASK_BELOW = 0.15  # only a clear "no"; anything unsure still runs and can ask
EMPTY_SCREEN_REREADS = 2
EMPTY_SCREEN_WAIT_SECONDS = 1.5
QUESTION_EXPIRES = 600
CONFIRM_EXPIRES = 120
RATE_LIMIT_WAITS = (5, 10, 20)
UNAVAILABLE_WAITS = (2, 5)
MAX_HELP_ASKS = 3
HELP_EXTRA_STEPS = 15
HELP_EXTRA_MODEL_CALLS = 25
HELP_CHOICES = ["Keep trying", "Stop"]
HELP_STOP_WORDS = {"stop", "cancel", "quit", "no", "give up", "forget it", "never mind", "nevermind"}
PAUSE_QUESTION = "What else can I do?"
PAUSE_ANSWER_SECONDS = 12  # the question is spoken, then the phone mic gives up after 8 s of silence (Listener.kt)
UNSURE_REPLIES = {"not sure", "i'm not sure", "i don't know", "dunno", "no idea"} | HELP_STOP_WORDS
HELP_KEEP_GOING_WORDS = {"keep trying", "keep going", "continue", "try again", "yes", "ok", "okay", "go on"}


@dataclass
class Timing:
    """Seconds spent on each kind of work, for the latency log."""
    ai: float = 0.0
    ai_calls: int = 0
    phone: float = 0.0
    commands: int = 0
    user: float = 0.0


class TaskEnded(Exception):
    def __init__(self, outcome: str, summary: str, proof: list[str] | None = None):
        super().__init__(summary)
        self.outcome = outcome
        self.summary = summary
        self.proof = proof


class Stuck(Exception):
    """The agent can't make progress on its own; the user is asked what to do before the task ends."""

    def __init__(self, reason: str, outcome: str, summary: str):
        super().__init__(reason)
        self.reason = reason
        self.outcome = outcome
        self.summary = summary


class Brain:
    def __init__(self, task: Task, link, ai, notebook: Notebook):
        self.task = task
        self.link = link
        self.ai = ai
        self.notebook = notebook
        self.screen = None
        self.view = None
        self.warnings: list[str] = []
        self.screen_history: list[str] = []
        self.consecutive_scrolls = 0
        self.started = time.monotonic()
        self.timing = Timing()
        self.screenshot: tuple[str, bytes] | None = None
        self.picture_size: tuple[int, int] = (0, 0)
        self.screenshotted_screens: set[str] = set()
        self.screenshots_taken = 0
        self.screenshot_requested = False
        self.unreadable_screens: set[str] = set()
        self.blind_screens: set[str] = set()
        self.step_limit = MAX_STEPS
        self.model_call_limit = MAX_MODEL_CALLS
        self.help_asks = 0
        self.attempts: dict[str, Attempt] = {}
        self.secret_screens = False
        self.verification_signature: str | None = None
        link.on_late_result = self.handle_late_result

    # ---------- the loop ----------

    async def run(self) -> TaskEnded | None:
        """Runs the task to its end; the caller tells the phone. None if the phone disconnected."""
        try:
            await self.screen_out_non_tasks()
            await self.read_screen()
            self.notebook.write("setup_done")
            while True:
                if self.link.stop_requested:
                    raise TaskEnded("stopped", "Stopped as you asked.")
                if await self.wait_if_paused():
                    await self.read_screen()
                started, before, steps_before, decision = time.monotonic(), replace(self.timing), len(self.task.steps), None
                try:
                    self.check_budgets()
                    await self.wait_for_readable_screen()
                    await self.capture_screenshot_if_needed()
                    decision = await self.decide_next_action()
                    if decision is not None:
                        await self.execute(decision)
                except Stuck as stuck:
                    await self.ask_for_help(stuck)
                finally:
                    self.record_turn(decision, started, before, steps_before)
        except TaskEnded as end:
            self.finish_task(end)
            return end
        except PhoneDisconnected:
            self.notebook.write("status", to="interrupted", reason="the phone disconnected")
            self.save_snapshot()
            log.info("task %s: phone disconnected", self.task.id)
            return None
        finally:
            self.log_timing()

    async def screen_out_non_tasks(self) -> None:
        """Small talk and misheard speech get a quick spoken answer instead of costing a phone run."""
        started = time.monotonic()
        said = self.task.goal
        if self.task.earlier:
            # Follow-ups like "how much was it?" only make sense with what came before.
            said = "Earlier tasks:\n" + "\n".join(f"- {line}" for line in self.task.earlier) + f"\nNow the user said: {said}"
        likelihood, kind = await asyncio.gather(self.ai.phone_task_likelihood(said), self.ai.speech_kind(self.task.goal))
        self.timing.ai += time.monotonic() - started
        self.notebook.write("intake", phone_task=round(likelihood, 3), speech=kind,
                            ms=round((time.monotonic() - started) * 1000))
        # Both end as "stopped" so the orb closes and the mic doesn't reopen; noise ends silently.
        if kind == "noise":
            raise TaskEnded("stopped", "")
        if kind == "thanks":
            raise TaskEnded("stopped", "You're welcome!")
        if likelihood >= NOT_A_TASK_BELOW:
            return
        started = time.monotonic()
        try:
            answer = await self.ai.chat(said)
        except AIError:
            answer = ""
        self.timing.ai += time.monotonic() - started
        raise TaskEnded("needs_user", answer or f"I heard \"{self.task.goal}\" but not a task. Say it again?")

    async def decide_next_action(self):
        view = self.view
        retry = None
        rate_waits = list(RATE_LIMIT_WAITS)
        down_waits = list(UNAVAILABLE_WAITS)
        image = self.screenshot[1] if self.screenshot and self.screenshot[0] == view.signature else None
        message = self.step_message(view, image_attached=image is not None)
        while True:
            if self.task.model_calls >= self.model_call_limit:
                raise self.budget_exhausted("AI calls")
            self.task.model_calls += 1
            try:
                decision = await self.call_ai(message, image, retry)
            except PhoneDisconnected:
                raise
            except Interrupted:
                return None
            except RateLimited as error:
                if not rate_waits:
                    raise TaskEnded("failed", "The AI service kept saying 'too many requests'. Try again in a minute.")
                if not await self.sleep_interruptibly(error.retry_after or rate_waits[0]):
                    return None
                rate_waits.pop(0)
                continue
            except AIUnavailable as error:
                if not down_waits:
                    raise TaskEnded("failed", f"The AI service is not answering ({error.message}).")
                if not await self.sleep_interruptibly(down_waits.pop(0)):
                    return None
                continue
            except InvalidReply as error:
                self.record_invalid_reply(error.reason)
                retry = retry_note(error.reason)
                continue
            except AIError as error:
                raise TaskEnded(self.failure_outcome(), error.message)

            checked = self.validate_decision(decision.tool, decision.args, view)
            if isinstance(checked, str):
                self.record_invalid_reply(checked)
                retry = retry_note(checked)
                continue
            decision.args, note = checked
            self.task.consecutive_invalid_replies = 0
            self.notebook.write("decision", n=len(self.task.steps) + 1, tool=decision.tool,
                                args=self.redact_secrets(decision.args), model=decision.model, ms=decision.ms, note=note)
            log.debug("AI chose %s %s", decision.tool, self.redact_secrets(decision.args))
            return decision

    async def call_ai(self, message: str, image: bytes | None, retry: str | None):
        started = time.monotonic()
        decision, problem = None, ""
        if not self.link.pause_requested:
            await self.link.progress("running", "Thinking…", len(self.task.steps) + 1)
        try:
            decision = await self.link.run_interruptible(
                self.ai.decide(SYSTEM_PROMPT, message, image=image, retry_note=retry))
            return decision
        except BaseException as error:
            problem = getattr(error, "message", "") or type(error).__name__
            raise
        finally:
            elapsed = time.monotonic() - started
            self.timing.ai += elapsed
            self.timing.ai_calls += 1
            usage = {"model": decision.model, "input_tokens": decision.input_tokens,
                     "cached_tokens": decision.cached_tokens, "output_tokens": decision.output_tokens} if decision else {}
            self.notebook.write("ai_call", ms=round(elapsed * 1000), ok=decision is not None, problem=problem,
                                image=image is not None, retry=retry is not None, **usage)

    def validate_decision(self, tool: str, args: dict, view):
        if self.has_committed_action() and len(self.task.steps) >= self.step_limit and tool not in POST_COMMIT_TOOLS:
            return "only checking is left: scroll, wait, finish or give_up"
        if tool == "reply" and self.task.steps:
            return "reply is only for requests that aren't phone tasks; use finish or give_up"
        if tool == "tap_point":
            if not (self.screenshot and self.screenshot[0] == view.signature):
                return "tap_point needs a picture of this screen; use look first"
            if picture_to_screen(args["x"], args["y"], self.picture_size, view.screen) is None:
                return f"that spot is outside the picture, which is {self.picture_size[0]}x{self.picture_size[1]} pixels"
        checked = checks.check_decision(tool, args, view, self.link.apps)
        if isinstance(checked, str):
            return checked
        problem = safety.check_action(tool, checked[0], view)
        return problem or checked

    async def execute(self, decision) -> None:
        tool, args = decision.tool, decision.args
        self.task.add_note(args.get("remember"))
        why = args.get("why", "")[:100]
        if tool in STATUS_TOOLS:
            await self.link.progress("running", self.status_text(tool, args), len(self.task.steps) + 1)

        if tool in PHONE_TOOLS:
            await self.handle_phone_action(tool, args, why)
        elif tool in BLIND_TOOLS:
            await self.handle_blind_action(tool, args, why)
        elif tool == "wait":
            await self.handle_wait(args, why)
        elif tool == "look":
            await self.handle_look(why)
        elif tool == "ask_user":
            await self.handle_ask_user(args)
        elif tool == "confirm_and_tap":
            await self.handle_confirm_and_tap(args, why)
        elif tool == "request_secret":
            await self.handle_secret_entry(args)
        elif tool == "finish":
            await self.handle_finish(args)
        elif tool == "reply":
            raise TaskEnded("needs_user", args["text"])
        elif tool == "give_up":
            if self.task.rejected_finishes or self.task.uncertain_actions:
                await self.handle_finish({"summary": args["reason"], "proof": []})
            else:
                raise Stuck(args["reason"], self.failure_outcome(), args["reason"])
        self.save_snapshot()

    # ---------- actions ----------

    async def handle_phone_action(self, tool: str, args: dict, why: str) -> None:
        if not await self.allow_attempt(tool, args):
            return
        before = self.view
        target = args.get("target")
        label = before.by_index[target].label if target in before.by_index else ""
        if tool == "open_app":
            label = self.link.apps.get(args.get("package_name"), args.get("name", ""))
        step = self.start_step(tool, target, label, why)
        step.action_key = safety.action_key(tool, args, before)
        step.duplicate_sensitive = safety.duplicate_sensitive(tool, args, before)

        if tool == "tap" and safety.payment_requirement(before, target) == "unknown":
            details = safety.transaction_details(before)
            answer = await self.ask("question", f'Does "{label}" cost you money?', ["No cost", "Costs money"], QUESTION_EXPIRES)
            if answer is None:
                return
            if answer.text.casefold().strip(" .!?") not in {"no cost", "free", "no", "no charge", "it is free"}:
                self.warnings.append("Establish the charge, then use confirm_and_tap. Nothing was tapped.")
                return
            await self.read_screen()
            matches = [line for line in self.view.lines if line.label == label and line.role == before.by_index[target].role]
            if (self.view.screen.package_name != before.screen.package_name
                    or safety.transaction_details(self.view) != details or len(matches) != 1):
                self.warnings.append("The screen changed during the cost question; choose the action again.")
                return
            args = {**args, "target": matches[0].index}
            command = self.attempt_command(tool, args)
            command.confirmed = True  # phone also gates ambiguous subscription/booking labels
        else:
            command = self.attempt_command(tool, args)

        if tool == "type_text" and args.get("tap_first"):
            if not await self.focus_and_type(step, args):
                self.note_attempt(step, before)
                return
            self.note_attempt(step, before)
            self.record_step_outcome(step, before)
            return

        result = await self.send(command, step)
        if result is None:
            return
        unchanged = result.status == "ok" and screens.describe_change(before, self.view).startswith("no change")
        if unchanged and command.action == "tap" and not step.duplicate_sensitive and not command.confirmed:
            result = await self.touch_if_click_ignored(before, args["target"], step, result)
            if result is None:
                return
            unchanged = result.status == "ok" and screens.describe_change(before, self.view).startswith("no change")
        if result.status in {"failed", "unknown"} or unchanged:
            await self.observe_result(before)
        if tool == "type_text" and result.status == "ok":
            self.task.typed_texts.append(args.get("text", ""))
        self.note_attempt(step, before)
        self.record_step_outcome(step, before)

    async def handle_blind_action(self, tool: str, args: dict, why: str) -> None:
        """Acts by picture where the screen text shows nothing. The text can't show progress, so a fresh picture is taken."""
        before = self.view
        step = self.start_step(tool, None, (args.get("what") or "typed text")[:60], why)
        if tool == "tap_point":
            x, y = picture_to_screen(args["x"], args["y"], self.picture_size, before.screen)
            command = new_command("tap_at", x=x, y=y)
        else:
            command = new_command("type_focused", text=args["text"], submit=bool(args.get("submit")))
        result = await self.send(command, step)
        if result is None:
            return
        if tool == "type_focused" and result.status == "ok":
            self.task.typed_texts.append(args["text"])
        self.screenshot_requested = True
        step.change = "done; check the new picture" if result.status == "ok" else step.error or step.status
        log.info("step %d: %s %s → %s", step.n, step.tool, step.label, step.change)

    async def allow_attempt(self, tool: str, args: dict) -> bool:
        key = safety.action_key(tool, args, self.view)
        previous = self.attempts.get(key)
        if previous is None:
            return True
        if previous.user_verified:
            self.warnings.append("The user says this action already happened. Verify/finish; do not repeat it.")
            return False
        if not previous.duplicate_sensitive and previous.progressed:
            return True
        if previous.count >= 2:
            raise Stuck("That action has already been attempted twice; try another approach", self.failure_outcome(),
                        "Stopped retrying the same action.")
        if previous.duplicate_sensitive and previous.status not in {"rejected", "not sent"} and not previous.retry_authorized:
            if not previous.recovery_requested:
                if not await self.observe_result(self.view, force_image=True):
                    return False
                previous.recovery_requested = True
                self.warnings.append("Inspect the result and fresh image, if available, before repeating this effect. If still uncertain, the user must resolve whether to retry.")
                return False
            answer = await self.ask("question", "The earlier action may already have happened. Check its result before choosing Retry action.",
                                    ["Already done", "Retry action", "Not sure"], QUESTION_EXPIRES)
            if answer is None:
                return False
            reply = answer.text.casefold().strip(" .!?")
            if reply in {"already done", "done", "yes", "it worked"}:
                previous.user_verified = True
                self.warnings.append("The user confirmed the earlier action. Do not repeat it; check completion.")
                return False
            if reply != "retry action":
                raise TaskEnded("unconfirmed", "The earlier action may already have happened; it was not repeated.")
            await self.read_screen()
            # The caller must use a target from this observation, not the pre-question index.
            self.warnings.append("The user authorized one retry. Select the target on the fresh screen.")
            previous.retry_authorized = True
            return False
        return True

    def attempt_command(self, tool: str, args: dict):
        previous = self.attempts.get(safety.action_key(tool, args, self.view))
        if tool == "tap" and previous and previous.count and previous.status in {"ok", "failed", "unknown"}:
            # A coordinate fallback is a second execution, after observation and any duplicate check.
            return self.tap_at_center(self.view, args["target"])
        return self.command(tool, args)

    def tap_at_center(self, view, target: int) -> Command:
        left, top, right, bottom = view.by_index[target].node.bounds
        return new_command("tap_at", x=(left + right) // 2, y=(top + bottom) // 2)

    async def touch_if_click_ignored(self, before, target: int, step: Step, result):
        # Some apps (Amazon results) acknowledge an accessibility click and ignore it; a real touch works.
        if self.task.wait_seconds + 1 > MAX_WAIT_SECONDS:
            return result
        self.task.wait_seconds += 1
        if not await self.sleep_interruptibly(1):
            return result
        await self.read_screen()
        if before.signature != self.view.signature:
            return result
        return await self.send(self.tap_at_center(before, target), step)

    def note_attempt(self, step: Step, before) -> None:
        previous = self.attempts.get(step.action_key, Attempt())
        # A changed label counts: calculator keys and quantity buttons repeat on purpose.
        progressed = step.status == "ok" and screens.describe_change(before, self.view).startswith(
            ("new screen", "same screen; changed"))
        count = 1 if previous.progressed and not previous.duplicate_sensitive else previous.count + 1
        self.attempts[step.action_key] = Attempt(count, step.status, step.duplicate_sensitive, progressed)

    async def observe_result(self, before, *, force_image: bool = False) -> bool:
        for seconds in (1, 2):
            if self.task.wait_seconds + seconds > MAX_WAIT_SECONDS:
                break
            self.task.wait_seconds += seconds
            if not await self.sleep_interruptibly(seconds):
                return False
            await self.read_screen()
            if before.signature != self.view.signature:
                if force_image:
                    break
                return True
        await self.capture_screenshot("Checking the action result", force=True)
        return True

    async def focus_and_type(self, step: Step, args: dict) -> bool:
        result = await self.send(self.command("tap", args), step)
        if result is None or result.status != "ok":
            self.record_step_outcome(step, self.view)
            return False
        field = self.find_text_field()
        if field is None:
            step.status, step.change = "failed", "tapped it, but no text box appeared"
            self.warnings.append("Tapping that did not open a text box.")
            return False
        if safety.is_secret_field(self.view, field):
            step.status, step.change = "rejected", "This is a secret field; use request_secret instead."
            self.warnings.append(step.change)
            return False
        result = await self.send(self.command("type_text", {**args, "target": field}), step)
        if result is not None and result.status == "ok":
            self.task.typed_texts.append(args.get("text", ""))
        return result is not None

    async def wait_for_readable_screen(self) -> None:
        # A screen that stayed empty once is a blind app, not a loading one; rereading it every turn only adds 3 s.
        rereads = 0 if self.view.signature in self.blind_screens else EMPTY_SCREEN_REREADS
        for _ in range(rereads):
            if self.view.by_index:
                return
            if not await self.sleep_interruptibly(EMPTY_SCREEN_WAIT_SECONDS):
                return
            await self.read_screen()
        if self.view.by_index:
            return
        self.blind_screens.add(self.view.signature)
        if self.screenshots_taken < MAX_SCREENSHOTS and not self.secret_screens:
            self.warnings.append("The screen text shows nothing here. Use the picture with tap_point and type_focused.")
            return
        if self.view.signature in self.unreadable_screens:
            self.warnings.append("Nothing on this screen can be read or tapped. Use go_back, go_home, open_app or give_up.")
            return
        self.unreadable_screens.add(self.view.signature)
        raise Stuck("I can't read anything on this screen", self.failure_outcome(),
                    f"I couldn't read the screen. {self.last_tried()}".strip())

    async def capture_screenshot_if_needed(self) -> None:
        # A blind screen's text never changes, so its old picture can't be trusted after any step.
        fresh = self.screenshot_requested or is_blind(self.view)
        if self.view.signature in self.screenshotted_screens and not fresh:
            return
        if fresh or needs_screenshot(self.view):
            force = fresh
            self.screenshot_requested = False
            await self.capture_screenshot("Looking at the screen", force=force)

    async def handle_look(self, why: str) -> None:
        before = self.view
        step = self.start_step("look", None, "", why)
        step.status = "ok"
        step.change = "picture attached" if await self.capture_screenshot(why, force=True) else "no picture available"
        self.record_step_outcome(step, before)

    async def capture_screenshot(self, why: str, *, force: bool = False) -> bool:
        if force:
            self.screenshot = None
        if self.secret_screens or any(node.sensitive for node in self.screen.nodes):
            self.warnings.append("Screenshots are disabled after secret entry for this task; use redacted screen text or ask the user.")
            return False
        if self.view.signature in self.screenshotted_screens and not force:
            self.warnings.append("You already have the picture of this screen.")
            return False
        if self.screenshots_taken >= MAX_SCREENSHOTS:
            self.warnings.append("No more pictures for this task; use the screen text.")
            return False
        self.screenshots_taken += 1
        observation = self.view
        result = await self.send(new_command("screenshot", max_side=1280, screen_id=self.screen.screen_id), None)
        if result is None:
            return False
        if result.status != "ok" or result.image is None:
            code = result.error.code if result.error else ""
            if code == "screenshot_blocked":
                self.warnings.append("This screen can't be captured (it is protected). Don't guess; ask the user "
                                     "if needed.")
            else:
                self.warnings.append("The picture could not be taken; use the screen text.")
            return False
        started = time.monotonic()
        marked = annotate_screenshot(result.image, observation)
        log.debug("annotated the screenshot in %d ms", (time.monotonic() - started) * 1000)
        if isinstance(marked, str):
            self.warnings.append(f"The picture was unusable ({marked}); use the screen text.")
            return False
        marked, self.picture_size = marked
        self.screenshot = (observation.signature, marked)
        self.screenshotted_screens.add(observation.signature)
        self.notebook.write("screenshot", signature=self.view.signature, bytes=len(marked), why=why)
        return True

    async def handle_wait(self, args: dict, why: str) -> None:
        before = self.view
        seconds = args["seconds"]
        if self.task.wait_seconds + seconds > MAX_WAIT_SECONDS:
            self.warnings.append("No more waiting; the screen is not changing. Try something else or give up.")
            return
        step = self.start_step("wait", None, f"{seconds} s", why)
        self.task.wait_seconds += seconds
        if not await self.sleep_interruptibly(seconds):
            return
        await self.read_screen()
        step.status = "waited"
        self.record_step_outcome(step, before)

    async def handle_ask_user(self, args: dict) -> None:
        question = args["question"]
        if safety.asks_for_secret(question):
            fields = [line for line in self.view.lines if line.node.editable and safety.is_secret_field(self.view, line.index)]
            if len(fields) == 1:
                await self.handle_secret_entry({"target": fields[0].index})
            else:
                outcome = await self.ask("secret", "Enter the secret directly on the phone, then reply done.", None, QUESTION_EXPIRES)
                if outcome is None:
                    return
                if outcome.status == "expired":
                    raise TaskEnded("needs_user", "Secret entry needs your input on the phone.")
                if outcome.text != "done":
                    self.warnings.append("The user has not completed manual secret entry.")
                    return
                self.secret_screens = True
                self.screenshot = None
                await self.read_screen()
            return
        step = self.start_step("ask_user", None, question, "")
        outcome = await self.ask("question", question, args.get("choices"), QUESTION_EXPIRES)
        if outcome is None:
            step.status = "cancelled"
            return
        if outcome.status == "expired":
            raise TaskEnded("needs_user", f"I asked \"{question}\" and got no answer.")
        answer = outcome.text or ("yes" if outcome.status == "approved" else "no")
        if safety.is_secret(answer, question):
            answer = "(the user handled it on the phone)"
        self.task.answers.append((question, answer))
        step.status, step.change = "answered", f"user said: {answer}"[:120]
        await self.read_screen()

    async def handle_secret_entry(self, args: dict) -> None:
        target = args["target"]
        if not safety.is_secret_field(self.view, target) or not self.view.by_index[target].node.editable:
            self.warnings.append("request_secret needs a visible editable secret field. Focus the field first.")
            return
        before = self.view
        node = before.by_index[target].node
        step = self.start_step("request_secret", target, "secret field", "")
        supported = "spokenSecretsV1" in self.link.capabilities
        binding = SecretTarget(package_name=before.screen.package_name, screen_id=before.screen.screen_id, target=target)
        prompt = ("Speak the value for this field, or enter it directly on the phone and say done."
                  if supported else "Enter the secret directly on the phone, then reply done. Voice entry requires an updated Android client.")
        outcome = await self.ask("secret", prompt, None, QUESTION_EXPIRES, secret_target=binding if supported else None)
        if outcome is None:
            step.status = "cancelled"
            return
        if outcome.status == "expired":
            raise TaskEnded("needs_user", "Secret entry needs your input on the phone.")
        if outcome.status == "secret" and supported:
            await self.read_screen()
            fields = [line for line in self.view.lines if line.node.editable and safety.is_secret_field(self.view, line.index)
                      and line.node.resource_id == node.resource_id and line.node.class_name == node.class_name
                      and (node.resource_id or line.node.bounds == node.bounds)]
            if self.screen.package_name != before.screen.package_name or len(fields) != 1:
                step.status, step.change = "blocked", "Secret target changed; request a new value for the current field."
                self.warnings.append(step.change)
                return
            self.secret_screens = True
            self.screenshot = None
            result = await self.send(new_command("type_text", screen_id=self.screen.screen_id,
                                                target=fields[0].index, secret_ref=outcome.secret_ref), step)
            if result is not None:
                step.change = "User-supplied secret entered" if result.status == "ok" else "Secret entry failed; request a new value or use manual entry."
        elif outcome.text == "done":
            step.status, step.change = "ok", "User handled secret entry on the phone"
            self.secret_screens = True
            self.screenshot = None
            await self.read_screen()
        else:
            step.status, step.change = "not sent", "Secret was not entered; use manual entry or request a new value."
            self.warnings.append(step.change)
        self.task.answers.append(("Secret entry", step.change))

    async def handle_confirm_and_tap(self, args: dict, why: str) -> None:
        if safety.payment_requirement(self.view, args["target"]) is None:
            await self.handle_phone_action("tap", args, why)
            return
        if not await self.allow_attempt("confirm_and_tap", args):
            return
        before = self.view
        plan = safety.plan_confirm(before, args, self.task.uncertain_actions)
        if isinstance(plan, str):
            self.record_invalid_reply(plan)
            self.warnings.append(plan)
            return
        step = self.start_step("confirm_and_tap", args["target"], plan.label, why)
        step.irreversible = True
        step.duplicate_sensitive = True
        step.action_key = safety.action_key("confirm_and_tap", args, before)
        approval_started = time.monotonic()
        outcome = await self.ask("confirm", plan.text, ["Yes", "No"], CONFIRM_EXPIRES)
        if outcome is None:
            step.status = "cancelled"
            return
        if outcome.status == "text" and await self.reply_approves(plan.text, outcome.text):
            outcome = replace(outcome, status="approved")
        if time.monotonic() - approval_started > CONFIRM_EXPIRES:
            step.status = "expired"
            self.warnings.append("Payment approval expired; ask again. Nothing was tapped.")
            return
        if outcome.status in ("expired", "declined"):
            step.status, step.change = "declined", "the user said no; nothing was tapped"
            self.task.answers.append((f"Approve: {args['summary']}", "No"))
            return
        if outcome.status != "approved":
            step.status, step.change = "declined", "the user answered with a change instead"
            if outcome.text:
                self.task.corrections.append(outcome.text)
                self.warnings.append(f"The user changed the request: {outcome.text}")
            return

        await self.read_screen()
        target = safety.recheck_confirmation(plan, self.view)
        if isinstance(target, str):
            step.status, step.change = "blocked", target
            self.warnings.append(target)
            return
        command = self.attempt_command("tap", {"target": target})
        command.confirmed = True
        result = await self.send(command, step, confirmed=True)
        if result is None:
            return
        total = safety.screen_total(before)
        if result.status == "ok":
            self.task.completed_irreversible.append(f'approved and tapped "{plan.label}"' + (f", total {total}" if total else ""))
        elif result.status == "unknown":
            self.task.uncertain_actions.append(f'tapped "{plan.label}" but the result was lost')
        if result.status != "rejected":
            await self.observe_result(before)
        self.note_attempt(step, before)
        self.record_step_outcome(step, before)

    async def reply_approves(self, question: str, reply: str) -> bool:
        """Lets the AI judge a free-form reply like "Yes, send it"; any doubt or error means not approved."""
        started = time.monotonic()
        approved = False
        if self.task.model_calls >= self.model_call_limit:
            return False
        self.task.model_calls += 1
        try:
            approved = await self.link.run_interruptible(self.ai.approves(question, reply))
        except PhoneDisconnected:
            raise
        except (Interrupted, AIError) as error:
            log.info("task %s: approval check failed: %s", self.task.id, getattr(error, "message", "") or type(error).__name__)
        finally:
            self.timing.ai += time.monotonic() - started
            self.timing.ai_calls += 1
            self.notebook.write("approval_check", reply=safety.hide_secrets(reply, question), approved=approved)
        return approved

    async def ask_for_help(self, stuck: Stuck) -> None:
        """Ask the user how to get unstuck. Their answer steers the next steps; Stop or silence ends the task."""
        if self.help_asks >= MAX_HELP_ASKS:
            raise TaskEnded(stuck.outcome, stuck.summary)
        self.help_asks += 1
        question = f"I'm stuck. {stuck.reason.strip().rstrip('.')}. What should I do?"
        self.notebook.write("stuck", reason=stuck.reason, ask=self.help_asks)
        log.info("task %s stuck: %s", self.task.id, stuck.reason)
        step = self.start_step("ask_user", None, question, "")
        outcome = await self.ask("question", question, HELP_CHOICES, QUESTION_EXPIRES)
        if outcome is None:
            step.status = "cancelled"
            return
        if outcome.status == "expired":
            raise TaskEnded("needs_user", f"{stuck.summary} I asked what to do and got no answer.")
        answer = (outcome.text or ("yes" if outcome.status == "approved" else "no")).strip()
        spoken = answer.casefold().rstrip(".!")
        if spoken in HELP_STOP_WORDS:
            raise TaskEnded("stopped", "Okay, stopping.")
        self.task.answers.append((question, answer))
        step.status, step.change = "answered", f"user said: {answer}"[:120]
        if spoken in HELP_KEEP_GOING_WORDS:
            self.warnings.append(f"You were stuck ({stuck.reason}). The user said keep trying. Try a different way.")
        else:
            self.task.corrections.append(answer)
            self.warnings.append(f"You were stuck ({stuck.reason}). Follow the user's advice: {answer}")
        self.screen_history.clear()
        # The user has weighed in, so blocked retries get a fresh count; uncertain money/send effects keep theirs.
        self.attempts = {key: attempt for key, attempt in self.attempts.items() if attempt.duplicate_sensitive}
        self.step_limit = max(self.step_limit, len(self.task.steps) + HELP_EXTRA_STEPS)
        self.model_call_limit = max(self.model_call_limit, self.task.model_calls + HELP_EXTRA_MODEL_CALLS)
        await self.read_screen()

    def status_text(self, tool: str, args: dict) -> str:
        target = args.get("target")
        label = self.view.by_index[target].label if target in self.view.by_index else ""
        if tool == "open_app":
            label = self.link.apps.get(args.get("package_name"), args.get("name", ""))
        why = args.get("why", "")
        if safety.is_secret(why) or (tool == "type_text" and safety.is_secret(args.get("text"))):
            why = ""
        return status_line(tool, why, label, args.get("direction"))

    async def handle_finish(self, args: dict) -> None:
        # Check the screen the AI saw; re-reading loses short-lived proof like a "Sent · Undo" snackbar.
        committed = [step for step in self.task.steps if step.irreversible]
        problem = checks.check_finish(
            args, self.view, goal=self.task.goal, corrections=self.task.corrections, typed=self.task.typed_texts,
            app_label=self.link.apps.get(self.screen.package_name, ""), committed_steps=committed)
        if problem is None:
            self.task.completion_evidence = "screen"
            raise TaskEnded("completed", args["summary"], args["proof"])
        observation_changed = self.verification_signature != self.view.signature
        self.task.rejected_finishes += 1
        if self.task.rejected_finishes == 1 or observation_changed:
            if not await self.observe_result(self.view, force_image=True):
                self.task.rejected_finishes -= 1
                return
            self.verification_signature = self.view.signature
            self.warnings.append(problem)
            self.warnings.append("Completion is not established. Inspect the fresh observation/image and check relevant result evidence without repeating the action. Try finish again or ask for help.")
            return
        # The summary, not the goal: the user may have redirected the task mid-way.
        question = f"I couldn't confirm it on screen. {args['summary'].strip().rstrip('.')}. Did that work?"
        # Here a thank-you means yes; handled below.
        answer = await self.ask("question", question, ["Yes", "No", "Not sure"], QUESTION_EXPIRES, end_on_thanks=False)
        if answer is None:
            return
        started = time.monotonic()
        confirmed = await self.ai.says_done(question, answer.text)
        self.timing.ai += time.monotonic() - started
        if confirmed is None:
            confirmed = yes_no(answer.text)
        if confirmed is None and answer.status == "text" and answer.text:
            confirmed = await self.ai.speech_kind(answer.text) == "thanks" or None
        self.notebook.write("completion_check", reply=answer.text, done=confirmed)
        if confirmed is True:
            self.task.completion_evidence = "user"
            raise TaskEnded("completed", "User confirmed: " + args["summary"])
        if confirmed is False:
            self.task.rejected_finishes = 0
            self.task.corrections.append("The user says the task is not complete. Check the result and recover; do not blindly repeat an effect.")
            await self.read_screen()
            return
        reply = (answer.text or "").strip()
        if answer.status == "text" and reply.casefold().rstrip(".!?") not in UNSURE_REPLIES:
            # Neither yes nor no ("It finished, but you were meant to add 2+2"): it's an instruction.
            self.task.rejected_finishes = 0
            self.task.corrections.append(reply)
            self.warnings.append(f"Asked whether it worked, the user said: {reply}. Follow that.")
            await self.read_screen()
            return
        raise TaskEnded("unconfirmed", "The result could not be verified, and the user did not confirm completion.")

    # ---------- talking to the phone ----------

    def command(self, action: str, args: dict) -> Command:
        if action == "scroll" and args.get("target") is None:
            return self.swipe_command(args.get("direction") or "down")
        targeted = action in ("tap", "type_text", "scroll")
        return new_command(
            action,
            screen_id=self.screen.screen_id if targeted else None,
            target=args.get("target") if targeted else None,
            text=args.get("text") if action == "type_text" else None,
            submit=bool(args.get("submit")) if action == "type_text" else None,
            direction=args.get("direction") if action == "scroll" else None,
            package_name=args.get("package_name") if action == "open_app" else None,
        )

    def swipe_command(self, direction: str) -> Command:
        width, height = self.screen.width, self.screen.height
        near, far = 0.3, 0.7
        start, end = {
            "down": ((0.5, far), (0.5, near)), "up": ((0.5, near), (0.5, far)),
            "right": ((far, 0.5), (near, 0.5)), "left": ((near, 0.5), (far, 0.5)),
        }[direction]
        return new_command("swipe", x=int(start[0] * width), y=int(start[1] * height),
                           x2=int(end[0] * width), y2=int(end[1] * height))

    async def send(self, command: Command, step: Step | None, confirmed: bool = False):
        n = step.n if step else None
        self.notebook.write("step_sending", n=n, command_id=command.command_id, action=command.action,
                            target=command.target, label=step.label if step else "", why=step.why if step else "",
                            confirmed=confirmed or bool(command.confirmed))
        started = time.monotonic()
        try:
            result = await self.link.run(command)
        except PhoneDisconnected:
            raise
        except Interrupted:
            self.notebook.write("step_result", n=n, command_id=command.command_id, status="not_sent")
            if step:
                step.status = "not sent"
            return None
        finally:
            elapsed = time.monotonic() - started
            self.timing.phone += elapsed
            self.timing.commands += 1
        error = result.error.code if result.error else ""
        if command.action == "type_text" and command.submit and error == "cant_submit":
            self.warnings.append(
                "The text was entered but the search was not submitted. Do not retype it. "
                "Use a visible Search/Go button or a suggestion matching the query on the fresh screen; "
                "if none is available, ask the user to press Search on the keyboard."
            )
        self.notebook.write("step_result", n=n, command_id=command.command_id, status=result.status, error=error,
                            ms=round(elapsed * 1000))
        if step:
            step.status = result.status
            if step.duplicate_sensitive and result.status in {"ok", "failed", "unknown"}:
                note = f'"{step.label}" may already have happened; inspect before retrying'
                if note not in self.task.uncertain_actions:
                    self.task.uncertain_actions.append(note)
            if result.status != "ok":
                step.error = describe_error(error, result.error.message if result.error else "")
        if result.screen is not None:
            self.set_screen(result.screen)
        elif command.action not in {"read_screen", "screenshot"}:
            await self.read_screen()
        if result.status == "unknown" and step and not step.irreversible:
            self.warnings.append(f'The phone did not confirm "{step.label}". Check the screen before repeating it.')
        return result

    async def read_screen(self) -> None:
        await self.wait_if_paused()  # a read during a pause would be stale by the time we resume
        for attempt in range(3):
            if attempt:
                await self.sleep_interruptibly(attempt)  # dialogs and app switches briefly leave no readable window
            result = await self.send(new_command("read_screen"), None)
            if result is not None and result.screen is not None:
                return
        raise TaskEnded(self.failure_outcome(), "The phone did not send a fresh screen.")

    def set_screen(self, screen) -> None:
        started = time.monotonic()
        screen = safety.redact_screen(screen)
        self.screen = screen
        self.view = screens.render(screen, self.link.apps)
        self.task.record_screen(self.view.app)
        log.debug("rendered %d nodes into %d lines in %d ms", len(screen.nodes), len(self.view.lines),
                  (time.monotonic() - started) * 1000)

    async def ask(self, kind: str, text: str, choices, expires_in: int, *, secret_target=None, while_paused: bool = False,
                  end_on_thanks: bool = True):
        self.notebook.write("ask", kind=kind, text=text)
        started = time.monotonic()
        try:
            outcome = await self.link.ask(kind, text, choices, expires_in, secret_target=secret_target,
                                          while_paused=while_paused)
        finally:
            self.timing.user += time.monotonic() - started
        if outcome.status == "cancelled":
            return None
        shown = safety.hide_secrets(outcome.text, text) if outcome.text else None
        self.notebook.write("answer", ask_id=outcome.ask_id, status=outcome.status, text=shown)
        if end_on_thanks and kind != "secret" and outcome.status == "text" and outcome.text:
            started = time.monotonic()
            thanked = await self.ai.speech_kind(outcome.text) == "thanks"
            self.timing.ai += time.monotonic() - started
            if thanked:
                raise TaskEnded("stopped", "You're welcome!")
        return outcome

    async def wait_if_paused(self) -> bool:
        """Hold the task while the user has it paused. True if it was paused; the screen must be read again."""
        if not self.link.pause_requested:
            return False
        await self.link.progress("paused", "Paused", len(self.task.steps))
        self.notebook.write("status", to="paused", reason="the user paused")
        log.info("task %s paused", self.task.id)
        await self.take_request_while_paused()
        started = time.monotonic()
        try:
            await self.link.wait_while_paused()
        finally:
            self.timing.user += time.monotonic() - started
        if self.link.stop_requested:
            raise TaskEnded("stopped", "Stopped as you asked.")
        self.notebook.write("status", to="running", reason="the user resumed")
        log.info("task %s resumed", self.task.id)
        await self.link.progress("running", "Resuming", len(self.task.steps))
        return True

    async def take_request_while_paused(self) -> None:
        """Pausing asks what else the user wants; Jev reads the reply as resume, stop or a change to the task."""
        answer = await self.ask("question", PAUSE_QUESTION, None, PAUSE_ANSWER_SECONDS, while_paused=True,
                                end_on_thanks=False)
        if answer and answer.status == "expired":
            raise TaskEnded("stopped", "No worries, I'll stop here.")
        reply = (answer.text or "").strip() if answer and answer.status == "text" else ""
        if not reply:
            return
        started = time.monotonic()
        choice = await self.ai.pause_reply(PAUSE_QUESTION, reply)
        self.timing.ai += time.monotonic() - started
        self.notebook.write("pause_reply", reply=reply, choice=choice)
        if choice == "stop":
            raise TaskEnded("stopped", "Okay, stopping.")
        if choice == "noise":
            raise TaskEnded("stopped", "No worries, I'll stop here.")
        if choice == "thanks":
            raise TaskEnded("stopped", "You're welcome!")
        if choice != "resume":
            self.task.corrections.append(reply)
            self.warnings.append(f"While paused, the user said: {reply}. Follow that.")
        self.link.pause_requested = False

    async def sleep_interruptibly(self, seconds: float) -> bool:
        try:
            await self.link.run_interruptible(asyncio.sleep(seconds))
            return True
        except PhoneDisconnected:
            raise
        except Interrupted:
            return False

    # ---------- budgets and ending ----------

    def check_budgets(self) -> None:
        limit = self.step_limit + (EXTRA_STEPS_AFTER_COMMIT if self.has_committed_action() else 0)
        if len(self.task.steps) >= limit:
            raise self.budget_exhausted("steps")
        if (time.monotonic() - self.started - self.timing.user) / 60 > MAX_WORK_MINUTES:
            raise TaskEnded(self.failure_outcome(), f"I ran out of time. {self.last_tried()}".strip())
        if self.task.invalid_replies >= MAX_INVALID_REPLIES:
            raise TaskEnded(self.failure_outcome(), "The AI kept giving answers I couldn't use.")

    def budget_exhausted(self, what: str) -> Stuck:
        return Stuck(f"This is taking too many {what}.", self.failure_outcome(),
                     f"I ran out of {what}. {self.last_tried()}".strip())

    def last_tried(self) -> str:
        steps = [s for s in self.task.steps if s.tool != "ask_user"]
        return f"Last step: {steps[-1].tool} {steps[-1].label}".strip() + "." if steps else ""

    def record_invalid_reply(self, reason: str) -> None:
        self.task.consecutive_invalid_replies += 1
        self.task.invalid_replies += 1
        self.notebook.write("invalid_reply", reason=reason)
        log.info("task %s: unusable AI reply: %s", self.task.id, reason)
        if self.task.consecutive_invalid_replies >= MAX_CONSECUTIVE_INVALID_REPLIES:
            raise TaskEnded(self.failure_outcome(), f"The AI kept giving answers I couldn't use (last: {reason}).")

    def failure_outcome(self) -> str:
        return "unconfirmed" if self.task.uncertain_actions else "failed"

    def has_committed_action(self) -> bool:
        return any(step.irreversible and step.status in ("ok", "unknown") for step in self.task.steps)

    def finish_task(self, end: TaskEnded) -> None:
        if self.task.uncertain_actions and end.outcome in ("stopped", "failed", "unconfirmed"):
            end.summary += " Heads-up: " + "; ".join(self.task.uncertain_actions) + "."
        end.summary = end.summary[:600]
        self.notebook.write("done", outcome=end.outcome, summary=end.summary, proof=end.proof)
        self.save_snapshot()
        log.info("task %s ended: %s — %s", self.task.id, end.outcome, end.summary)

    def record_turn(self, decision, started: float, before: Timing, steps_before: int) -> None:
        total = time.monotonic() - started
        ai, phone, user = self.timing.ai - before.ai, self.timing.phone - before.phone, self.timing.user - before.user
        ai_calls, commands = self.timing.ai_calls - before.ai_calls, self.timing.commands - before.commands
        step = self.task.steps[-1] if len(self.task.steps) > steps_before else None
        self.notebook.write("turn", tool=decision.tool if decision else None, total=round(total, 3), ai=round(ai, 3),
                            phone=round(phone, 3), user=round(user, 3), ai_calls=ai_calls, commands=commands,
                            step=asdict(step) if step else None)
        log.debug("turn %s: %.2f s = AI %.2f s (%d calls) + phone %.2f s (%d commands) + user %.2f s + other %.2f s",
                  decision.tool if decision else "-", total, ai, ai_calls, phone, commands, user,
                  total - ai - phone - user)

    def log_timing(self) -> None:
        timing = self.timing
        total = time.monotonic() - self.started
        self.notebook.write("timing", total=round(total, 2), steps=len(self.task.steps),
                            **{k: round(v, 2) for k, v in asdict(timing).items()})
        log.info("task %s took %.1f s over %d steps: AI %.1f s (%d calls, avg %.1f s), "
                 "phone %.1f s (%d commands, avg %d ms), waiting on you %.1f s",
                 self.task.id, total, len(self.task.steps), timing.ai, timing.ai_calls,
                 timing.ai / max(1, timing.ai_calls), timing.phone, timing.commands,
                 timing.phone * 1000 / max(1, timing.commands), timing.user)

    # ---------- learning from each step ----------

    def start_step(self, tool: str, target, label: str, why: str) -> Step:
        step = Step(n=len(self.task.steps) + 1, tool=tool, target=target, label=label, why=why,
                    signature=self.view.signature)
        self.task.steps.append(step)
        return step

    def record_step_outcome(self, step: Step, before) -> None:
        after = self.view
        if step.status == "ok" or step.status == "waited":
            step.change = step.change or screens.describe_change(before, after)
        elif not step.change:
            step.change = step.error or step.status
        if step.change.startswith("no change") and step.tool != "wait":
            if step.tool == "scroll":
                self.warnings.append("That list is at its end.")
            else:
                self.warnings.append("No visible change. Delayed observation/recovery applies before a bounded retry.")
        self.consecutive_scrolls = self.consecutive_scrolls + 1 if step.tool == "scroll" and before.signature != after.signature else 0
        if self.consecutive_scrolls >= 5:
            self.warnings.append("Lots of scrolling. Try the search box.")
        self.screen_history.append(after.signature)
        recent = self.screen_history[-10:]
        if len(self.screen_history) >= 10 and len(set(recent)) <= 2:
            raise Stuck("I keep landing on the same screens.", self.failure_outcome(),
                        f"I got stuck going in circles. {self.last_tried()}".strip())
        if len(self.screen_history) >= 6 and len(set(self.screen_history[-6:])) <= 2:
            apps = " and ".join(dict.fromkeys(self.task.screens_seen[-2:])) or "the same screens"
            self.warnings.append(f"You are going back and forth in {apps}. Try a different way.")
            if after.signature not in self.screenshotted_screens:
                self.screenshot_requested = True
        log.info("step %d: %s %s → %s", step.n, step.tool, step.label, step.change)

    def find_text_field(self) -> int | None:
        fields = [line for line in self.view.lines if line.node.editable]
        return fields[0].index if len(fields) == 1 else None

    def step_message(self, view, image_attached: bool = False) -> str:
        steps = self.task.steps
        first = not steps
        apps_line = ", ".join(sorted(self.link.apps.values())) if first or self.last_open_app_failed() else None
        return build_step_message(
            goal=self.task.goal, earlier=self.task.earlier, corrections=self.task.corrections, answers=self.task.answers,
            completed_irreversible=self.task.completed_irreversible, uncertain_actions=self.task.uncertain_actions, notes=self.task.notes,
            apps_line=apps_line, screens_seen=self.task.screens_seen, steps=steps[-8:],
            total_steps=len(steps), step_limit=self.step_limit, warnings=self.drain_warnings(),
            view_text=view.text, image_attached=image_attached, picture_size=self.picture_size)

    def drain_warnings(self) -> list[str]:
        warnings = list(dict.fromkeys(self.warnings))[-3:]
        self.warnings.clear()
        return warnings

    def last_open_app_failed(self) -> bool:
        last = self.task.steps[-1] if self.task.steps else None
        return bool(last and last.tool == "open_app" and last.status != "ok")

    def handle_late_result(self, result) -> None:
        self.notebook.write("late_result", command_id=result.command_id, status=result.status)
        if result.status == "ok" and self.task.uncertain_actions:
            self.warnings.append("The phone later confirmed the action whose result was lost.")

    def redact_secrets(self, args: dict) -> dict:
        shown = dict(args)
        if "text" in shown and safety.is_secret(str(shown["text"])):
            shown["text"] = "[hidden]"
        return shown

    def save_snapshot(self) -> None:
        self.notebook.write("state", task=self.task.snapshot())
