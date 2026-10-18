import asyncio
import logging
import time

from jarvis import checks, safety
from jarvis import screen as screens
from jarvis.claude import AIError, AIUnavailable, BadReply, RateLimited
from jarvis.messages import Command, Done
from jarvis.phone import Interrupted, PhoneGone, new_command
from jarvis.pictures import marked_picture, needs_picture
from jarvis.prompt import SYSTEM_PROMPT, build_step_message, plain_error, retry_note
from jarvis.task import Notebook, Step, Task

log = logging.getLogger("jarvis.brain")

PHONE_TOOLS = {"tap", "type_text", "scroll", "go_back", "go_home", "open_app"}
AFTER_COMMIT_TOOLS = {"scroll", "wait", "finish", "give_up", "look"}
MAX_PICTURES = 8
MAX_STEPS = 50
EXTRA_STEPS_AFTER_COMMIT = 3
MAX_MODEL_CALLS = 80
MAX_WORK_MINUTES = 15
MAX_BAD_IN_ROW = 3
MAX_BAD_TOTAL = 8
MAX_WAIT_SECONDS = 90
QUESTION_EXPIRES = 600
CONFIRM_EXPIRES = 120
RATE_LIMIT_WAITS = (5, 10, 20)
UNAVAILABLE_WAITS = (2, 5)


class TaskEnded(Exception):
    def __init__(self, outcome: str, summary: str, proof: list[str] | None = None):
        super().__init__(summary)
        self.outcome = outcome
        self.summary = summary
        self.proof = proof


def brain_runner(ai):
    async def run(server_run) -> Done | None:
        task = Task(goal=server_run.goal, device_id=server_run.device_id, id=server_run.task_id)
        notebook = Notebook.for_task(server_run.settings.data_dir, task.id)
        notebook.write("task", task_id=task.id, goal=task.goal, device_id=task.device_id, ai=ai.name)
        return await Brain(task, server_run.link, ai, notebook).run()
    return run


