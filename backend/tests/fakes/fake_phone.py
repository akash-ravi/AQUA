import asyncio
import base64
import inspect
import io
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from tests.fakes.fakedash import APPS, DOORDASH, Built, Entry, FakeDash
from jarvis.messages import (
    ACTIONS, Answer, App, Ask, AskCancel, BadMessage, Command, Device, Done, ErrorInfo, Hello, Image,
    LastResult, Node, Pause, Progress, Ready, Rejoin, Result, Resume, Screen, Stop, TaskRequest, Update,
    parse_backend_message, to_json,
)

MAX_FRAME = 16 * 1024 * 1024
REMEMBERED_COMMANDS = 200
MOVE_TOLERANCE = 8
SETTLE_MS = 300


class FakePhone:
    def __init__(self, app: FakeDash | None = None, *, popup_race: bool = False, fail_once: dict[str, str] | None = None,
                 unknown_once: str | None = None, drop_after: str | None = None, late_result: float = 0.0,
                 reorder_menu: bool = False, black_screenshot: bool = False, loading_forever: bool = False,
                 screenshots: bool = False):
        self.app = app or FakeDash()
        self.popup_race = popup_race
        self.fail_once = dict(fail_once or {})
        self.unknown_once = unknown_once
        self.drop_after = drop_after
        self.late_result = late_result
        self.reorder_menu = reorder_menu
        self.black_screenshot = black_screenshot
        self.loading_forever = loading_forever
        self.screenshots = screenshots

        self.device_id = f"fake-{uuid.uuid4().hex[:12]}"
        self.task_id: str | None = None
        self.rejoin_key: str | None = None
        self.screen_count = 0
        self.latest_id: str | None = None
        self.latest: Built | None = None
        self.approved: set[str] = set()
        self.used_asks: set[str] = set()
        self.results: OrderedDict[str, Result] = OrderedDict()
        self.last_result: LastResult | None = None
        self.stopped = False
        self.paused = False
        self.revision_seen = 0
        self.min_revision = 0
        self.drop_now = False
        self.outbox: asyncio.Queue = asyncio.Queue()

    @property
    def actions(self) -> list[str]:
        return [action for action in ACTIONS if action != "screenshot" or self.screenshots]

    @property
    def apps(self) -> list[App]:
        return [App(label=label, package_name=package) for package, label in APPS.items()]

    def hello(self) -> Hello:
        device = Device(id=self.device_id, name="Fake phone", sdk_int=36)
        return Hello(protocol=1, device=device, app_version="fake", actions=self.actions, apps=self.apps)

    # ---- the user and the backend's other messages ----

    def record_answer(self, ask_id: str, approved: bool) -> None:
        if approved:
            self.approved.add(ask_id)
        else:
            self.approved.discard(ask_id)

    def user_stop(self) -> None:
        self.stopped = True
        self.outbox.put_nowait(Stop())

    def user_pause(self) -> None:
        self.paused = True
        self.outbox.put_nowait(Pause())

    def user_resume(self) -> None:
        self.paused = False
        self.outbox.put_nowait(Resume())

    def user_update(self, text: str) -> None:
        self.outbox.put_nowait(Update(text=text))
        self.sent_update()

    def sent_update(self) -> None:
        self.min_revision = self.revision_seen + 1

    def saw_revision(self, revision: int) -> None:
        self.revision_seen = max(self.revision_seen, revision)

    # ---- commands ----

    def handle(self, command: Command) -> Result:
        if command.command_id in self.results:
            return self.results[command.command_id]
        result = self._run(command)
        self.saw_revision(command.revision)
        self.results[command.command_id] = result
        while len(self.results) > REMEMBERED_COMMANDS:
            self.results.popitem(last=False)
        self.last_result = LastResult(command_id=command.command_id, status=result.status, error=result.error)
        return result

    def read_screen(self) -> Screen:
        self.app.advance()
        built = self.app.snapshot()
        self.screen_count += 1
        self.latest_id, self.latest = f"s-{self.screen_count}", built
        screen = Screen(
            screen_id=self.latest_id, at=int(time.time() * 1000), package_name=built.package, title=built.title,
            width=1080, height=2400, keyboard_top=built.keyboard_top, loading=built.loading or self.loading_forever,
            nodes=built.nodes(),
        )
        self.app.screen_sent()
        self._spring_traps()
        return screen

    def _spring_traps(self) -> None:
        in_doordash = self.app.package == DOORDASH and not self.app.popup
        if self.popup_race and in_doordash:
            self.popup_race = False
            self.app.show_popup()
        elif self.reorder_menu and in_doordash and self.app.page == "dd_store":
            self.reorder_menu = False
            self.app.swap_menu()

    def _run(self, command: Command) -> Result:
        if self.stopped:
            return self._reply(command, "rejected", "stopped", "the user pressed Stop")
        if self.paused:
            return self._reply(command, "rejected", "paused", "the user paused the task")
        if command.revision < self.min_revision:
            return self._reply(command, "rejected", "old_revision", "the user changed the task")
        if command.action not in self.actions:
            return self._reply(command, "rejected", "unsupported", f"this phone can't {command.action}")
        handlers: dict[str, Callable[[Command], Result]] = {
            "read_screen": lambda c: self._reply(c, "ok"),
            "tap": self._tap,
            "type_text": self._type_text,
            "scroll": self._scroll,
            "go_back": self._go_back,
            "go_home": self._go_home,
            "open_app": self._open_app,
            "screenshot": self._screenshot,
        }
        return handlers[command.action](command)

    def _reply(self, command: Command, status: str, code: str | None = None, message: str = "", **extra) -> Result:
        error = ErrorInfo(code=code, message=message) if code else None
        settle_ms = 120 if command.action == "read_screen" else SETTLE_MS
        return Result(command_id=command.command_id, status=status, error=error, settled=True, settle_ms=settle_ms,
                      screen=self.read_screen(), **extra)

    def _stale(self, command: Command) -> str | None:
        if self.latest is None or command.screen_id != self.latest_id:
            return "stale_screen"
        now = self.app.snapshot()
        if now.window_id != self.latest.window_id:
            return "stale_screen"
        then = self.latest.entries.get(command.target)
        if then is None:
            return "bad_target"
        if not _same_node(then, now.find(then.key)):
            return "stale_target"
        return None

    def _now(self, index: int) -> Entry | None:
        return self.app.snapshot().find(self.latest.entries[index].key)

    def _tap(self, command: Command) -> Result:
        if code := self._stale(command):
            return self._reply(command, "rejected", code, STALE_MESSAGES[code])
        if command.confirmed and (problem := self._confirm_problem(command)):
            return self._reply(command, "rejected", *problem)
        index = _nearest(self.latest, command.target, lambda node: node.clickable)
        if index is None:
            if command.confirmed:
                return self._reply(command, "failed", "internal", "nothing clickable there")
            # The gesture fallback lands on plain text, so nothing happens.
            return self._reply(command, "ok", acted_on=command.target, method="gesture")
        entry = self._now(index)
        if entry is None:
            return self._reply(command, "rejected", "stale_target", STALE_MESSAGES["stale_target"])
        if not entry.el.enabled:
            return self._reply(command, "rejected", "disabled", "that button is disabled")
        if command.confirmed:
            self.used_asks.add(command.ask_id)
        labels = _labels(self.latest, index)
        failed = next((label for label in self.fail_once if label.casefold() in labels), None)
        if failed:
            code = self.fail_once.pop(failed)
            return self._reply(command, "rejected", code, f"test failure for {failed!r}")
        entry.el.tap()
        if self.drop_after and self.drop_after.casefold() in labels:
            self.drop_after, self.drop_now = None, True
        if self.unknown_once and self.unknown_once.casefold() in labels:
            self.unknown_once = None
            return self._reply(command, "unknown", "gesture_unconfirmed", "the app never confirmed the tap",
                               acted_on=index, method="action")
        return self._reply(command, "ok", acted_on=index, method="action")

    def _confirm_problem(self, command: Command) -> tuple[str, str] | None:
        if command.ask_id not in self.approved or command.ask_id in self.used_asks:
            return "not_approved", "no matching Yes from the user"
        shown = [t.casefold() for node in self.app.snapshot().nodes() for t in (node.text, node.description) if t]
        missing = [s for s in command.must_see or [] if not any(s.casefold() in t for t in shown)]
        if missing:
            return "must_see_missing", f"not on screen: {', '.join(missing)}"
        return None

    def _type_text(self, command: Command) -> Result:
        if code := self._stale(command):
            return self._reply(command, "rejected", code, STALE_MESSAGES[code])
        el = self._now(command.target).el
        if not el.editable:
            return self._reply(command, "rejected", "not_editable", "that is not a text box")
        if not el.enabled:
            return self._reply(command, "rejected", "disabled", "that text box is disabled")
        el.typed(command.text or "")
        if command.submit:
            if el.submit is None:
                return self._reply(command, "failed", "cant_submit", "text entered; Enter not available here")
            el.submit()
        return self._reply(command, "ok")

    def _scroll(self, command: Command) -> Result:
        if command.target is not None:
            if code := self._stale(command):
                return self._reply(command, "rejected", code, STALE_MESSAGES[code])
            index = _nearest(self.latest, command.target, lambda node: node.scrollable)
            entry = self._now(index) if index is not None else None
        else:
            lists = [e for e in self.app.snapshot().entries.values() if e.el.scrollable]
            entry = max(lists, key=lambda e: _area(e.node.bounds), default=None)
        if entry is None or not entry.el.scroll:
            return self._reply(command, "ok")  # a swipe over something that can't move
        if command.direction not in entry.el.scroll:
            return self._reply(command, "rejected", "cant_scroll", f"that list can't move {command.direction}")
        entry.el.scrolled(command.direction)
        return self._reply(command, "ok")

    def _go_back(self, command: Command) -> Result:
        self.app.back()
        return self._reply(command, "ok")

    def _go_home(self, command: Command) -> Result:
        self.app.home()
        return self._reply(command, "ok")

    def _open_app(self, command: Command) -> Result:
        if not self.app.open(command.package_name or ""):
            return self._reply(command, "rejected", "app_not_found", f"no app {command.package_name}")
        return self._reply(command, "ok")

    def _screenshot(self, command: Command) -> Result:
        if self.black_screenshot:
            return self._reply(command, "failed", "screenshot_blocked", "the screen came back black")
        result = self._reply(command, "ok")
        result.image = _draw(result.screen, command.max_side or 1280)
        return result


