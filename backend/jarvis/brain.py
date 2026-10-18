import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jarvis import checks, safety
from jarvis import screen as screens
from jarvis.llm.common import AIUnavailable, AuthProblem, BadReply, QuotaUsedUp, RateLimited, SetupProblem
from jarvis.llm.tools import tools_for
from jarvis.messages import Command, Done, LastAction, Ready, new_id
from jarvis.phone import Interrupted, PhoneGone
from jarvis.pictures import marked_picture, needs_picture
from jarvis.prompt import SYSTEM_PROMPT, build_step_message, plain_error, retry_note
from jarvis.task import Notebook, Step, Task, load_task

log = logging.getLogger("jarvis.brain")

TIMEOUTS_MS = {"open_app": 15_000, "screenshot": 8_000, "read_screen": 5_000}
PHONE_TOOLS = {"tap", "type_text", "scroll", "go_back", "go_home", "open_app"}
AFTER_COMMIT_TOOLS = {"scroll", "wait", "finish", "give_up", "look"}
REJOIN_MINUTES = 30
MAX_PICTURES = 8


@dataclass
class Limits:
    max_steps: int = 50
    max_model_calls: int = 80
    max_work_minutes: float = 15
    max_bad_in_row: int = 3
    max_bad_total: int = 8
    max_wait_seconds: int = 90
    question_expires: int = 600
    confirm_expires: int = 120
    extra_steps_after_commit: int = 3
    loading_retries: int = 2
    loading_pause: float = 1.5
    rate_limit_waits: tuple = (5, 10, 20)
    unavailable_waits: tuple = (2, 5)


class TaskEnded(Exception):
    def __init__(self, outcome: str, summary: str, proof: list[str] | None = None):
        super().__init__(summary)
        self.outcome = outcome
        self.summary = summary
        self.proof = proof


async def run_task(task: Task, link, ai, notebook: Notebook, limits: Limits | None = None) -> Done | None:
    return await Brain(task, link, ai, notebook, limits or Limits()).run()


def brain_runner(ai, limits: Limits | None = None):
    """Adapts the server's Run record to run_task, for new tasks and for a phone rejoining one."""
    async def run(server_run, rejoin=None) -> Done | None:
        data_dir = server_run.settings.data_dir
        if rejoin is None:
            task = Task(goal=server_run.goal, device_id=server_run.device_id, id=server_run.task_id,
                        rejoin_key=server_run.rejoin_key)
            notebook = Notebook.for_task(data_dir, task.id)
            notebook.write("task", task_id=task.id, goal=task.goal, device_id=task.device_id,
                           ai=getattr(ai, "name", ""))
            return await run_task(task, server_run.link, ai, notebook, limits)

        loaded = load_task(data_dir, rejoin.task_id)
        problem = rejoin_problem(loaded, server_run.device_id, rejoin)
        if problem:
            await server_run.link.fail(*problem, task_id=rejoin.task_id)
            return None
        task, events = loaded
        notebook = Notebook.for_task(data_dir, task.id)
        settle_open_commands(task, events, rejoin.last_result, notebook)
        server_run.goal = task.goal
        server_run.link.revision = task.revision
        await server_run.link.send(Ready(task_id=task.id, rejoin_key=task.rejoin_key, revision=task.revision,
                                         rejoined=True))
        notebook.write("status", to="running", reason="the phone rejoined")
        return await run_task(task, server_run.link, ai, notebook, limits)
    return run


def rejoin_problem(loaded, device_id: str, rejoin) -> tuple[str, str] | None:
    if loaded is None:
        return "task_not_found", "That task is not known here."
    task, events = loaded
    if task.device_id != device_id or task.rejoin_key != rejoin.rejoin_key:
        return "task_not_found", "That task belongs to another phone."
    done = next((e for e in events if e["event"] == "done"), None)
    if done:
        return "task_finished", f"That task already ended: {done.get('summary', '')}"
    last = datetime.fromisoformat(events[-1]["at"])
    if datetime.now(timezone.utc) - last > timedelta(minutes=REJOIN_MINUTES):
        return "task_not_found", f"That task was cut off more than {REJOIN_MINUTES} minutes ago."
    return None