class Brain:
    def __init__(self, task: Task, link, ai, notebook: Notebook):
        self.task = task
        self.link = link
        self.ai = ai
        self.notebook = notebook
        self.screen = None
        self.view = None
        self.warnings: list[str] = []
        self.dead_ends: set[tuple] = set()
        self.signatures: list[str] = []
        self.same_way_scrolls = 0
        self.started = time.monotonic()
        self.user_time = 0.0
        self.picture: tuple[str, bytes] | None = None
        self.pictured: set[str] = set()
        self.pictures_taken = 0
        self.want_picture = False
        link.on_late_result = self.late_result

    # ---------- the loop ----------

    async def run(self) -> Done | None:
        try:
            await self.read_screen()
            while True:
                if self.link.stop_requested:
                    raise TaskEnded("stopped", "Stopped as you asked.")
                self.check_budgets()
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
        rate_waits = list(RATE_LIMIT_WAITS)
        down_waits = list(UNAVAILABLE_WAITS)
        image = self.picture[1] if self.picture and self.picture[0] == view.signature else None
        message = self.step_message(view, image_attached=image is not None)
        while True:
            if self.task.model_calls >= MAX_MODEL_CALLS:
                raise self.out_of("AI calls")
            self.task.model_calls += 1
            try:
                decision = await self.link.interruptible(
                    self.ai.decide(SYSTEM_PROMPT, message, image=image, retry_note=retry))
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
            except BadReply as error:
                self.bad_reply(error.reason)
                retry = retry_note(error.reason)
                continue
            except AIError as error:
                raise TaskEnded(self.failed_or_unsure(), error.message)

            checked = self.validate(decision.tool, decision.args, view)
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
        if self.committed() and len(self.task.steps) >= MAX_STEPS and tool not in AFTER_COMMIT_TOOLS:
            return "only checking is left: scroll, wait, finish or give_up"
        checked = checks.check_decision(tool, args, view, self.link.apps, self.dead_ends)
        if isinstance(checked, str):
            return checked
        problem = safety.check_action(tool, checked[0], view)
        return problem or checked

    async def act(self, decision) -> None:
        tool, args = decision.tool, decision.args
        self.task.add_note(args.get("remember"))
        why = args.get("why", "")[:100]

        if tool in PHONE_TOOLS:
            await self.do_phone_action(tool, args, why)
        elif tool == "wait":
            await self.do_wait(args, why)
        elif tool == "look":
            await self.take_picture(why)
        elif tool == "ask_user":
            await self.do_ask(args)
        elif tool == "confirm_and_tap":
            await self.do_confirm_and_tap(args, why)
        elif tool == "finish":
            await self.do_finish(args)
        elif tool == "give_up":
            raise TaskEnded(self.failed_or_unsure(), args["reason"])
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

        result = await self.send(self.command(tool, args), step)
        if result is None:
            return
        if (result.status == "rejected" and tool == "tap" and result.error
                and result.error.code in ("stale_screen", "stale_target")):
            retarget = self.same_place(before, label)
            if retarget is not None:
                step.change = "screen shifted; tapped the same item again"
                result = await self.send(self.command(tool, {**args, "target": retarget}), step)
                if result is None:
                    return
        if tool == "tap" and self.click_did_nothing(result, before) and target in before.by_index:
            await self.tap_at_position(step, before.by_index[target].node)
        if tool == "type_text" and result.status == "ok":
            self.task.typed_texts.append(args.get("text", ""))
        self.after_step(step, before)

    def click_did_nothing(self, result, before) -> bool:
        if result.status == "failed" and result.error and result.error.code == "action_failed":
            return True
        return result.status == "ok" and screens.describe_change(before, self.view).startswith("no change")

    async def tap_at_position(self, step: Step, node) -> None:
        left, top, right, bottom = node.bounds
        await self.send(new_command("tap_at", x=(left + right) // 2, y=(top + bottom) // 2), step)

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
        if self.view.signature in self.pictured:
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
        result = await self.send(new_command("screenshot", max_side=1280), None)
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
        seconds = args["seconds"]
        if self.task.wait_seconds + seconds > MAX_WAIT_SECONDS:
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
        question = args["question"]
        step = self.new_step("ask_user", None, question, "")
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

    async def do_confirm_and_tap(self, args: dict, why: str) -> None:
        before = self.view
        plan = safety.plan_confirm(before, args, self.task.unsure)
        if isinstance(plan, str):
            self.bad_reply(plan)
            self.warnings.append(plan)
            return
        step = self.new_step("confirm_and_tap", args["target"], plan.label, why)
        step.irreversible = True
        outcome = await self.ask("confirm", plan.text, ["Yes", "No"], CONFIRM_EXPIRES)
        if outcome is None:
            step.status = "cancelled"
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
        target = safety.recheck(plan, self.view)
        if isinstance(target, str):
            step.status, step.change = "blocked", target
            self.warnings.append(target)
            return
        command = self.command("tap", {"target": target})
        command.confirmed = True
        result = await self.send(command, step, confirmed=True)
        if result is None:
            return
        total = safety.screen_total(before)
        if result.status == "ok":
            self.task.done_for_good.append(f'approved and tapped "{plan.label}"' + (f", total {total}" if total else ""))
        elif result.status == "unknown":
            self.task.unsure.append(f'tapped "{plan.label}" but the result was lost')
        self.after_step(step, before)

    async def do_finish(self, args: dict) -> None:
        await self.read_screen()
        committed = [step for step in self.task.steps if step.irreversible]
        problem = checks.check_finish(
            args, self.view, goal=self.task.goal, corrections=self.task.corrections, typed=self.task.typed_texts,
            app_label=self.link.apps.get(self.screen.package_name, ""), committed_steps=committed)
        if problem is None:
            raise TaskEnded("completed", args["summary"], args["proof"])
        self.task.finish_misses += 1
        if self.task.finish_misses >= 2:
            raise TaskEnded("unconfirmed", "Could not confirm: " + args["summary"])
        self.warnings.append(problem)

    # ---------- talking to the phone ----------

    def command(self, action: str, args: dict) -> Command:
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
        if command.action == "type_text" and command.submit and error == "cant_submit":
            self.warnings.append(
                "The text was entered but the search was not submitted. Do not retype it. "
                "Use a visible Search/Go button or a suggestion matching the query on the fresh screen; "
                "if none is available, ask the user to press Search on the keyboard."
            )
        elif command.action == "type_text" and command.submit and result.status == "ok" and result.submitted is not True:
            self.warnings.append(
                "The phone acknowledged typing but did not confirm search submission (older phone builds may ignore submit). "
                "Check the fresh screen for actual results. If it still shows suggestions, tap a visible Search/Go "
                "button or a matching suggestion instead of retyping; ask the user to press Search if none is available."
            )
        self.notebook.write("step_result", n=n, command_id=command.command_id, status=result.status, error=error)
        if step:
            step.status = result.status
            if result.status != "ok":
                step.error = plain_error(error, result.error.message if result.error else "")
        if result.screen is not None:
            self.set_screen(result.screen)
        elif result.status != "ok" or command.action != "read_screen":
            await self.read_screen()
        if result.status == "unknown" and step and not step.irreversible:
            self.warnings.append(f'The phone did not confirm "{step.label}". Check the screen before repeating it.')
        return result

    async def read_screen(self) -> None:
        for _ in range(2):
            result = await self.send(new_command("read_screen"), None)
            if result is not None and result.screen is not None:
                return
        if self.screen is None:
            raise TaskEnded("failed", "The phone did not send its screen.")

    def set_screen(self, screen) -> None:
        self.screen = screen
        self.view = screens.render(screen, self.link.apps)
        self.task.saw_screen(self.view.app)

    async def ask(self, kind: str, text: str, choices, expires_in: int):
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
        return outcome

    async def pause_for(self, seconds: float) -> bool:
        try:
            await self.link.interruptible(asyncio.sleep(seconds))
            return True
        except PhoneGone:
            raise
        except Interrupted:
            return False

    # ---------- budgets and ending ----------

    def check_budgets(self) -> None:
        limit = MAX_STEPS + (EXTRA_STEPS_AFTER_COMMIT if self.committed() else 0)
        if len(self.task.steps) >= limit:
            raise self.out_of("steps")
        if (time.monotonic() - self.started - self.user_time) / 60 > MAX_WORK_MINUTES:
            raise self.out_of("time")
        if self.task.bad_total >= MAX_BAD_TOTAL:
            raise TaskEnded(self.failed_or_unsure(), "The AI kept giving answers I couldn't use.")

    def out_of(self, what: str) -> TaskEnded:
        tried = "; ".join(f"{s.tool} {s.label}".strip() for s in self.task.steps[-3:])
        return TaskEnded(self.failed_or_unsure(), f"I ran out of {what} before finishing. Last tried: {tried or 'nothing'}.")

    def bad_reply(self, reason: str) -> None:
        self.task.bad_in_row += 1
        self.task.bad_total += 1
        self.notebook.write("bad_reply", reason=reason)
        log.info("task %s: unusable AI reply: %s", self.task.id, reason)
        if self.task.bad_in_row >= MAX_BAD_IN_ROW:
            raise TaskEnded(self.failed_or_unsure(), f"The AI kept giving answers I couldn't use (last: {reason}).")

    def failed_or_unsure(self) -> str:
        return "unconfirmed" if self.task.unsure else "failed"

    def committed(self) -> bool:
        return any(step.irreversible and step.status in ("ok", "unknown") for step in self.task.steps)

    async def end(self, outcome: str, summary: str, proof: list[str] | None) -> Done:
        self.task.status = outcome
        self.task.work_seconds = time.monotonic() - self.started - self.user_time
        if self.task.unsure and outcome in ("stopped", "failed", "unconfirmed"):
            summary += " Heads-up: " + "; ".join(self.task.unsure) + "."
        done = Done(outcome=outcome, summary=summary[:600])
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
                    signature=self.view.signature)
        self.task.steps.append(step)
        return step

    def after_step(self, step: Step, before) -> None:
        after = self.view
        if step.status == "ok" or step.status == "waited":
            step.change = step.change or screens.describe_change(before, after)
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
            apps = " and ".join(dict.fromkeys(self.task.screens_seen[-2:])) or "the same screens"
            self.warnings.append(f"You are going back and forth in {apps}. Try a different way.")
            self.want_picture = True
        log.info("step %d: %s %s → %s", step.n, step.tool, step.label, step.change)

    def same_place(self, before, label: str) -> int | None:
        after = self.view
        if not label:
            return None
        old = {(line.role, line.label) for line in before.lines}
        new = {(line.role, line.label) for line in after.lines}
        if not old or len(old & new) / len(old) < 0.9:
            return None
        matches = [line for line in after.lines if line.label == label]
        return matches[0].index if len(matches) == 1 else None

    def text_box(self) -> int | None:
        fields = [line for line in self.view.lines if line.node.editable]
        return fields[0].index if len(fields) == 1 else None

    def step_message(self, view, image_attached: bool = False) -> str:
        steps = self.task.steps
        first = not steps
        apps_line = ", ".join(sorted(self.link.apps.values())) if first or self.open_app_failed() else None
        return build_step_message(
            goal=self.task.goal, corrections=self.task.corrections, answers=self.task.answers,
            done_for_good=self.task.done_for_good, unsure=self.task.unsure, notes=self.task.notes,
            apps_line=apps_line, screens_seen=self.task.screens_seen, steps=steps[-8:],
            total_steps=len(steps), step_limit=MAX_STEPS, warnings=self.take_warnings(),
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