STALE_MESSAGES = {
    "stale_screen": "that screen is no longer in front",
    "bad_target": "no such item on that screen",
    "stale_target": "that item changed or is gone",
}


def _same_node(then: Entry, now: Entry | None) -> bool:
    if now is None or then.fingerprint != now.fingerprint:
        return False
    return all(abs(a - b) <= MOVE_TOLERANCE for a, b in zip(then.node.bounds, now.node.bounds))


def _nearest(built: Built, index: int, wanted: Callable[[Node], bool]) -> int | None:
    current: int | None = index
    while current is not None:  # parents always come earlier, so this ends
        node = built.entries[current].node
        if wanted(node):
            return current
        current = node.parent
    return None


def _labels(built: Built, index: int) -> set[str]:
    labels: set[str] = set()
    for i, entry in built.entries.items():
        current: int | None = i
        while current is not None and current != index:
            current = built.entries[current].node.parent
        if current == index:
            labels.update(t.casefold() for t in (entry.node.text, entry.node.description) if t)
    return labels


def _area(bounds: tuple[int, int, int, int]) -> int:
    return (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])


def _draw(screen: Screen, max_side: int) -> Image:
    from PIL import Image as Picture, ImageDraw

    scale = min(1.0, max_side / max(screen.width, screen.height))
    width, height = round(screen.width * scale), round(screen.height * scale)
    picture = Picture.new("RGB", (width, height), "white")
    pen = ImageDraw.Draw(picture)
    for node in screen.nodes:
        box = [round(v * scale) for v in node.bounds]
        pen.rectangle(box, outline="gray")
        if node.text:
            pen.text((box[0] + 4, box[1] + 4), node.text, fill="black")
    data = io.BytesIO()
    picture.save(data, "JPEG", quality=75)
    return Image(format="jpeg", data=base64.b64encode(data.getvalue()).decode(), width=width, height=height)