def settle_open_commands(task: Task, events: list[dict], last_result, notebook: Notebook) -> None:
    """Works out what happened to commands that were sent but never answered before the connection dropped."""
    answered = {e.get("command_id"): e.get("status") for e in events if e["event"] in ("step_result", "late_result")}
    for event in events:
        if event["event"] != "step_sending" or event.get("command_id") in answered:
            continue
        if last_result and last_result.command_id == event["command_id"]:
            status = last_result.status
        elif last_result:
            status = "not sent"  # the phone's last command is a different one, so this one never arrived
        else:
            status = "unknown"
        answered[event["command_id"]] = status
        notebook.write("step_result", n=event.get("n"), command_id=event["command_id"], status=status,
                       change="settled when the phone rejoined")
    for event in events:
        if event["event"] != "step_sending" or not event.get("n"):
            continue
        n = event["n"]
        if n > len(task.steps):
            continue
        step = task.steps[n - 1]
        status = answered.get(event["command_id"], step.status)
        if step.status in ("", "unknown"):
            step.status = status
        if step.irreversible and event.get("confirmed"):
            if status == "ok" and not any(step.label in line for line in task.done_for_good):
                task.done_for_good.append(f'approved and tapped "{step.label}"')
            elif status == "unknown" and not any(step.label in line for line in task.unsure):
                task.unsure.append(f'tapped "{step.label}" but the result was lost')