# ---- the socket client ----

AnswerSource = Callable[[Ask], Any] | list | None


async def run_fake_phone(url: str, task_text: str, *, phone: FakePhone | None = None, answers: AnswerSource = None,
                         auto_yes: bool = False, on_event: Callable[[Any], Any] | None = None,
                         device_id: str | None = None, rejoin: Rejoin | None = None) -> Done | None:
    phone = phone or FakePhone()
    if device_id:
        phone.device_id = device_id
    if isinstance(answers, list):
        answers = list(answers)
    emit = on_event or (lambda message: None)
    open_asks: dict[str, asyncio.Task] = {}

    async with connect(url, max_size=MAX_FRAME, proxy=None) as socket:
        sender = asyncio.create_task(_send_outbox(socket, phone))
        try:
            await socket.send(to_json(phone.hello()))
            await socket.send(to_json(rejoin or TaskRequest(text=task_text)))
            async for raw in socket:
                try:
                    message = parse_backend_message(raw)
                except BadMessage:
                    continue
                if isinstance(message, Command):
                    result = phone.handle(message)
                    if phone.drop_now:
                        phone.drop_now = False
                        socket.transport.abort()
                        return None
                    if phone.late_result:
                        await asyncio.sleep(phone.late_result)
                    try:
                        await socket.send(to_json(result))
                    except ConnectionClosed:
                        pass  # still read whatever the backend sent before closing
                    continue
                if isinstance(message, (Ready, Progress)):
                    phone.saw_revision(message.revision)
                if isinstance(message, Ready):
                    phone.task_id, phone.rejoin_key = message.task_id, message.rejoin_key
                if isinstance(message, Ask):
                    open_asks[message.ask_id] = asyncio.create_task(_answer(socket, phone, message, answers, auto_yes))
                if isinstance(message, AskCancel) and message.ask_id in open_asks:
                    open_asks.pop(message.ask_id).cancel()
                outcome = emit(message)
                if inspect.isawaitable(outcome):
                    await outcome
                if isinstance(message, Done):
                    return message
        except ConnectionClosed:
            return None
        finally:
            sender.cancel()
            for task in open_asks.values():
                task.cancel()
    return None