class Brain:
    def __init__(self, task: Task, link, ai, notebook: Notebook, limits: Limits):
        self.task = task
        self.link = link
        self.ai = ai
        self.notebook = notebook
        self.limits = limits
        self.screen = None
        self.view = None
        self.settled = True
        self.warnings: list[str] = []
        self.dead_ends: set[tuple] = set()
        self.signatures: list[str] = []
        self.same_way_scrolls = 0
        self.started = time.monotonic()
        self.user_time = 0.0
        self.images = getattr(ai, "sees_images", False) and "screenshot" in link.actions
        self.picture: tuple[str, bytes] | None = None
        self.pictured: set[str] = set()
        self.pictures_taken = 0
        self.want_picture = False
        self.offered = {tool.name for tool in tools_for(link.actions, self.images)}
        link.on_late_result = self.late_result

    # ---------- the loop ----------

    async def run(self) -> Done | None:
        try:
            await self.progress("running", "Starting")
            await self.read_screen()
            while True:
                await self.handle_interrupts()
                self.check_budgets()
                await self.wait_while_loading()
                await self.picture_if_needed()
                decision = await self.think()
                if decision is not None:
                    await self.act(decision)
        except TaskEnded as end:
            return await self.end(end.outcome, end.summary, end.proof)
        except PhoneGone:
            self.task.status = "interrupted"
            self.notebook.write("status", to="interrupted", reason="the phone disconnected")
            self.save()
            log.info("task %s: phone disconnected", self.task.id)
            return None

    async def think(self):
        view = self.view
        retry = None
        rate_waits = list(self.limits.rate_limit_waits)
        down_waits = list(self.limits.unavailable_waits)
        revision = self.link.revision
        image = self.picture[1] if self.picture and self.picture[0] == view.signature else None
        message = self.step_message(view, image_attached=image is not None)
        while True:
            if self.task.model_calls >= self.limits.max_model_calls:
                raise self.out_of("AI calls")
            self.task.model_calls += 1
            try:
                decision = await self.link.interruptible(
                    self.ai.decide(SYSTEM_PROMPT, message, tools_for(self.link.actions, self.images),
                                   image=image, retry_note=retry))
            except PhoneGone:
                raise
            except Interrupted:
                return None
            except RateLimited as error:
                if not rate_waits:
                    raise TaskEnded("failed", "The AI service kept saying 'too many requests'. Try again in a minute.")
                if not await self.pause_for(error.retry_after or rate_waits[0]):
                    return None
                rate_waits.pop(0)
                continue
            except AIUnavailable as error:
                if not down_waits:
                    raise TaskEnded("failed", f"The AI service is not answering ({error.message}).")
                if not await self.pause_for(down_waits.pop(0)):
                    return None
                continue
            except (QuotaUsedUp, AuthProblem, SetupProblem) as error:
                raise TaskEnded(self.failed_or_unsure(), error.message)
            except BadReply as error:
                self.bad_reply(error.reason)
                retry = retry_note(error.reason)
                continue

            if self.link.revision != revision:
                return None
            checked = self.validate(decision.tool, decision.args or {}, view)
            if isinstance(checked, str):
                self.bad_reply(checked)
                retry = retry_note(checked)
                continue
            decision.args, note = checked
            self.task.bad_in_row = 0
            self.notebook.write("decision", n=len(self.task.steps) + 1, tool=decision.tool,
                                args=self.loggable(decision.args), model=decision.model, ms=decision.ms, note=note)
            return decision

    def validate(self, tool: str, args: dict, view):
        if self.committed() and len(self.task.steps) >= self.limits.max_steps and tool not in AFTER_COMMIT_TOOLS:
            return "only checking is left: scroll, wait, finish or give_up"
        checked = checks.check_decision(tool, args, view, self.link.apps, self.offered, self.dead_ends)
        if isinstance(checked, str):
            return checked
        problem = safety.check_action(tool, checked[0], view)
        return problem or checked

    async def act(self, decision) -> None:
        tool, args = decision.tool, decision.args
        self.task.add_note(args.get("remember"))
        why = (args.get("why") or "").strip()[:100]
        if why:
            await self.progress("running", why)

        if tool in PHONE_TOOLS:
            await self.do_phone_action(tool, args, why)
        elif tool == "wait":
            await self.do_wait(args, why)
        elif tool == "look":
            await self.take_picture(why or "Looking at the screen")
        elif tool == "ask_user":
            await self.do_ask(args)
        elif tool == "confirm_and_tap":
            await self.do_confirm_and_tap(args, why)
        elif tool == "finish":
            await self.do_finish(args)
        elif tool == "give_up":
            raise TaskEnded(self.failed_or_unsure(), args.get("reason") or "The AI gave up.")
        self.save()

    # ---------- actions ----------

    async def do_phone_action(self, tool: str, args: dict, why: str) -> None:
        before = self.view
        target = args.get("target")
        label = before.by_index[target].label if target in before.by_index else ""
        if tool == "open_app":
            label = self.link.apps.get(args.get("package_name"), args.get("name", ""))
        step = self.new_step(tool, target, label, why)

        if tool == "type_text" and args.get("tap_first"):
            if not await self.tap_then_type(step, args):
                return
            self.after_step(step, before)
            return

        command = self.command(tool, args)
        result = await self.send(command, step)
        if result is None:
            return
        if (result.status == "rejected" and tool == "tap" and result.error
                and result.error.code in ("stale_screen", "stale_target")):
            retarget = self.same_place(before, label)
            if retarget is not None:
                step.change = "screen shifted; tapped the same item again"
                command = self.command(tool, {**args, "target": retarget})
                result = await self.send(command, step)
                if result is None:
                    return
        if tool == "type_text" and result.status == "ok":
            self.task.typed_texts.append(args.get("text", ""))
        self.after_step(step, before)

    async def tap_then_type(self, step: Step, args: dict) -> bool:
        result = await self.send(self.command("tap", args), step)
        if result is None or result.status != "ok":
            self.after_step(step, self.view)
            return False
        field = self.text_box()
        if field is None:
            step.status, step.change = "failed", "tapped it, but no text box appeared"
            self.warnings.append("Tapping that did not open a text box.")
            return False
        result = await self.send(self.command("type_text", {**args, "target": field}), step)
        if result is not None and result.status == "ok":
            self.task.typed_texts.append(args.get("text", ""))
        return result is not None

    async def picture_if_needed(self) -> None:
        if not self.images or self.view.signature in self.pictured:
            return
        if self.want_picture or needs_picture(self.view):
            self.want_picture = False
            await self.take_picture("Looking at the screen")

    async def take_picture(self, why: str) -> None:
        if self.view.signature in self.pictured:
            self.warnings.append("You already have the picture of this screen.")
            return
        if self.pictures_taken >= MAX_PICTURES:
            self.warnings.append("No more pictures for this task; use the screen text.")
            return
        self.pictured.add(self.view.signature)
        self.pictures_taken += 1
        command = self.command("screenshot", {})
        command.max_side = 1280
        result = await self.send(command, None)
        if result is None:
            return
        if result.status != "ok" or result.image is None:
            code = result.error.code if result.error else ""
            if code == "screenshot_blocked":
                self.warnings.append("This screen can't be captured (it is protected). Don't guess; ask the user "
                                     "if needed.")
            else:
                self.warnings.append("The picture could not be taken; use the screen text.")
            return
        marked = marked_picture(result.image, self.view)  # send() already switched to the screen the picture shows
        if isinstance(marked, str):
            self.warnings.append(f"The picture was unusable ({marked}); use the screen text.")
            return
        self.picture = (self.view.signature, marked)
        self.pictured.add(self.view.signature)
        self.notebook.write("picture", signature=self.view.signature, bytes=len(marked), why=why)

    async def do_wait(self, args: dict, why: str) -> None:
        before = self.view
        seconds = max(1, min(10, int(args.get("seconds") or 3)))
        if self.task.wait_seconds + seconds > self.limits.max_wait_seconds:
            self.warnings.append("No more waiting; the screen is not changing. Try something else or give up.")
            return
        step = self.new_step("wait", None, f"{seconds} s", why)
        self.task.wait_seconds += seconds
        if not await self.pause_for(seconds):
            return
        await self.read_screen()
        step.status = "waited"
        self.after_step(step, before)

    async def do_ask(self, args: dict) -> None:
        question = args["question"].strip()
        step = self.new_step("ask_user", None, question, "")
        outcome = await self.ask("question", question, args.get("choices"), self.limits.question_expires)
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

    async def do_confirm_and_tap(self, args: dict, why: str) -> None:
        before = self.view
        plan = safety.plan_confirm(before, args, self.task.unsure)
        if isinstance(plan, str):
            self.bad_reply(plan)
            self.warnings.append(plan)
            return
        step = self.new_step("confirm_and_tap", args["target"], plan.label, why)
        step.irreversible = True
        outcome = await self.ask("confirm", plan.text, ["Yes", "No"], self.limits.confirm_expires)
        if outcome is None:
            step.status = "cancelled"
            return
        if outcome.status in ("expired", "declined"):
            step.status, step.change = "declined", "the user said no; nothing was tapped"
            self.task.answers.append((f"Approve: {args['summary']}", "No"))
            return
        if outcome.status != "approved":
            correction = (outcome.text or "").strip()
            step.status, step.change = "declined", "the user answered with a change instead"
            if correction:
                self.task.corrections.append(correction)
                self.warnings.append(f"The user changed the request: {correction}")
            return

        await self.read_screen()
        target = safety.recheck(plan, self.view)
        if isinstance(target, str):
            step.status, step.change = "blocked", target
            self.warnings.append(target)
            return
        command = self.command("tap", {"target": target})
        command.confirmed, command.ask_id, command.must_see = True, outcome.ask_id, plan.must_see or None
        result = await self.send(command, step, confirmed=True)
        if result is None:
            return
        total = safety.screen_total(before)
        if result.status == "ok":
            self.task.done_for_good.append(f'approved and tapped "{plan.label}"' + (f", total {total}" if total else ""))
        elif result.status == "unknown":
            self.task.unsure.append(f'tapped "{plan.label}" but the result was lost')
        elif result.error and result.error.code == "must_see_missing":
            self.warnings.append("The total changed before the tap; nothing was done. Ask again with the new total.")
        self.after_step(step, before)

    async def do_finish(self, args: dict) -> None:
        await self.read_screen()
        await self.wait_while_loading()
        committed = [step for step in self.task.steps if step.irreversible]
        problem = checks.check_finish(
            args, self.view, goal=self.task.goal, corrections=self.task.corrections, typed=self.task.typed_texts,
            app_label=self.link.apps.get(self.screen.package_name, ""), committed_steps=committed)
        if problem is None:
            raise TaskEnded("completed", args.get("summary") or "Done.", [str(p) for p in args.get("proof") or []])
        self.task.finish_misses += 1
        if self.task.finish_misses >= 2:
            raise TaskEnded("unconfirmed", "Could not confirm: " + (args.get("summary") or "the task"))
        self.warnings.append(problem)

    # ---------- talking to the phone ----------

    def command(self, action: str, args: dict) -> Command:
        return Command(
            command_id=new_id("c"), action=action, revision=self.link.revision,
            timeout_ms=TIMEOUTS_MS.get(action, 10_000),
            screen_id=self.screen.screen_id if action in ("tap", "type_text", "scroll") and self.screen else None,
            target=args.get("target") if action in ("tap", "type_text", "scroll") else None,
            text=args.get("text") if action == "type_text" else None,
            submit=bool(args.get("submit")) if action == "type_text" else None,
            direction=args.get("direction") if action == "scroll" else None,
            package_name=args.get("package_name") if action == "open_app" else None,
        )

    async def send(self, command: Command, step: Step | None, confirmed: bool = False):
        n = step.n if step else None
        self.notebook.write("step_sending", n=n, command_id=command.command_id, action=command.action,
                            target=command.target, label=step.label if step else "", why=step.why if step else "",
                            confirmed=confirmed)
        try:
            result = await self.link.run(command)
        except PhoneGone:
            raise
        except Interrupted:
            self.notebook.write("step_result", n=n, command_id=command.command_id, status="not_sent")
            if step:
                step.status = "not sent"
            return None
        error = result.error.code if result.error else ""
        self.notebook.write("step_result", n=n, command_id=command.command_id, status=result.status, error=error,
                            settled=result.settled)
        if step:
            step.status = result.status
            if result.status != "ok":
                step.error = plain_error(error, result.error.message if result.error else "")
        if result.screen is not None:
            self.set_screen(result.screen, result.settled)
        elif result.status != "ok" or command.action != "read_screen":
            await self.read_screen()
        if result.status == "unknown" and step and not step.irreversible:
            self.warnings.append(f'The phone did not confirm "{step.label}". Check the screen before repeating it.')
        return result

    async def read_screen(self) -> None:
        for _ in range(2):
            result = await self.send(self.command("read_screen", {}), None)
            if result is not None and result.screen is not None:
                return
        if self.screen is None:
            raise TaskEnded("failed", "The phone did not send its screen.")

    def set_screen(self, screen, settled: bool) -> None:
        self.screen = screen
        self.settled = settled
        self.view = screens.render(screen, self.link.apps)
        self.task.saw_screen(self.view.title or self.view.app)

    async def ask(self, kind: str, text: str, choices, expires_in: int):
        await self.progress("waiting_for_approval" if kind == "confirm" else "waiting_for_user", text.split("\n")[0])
        self.notebook.write("ask", kind=kind, text=text)
        started = time.monotonic()
        try:
            outcome = await self.link.ask(kind, text, choices, expires_in)
        finally:
            self.user_time += time.monotonic() - started
        if outcome.status == "cancelled":
            return None
        shown = safety.hide_secrets(outcome.text, text) if outcome.text else None
        self.notebook.write("answer", ask_id=outcome.ask_id, status=outcome.status, text=shown)
        await self.progress("running", "Continuing")
        return outcome

    async def progress(self, state: str, text: str) -> None:
        await self.link.progress(state, text, len(self.task.steps))

    async def pause_for(self, seconds: float) -> bool:
        try:
            await self.link.interruptible(asyncio.sleep(seconds))
            return True
        except PhoneGone:
            raise
        except Interrupted:
            return False

    # ---------- interruptions, budgets, ending ----------

    async def handle_interrupts(self) -> None:
        for text in self.link.pending_updates():
            self.task.corrections.append(text)
            self.task.revision = self.link.revision
            self.notebook.write("update", text=text, revision=self.task.revision)
            self.warnings.append(f"The user changed the request: {text}")
        if self.link.stop_requested:
            raise TaskEnded("stopped", "Stopped as you asked.")
        if self.link.pause_requested:
            await self.progress("paused", "Paused")
            self.notebook.write("status", to="paused", reason="the user paused")
            started = time.monotonic()
            while self.link.pause_requested and not self.link.stop_requested:
                await self.link.wait_changed()
            self.user_time += time.monotonic() - started
            if self.link.stop_requested:
                raise TaskEnded("stopped", "Stopped as you asked.")
            self.notebook.write("status", to="running", reason="the user resumed")
            await self.progress("running", "Resuming")
            await self.read_screen()
            await self.handle_interrupts()

    def check_budgets(self) -> None:
        limit = self.limits.max_steps + (self.limits.extra_steps_after_commit if self.committed() else 0)
        if len(self.task.steps) >= limit:
            raise self.out_of("steps")
        if (time.monotonic() - self.started - self.user_time) / 60 > self.limits.max_work_minutes:
            raise self.out_of("time")
        if self.task.bad_total >= self.limits.max_bad_total:
            raise TaskEnded(self.failed_or_unsure(), "The AI kept giving answers I couldn't use.")

    def out_of(self, what: str) -> TaskEnded:
        tried = "; ".join(f"{s.tool} {s.label}".strip() for s in self.task.steps[-3:])
        return TaskEnded(self.failed_or_unsure(), f"I ran out of {what} before finishing. Last tried: {tried or 'nothing'}.")

    def bad_reply(self, reason: str) -> None:
        self.task.bad_in_row += 1
        self.task.bad_total += 1
        self.notebook.write("bad_reply", reason=reason)
        log.info("task %s: unusable AI reply: %s", self.task.id, reason)
        if self.task.bad_in_row >= self.limits.max_bad_in_row:
            raise TaskEnded(self.failed_or_unsure(), f"The AI kept giving answers I couldn't use (last: {reason}).")

    def failed_or_unsure(self) -> str:
        return "unconfirmed" if self.task.unsure else "failed"

    def committed(self) -> bool:
        return any(step.irreversible and step.status in ("ok", "unknown") for step in self.task.steps)

    async def end(self, outcome: str, summary: str, proof: list[str] | None) -> Done:
        self.task.status = outcome
        self.task.work_seconds = time.monotonic() - self.started - self.user_time
        last = self.task.steps[-1] if self.task.steps else None
        last_action = None
        if last and (last.irreversible or last.status == "unknown"):
            last_action = LastAction(label=f'{last.tool} "{last.label}"', status=last.status or "not sent")
        if self.task.unsure and outcome in ("stopped", "failed", "unconfirmed"):
            summary += " Heads-up: " + "; ".join(self.task.unsure) + "."
        done = Done(task_id=self.task.id, outcome=outcome, summary=summary[:600], proof=proof or None,
                    last_action=last_action)
        self.notebook.write("done", outcome=outcome, summary=done.summary, proof=proof)
        self.save()
        log.info("task %s ended: %s — %s", self.task.id, outcome, done.summary)
        try:
            await self.link.finish(done)
        except PhoneGone:
            pass
        return done

    # ---------- learning from each step ----------

    def new_step(self, tool: str, target, label: str, why: str) -> Step:
        step = Step(n=len(self.task.steps) + 1, tool=tool, target=target, label=label, why=why,
                    signature=self.view.signature if self.view else "")
        self.task.steps.append(step)
        return step

    def after_step(self, step: Step, before) -> None:
        after = self.view
        if step.status == "ok" or step.status == "waited":
            step.change = step.change or screens.describe_change(before, after, self.settled)
        elif not step.change:
            step.change = step.error or step.status
        if step.change.startswith("no change") and step.tool != "wait":
            self.dead_ends.add((step.tool, step.label, before.signature))
            if step.tool == "scroll":
                self.warnings.append("That list is at its end.")
            else:
                self.warnings.append("Your last action changed nothing; do not repeat it.")
        self.same_way_scrolls = self.same_way_scrolls + 1 if step.tool == "scroll" and before.signature != after.signature else 0
        if self.same_way_scrolls >= 5:
            self.warnings.append("Lots of scrolling. Try the search box.")
        self.signatures.append(after.signature)
        recent = self.signatures[-10:]
        if len(self.signatures) >= 10 and len(set(recent)) <= 2:
            raise TaskEnded(self.failed_or_unsure(), "I got stuck: " +
                            "; ".join(f"{s.tool} {s.label}".strip() for s in self.task.steps[-3:]))
        if len(self.signatures) >= 6 and len(set(self.signatures[-6:])) <= 2:
            titles = " and ".join(dict.fromkeys(self.task.screens_seen[-2:])) or "the same screens"
            self.warnings.append(f"You are going back and forth between {titles}. Try a different way.")
            self.want_picture = True
        log.info("step %d: %s %s → %s", step.n, step.tool, step.label, step.change)

    def same_place(self, before, label: str) -> int | None:
        after = self.view
        if after is None or not label:
            return None
        old = {(line.role, line.label) for line in before.lines}
        new = {(line.role, line.label) for line in after.lines}
        if not old or len(old & new) / len(old) < 0.9:
            return None
        matches = [line for line in after.lines if line.label == label]
        return matches[0].index if len(matches) == 1 else None

    def text_box(self) -> int | None:
        fields = [line for line in self.view.lines if line.node.editable]
        focused = [line for line in fields if line.node.focused]
        if focused:
            return focused[0].index
        return fields[0].index if len(fields) == 1 else None

    async def wait_while_loading(self) -> None:
        for _ in range(self.limits.loading_retries):
            loading = self.screen.loading or not self.settled and len(self.view.lines) < 3
            if not loading:
                return
            if not await self.pause_for(self.limits.loading_pause):
                return
            await self.read_screen()

    def step_message(self, view, image_attached: bool = False) -> str:
        steps = self.task.steps
        first = not steps
        apps_line = ", ".join(sorted(self.link.apps.values())) if first or self.open_app_failed() else None
        return build_step_message(
            goal=self.task.goal, corrections=self.task.corrections, answers=self.task.answers,
            done_for_good=self.task.done_for_good, unsure=self.task.unsure, notes=self.task.notes,
            apps_line=apps_line, screens_seen=self.task.screens_seen, steps=steps[-8:],
            total_steps=len(steps), step_limit=self.limits.max_steps, warnings=self.take_warnings(),
            view_text=view.text, image_attached=image_attached)

    def take_warnings(self) -> list[str]:
        warnings = list(dict.fromkeys(self.warnings))[-3:]
        self.warnings.clear()
        return warnings

    def open_app_failed(self) -> bool:
        last = self.task.steps[-1] if self.task.steps else None
        return bool(last and last.tool == "open_app" and last.status != "ok")

    def late_result(self, result) -> None:
        self.notebook.write("late_result", command_id=result.command_id, status=result.status)
        if result.status == "ok" and self.task.unsure:
            self.warnings.append("The phone later confirmed the action whose result was lost.")

    def loggable(self, args: dict) -> dict:
        shown = dict(args)
        if "text" in shown and safety.is_secret(str(shown["text"])):
            shown["text"] = "[hidden]"
        return shown

    def save(self) -> None:
        self.notebook.write("state", task=self.task.snapshot())