async def _send_outbox(socket, phone: FakePhone) -> None:
    while True:
        message = await phone.outbox.get()
        try:
            await socket.send(to_json(message))
        except ConnectionClosed:
            return


async def _answer(socket, phone: FakePhone, ask: Ask, answers: AnswerSource, auto_yes: bool) -> None:
    if callable(answers):
        value = answers(ask)
        if inspect.isawaitable(value):
            value = await value
    elif isinstance(answers, list):
        value = answers.pop(0) if answers else None
    elif auto_yes:
        value = True if ask.kind == "confirm" else (ask.choices[0] if ask.choices else "yes")
    else:
        value = None
    answer = _as_answer(ask, value)
    if answer is None:
        return
    if ask.kind == "confirm" and answer.approved is not None:
        phone.record_answer(ask.ask_id, answer.approved)
    try:
        await socket.send(to_json(answer))
    except ConnectionClosed:
        pass


def _as_answer(ask: Ask, value: Any) -> Answer | None:
    if value is None:
        return None
    if isinstance(value, Answer):
        return value.model_copy(update={"ask_id": ask.ask_id})
    if isinstance(value, bool):
        return Answer(ask_id=ask.ask_id, approved=value)
    reply = str(value).strip()
    if ask.kind == "confirm" and reply.casefold() in ("y", "yes", "n", "no", ""):
        return Answer(ask_id=ask.ask_id, approved=reply.casefold() in ("y", "yes"))
    if ask.kind == "question" and ask.choices and reply.isdigit() and 1 <= int(reply) <= len(ask.choices):
        reply = ask.choices[int(reply) - 1]
    return Answer(ask_id=ask.ask_id, text=reply)



