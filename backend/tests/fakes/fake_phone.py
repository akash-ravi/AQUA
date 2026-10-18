import asyncio
import base64
import inspect
import io
import json
import re
import time
from collections import OrderedDict
from typing import Any, Callable

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from tests.fakes.fakedash import APPS, DOORDASH, Built, Entry, FakeDash
from jarvis.messages import ACTIONS, App, Answer, Ask, Command, Done, ErrorInfo, Image, Node, Result, Screen
from jarvis.wire import COMMAND_NAMES

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

        self.task_id: str | None = None
        self.screen_count = 0
        self.latest_id: str | None = None
        self.latest: Built | None = None
        self.approved: set[str] = set()
        self.used_asks: set[str] = set()
        self.results: OrderedDict[str, Result] = OrderedDict()
        self.stopped = False
        self.drop_now = False
        self.outbox: asyncio.Queue = asyncio.Queue()

    @property
    def actions(self) -> list[str]:
        return [action for action in (*ACTIONS, "list_apps") if action != "screenshot" or self.screenshots]

    @property
    def apps(self) -> list[App]:
        return [App(label=label, package_name=package) for package, label in APPS.items()]

    # ---- the user and the backend's other messages ----

    def record_answer(self, ask_id: str, approved: bool) -> None:
        if approved:
            self.approved.add(ask_id)
        else:
            self.approved.discard(ask_id)

    def user_stop(self) -> None:
        self.stopped = True
        self.outbox.put_nowait({"type": "stop"})

    # ---- commands ----

    def handle(self, command: Command) -> Result:
        if command.command_id in self.results:
            return self.results[command.command_id]
        result = self._run(command)
        if command.action != "read_screen":
            self.latest_id = None
        self.results[command.command_id] = result
        while len(self.results) > REMEMBERED_COMMANDS:
            self.results.popitem(last=False)
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
        if command.action not in self.actions:
            return self._reply(command, "rejected", "unsupported", f"this phone can't {command.action}")
        handlers: dict[str, Callable[[Command], Result]] = {
            "read_screen": lambda c: self._reply(c, "ok"),
            "list_apps": lambda c: self._reply(c, "ok"),
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
        screen = self.read_screen() if command.action == "read_screen" else None
        return Result(command_id=command.command_id, status=status, error=error, settled=True, screen=screen, **extra)

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
                return self._reply(command, "failed", "cant_submit", "text entered, but Enter isn't available here")
            el.submit()
        return self._reply(command, "ok", submitted=True if command.submit else None)

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
        result.image = _draw(self.screenshot_screen(), command.max_side or 720)
        return result

    def screenshot_screen(self) -> Screen:
        built = self.app.snapshot()
        return Screen(screen_id="shot", package_name=built.package, width=1080, height=2400, nodes=built.nodes())


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
                         auto_yes: bool = False, on_event: Callable[[Any], Any] | None = None) -> Done | None:
    phone = phone or FakePhone()
    if isinstance(answers, list):
        answers = list(answers)
    emit = on_event or (lambda message: None)
    open_asks: dict[str, asyncio.Task] = {}
    summary: str | None = None

    async with connect(url, max_size=MAX_FRAME, proxy=None) as socket:
        sender = asyncio.create_task(_send_outbox(socket, phone))
        try:
            start = {"type": "start", "task": task_text, "device": {"width": 1080, "height": 2400, "sdk": 36}}
            await socket.send(json.dumps(start))
            async for raw in socket:
                message = json.loads(raw)
                if message["type"] == "command":
                    command = _command_from_wire(message)
                    await _learn_task_id(phone, url)
                    result = phone.handle(command)
                    if phone.drop_now:
                        phone.drop_now = False
                        socket.transport.abort()
                        return None
                    outcome = emit(command)
                    if inspect.isawaitable(outcome):
                        await outcome
                    if phone.late_result:
                        await asyncio.sleep(phone.late_result)
                    if result.status == "unknown":
                        continue
                    try:
                        await socket.send(json.dumps(_result_to_wire(result, command, phone)))
                    except ConnectionClosed:
                        pass  # still read whatever the backend sent before closing
                    continue
                if message["type"] == "ask":
                    ask = _ask_from_wire(message)
                    open_asks[ask.ask_id] = asyncio.create_task(_answer(socket, phone, ask, answers, auto_yes))
                    outcome = emit(ask)
                    if inspect.isawaitable(outcome):
                        await outcome
                if message["type"] in ("done", "error"):
                    summary = message.get("summary") or message.get("message") or ""
        except ConnectionClosed:
            return None
        finally:
            sender.cancel()
            for task in open_asks.values():
                task.cancel()
    return await _finished(phone, url, summary or "") if phone.task_id else None


async def _learn_task_id(phone: FakePhone, url: str) -> None:
    if phone.task_id:
        return
    async with httpx.AsyncClient(base_url=url.replace("ws://", "http://").removesuffix("/agent")) as client:
        running = (await client.get("/status")).json()["running"]
    phone.task_id = running[0]["task_id"] if running else None


async def _finished(phone: FakePhone, url: str, summary: str) -> Done:
    base_url = url.replace("ws://", "http://").removesuffix("/agent")
    async with httpx.AsyncClient(base_url=base_url) as client:
        for _ in range(100):
            tasks = (await client.get("/tasks", params={"limit": 1})).json()["tasks"]
            if not tasks:
                return Done.model_construct(task_id="", outcome="unknown", summary=summary)
            if tasks[0]["status"] != "running":
                break
            await asyncio.sleep(0.02)
    phone.task_id = tasks[0]["task_id"]
    return Done(task_id=tasks[0]["task_id"], outcome=tasks[0]["status"], summary=summary)


def _command_from_wire(message: dict) -> Command:
    action = next(action for action, name in COMMAND_NAMES.items() if name == message["name"])
    args = message["args"]
    return Command(command_id=message["id"], action=action, screen_id=args.get("screenId"), target=args.get("index"),
                   text=args.get("text"), submit=args.get("submit"), direction=args.get("direction"), package_name=args.get("name"),
                   confirmed=args.get("confirmed"), max_side=args.get("maxWidth"))


def _ask_from_wire(message: dict) -> Ask:
    question = message["question"]
    found = re.search(r" \((.+ / .+)\)$", question)
    kind = "confirm" if found and found.group(1) == "Yes / No" else "question"
    return Ask(ask_id=message["id"], kind=kind, text=question)


def _result_to_wire(result: Result, command: Command, phone: FakePhone) -> dict:
    if result.status != "ok":
        error = result.error
        wire = {"type": "result", "id": result.command_id, "ok": False, "error": WIRE_ERRORS.get(error.code, "actionFailed"),
                "message": error.message}
        return wire
    data: dict = {}
    if command.action == "read_screen":
        data = {"screenId": result.screen.screen_id, "package": result.screen.package_name,
                "nodes": [_node_to_wire(node) for node in result.screen.nodes]}
    elif command.action == "screenshot":
        data = {"png": result.image.data, "width": result.image.width, "height": result.image.height}
    elif command.action == "list_apps":
        data = {"apps": [{"label": label, "package": package} for package, label in APPS.items()]}
    elif command.action == "type_text" and result.submitted is not None:
        data = {"submitted": result.submitted}
    return {"type": "result", "id": result.command_id, "ok": True, "data": data}


def _node_to_wire(node: Node) -> dict:
    return {"index": node.index, "class": node.class_name, "text": node.text or node.hint, "desc": node.description,
            "id": node.resource_id, "bounds": list(node.bounds), "clickable": node.clickable,
            "editable": node.editable, "scrollable": node.scrollable,
            "checked": node.checked if node.checkable else None}


WIRE_ERRORS = {
    "cant_submit": "cantSubmit",
    "stale_screen": "staleScreen", "stale_target": "nodeNotFound", "bad_target": "nodeNotFound",
    "app_not_found": "appNotFound", "screenshot_blocked": "secureWindow", "stopped": "actionFailed",
}


async def _send_outbox(socket, phone: FakePhone) -> None:
    while True:
        message = await phone.outbox.get()
        try:
            await socket.send(json.dumps(message))
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
        value = True if ask.kind == "confirm" else "yes"
    else:
        value = None
    text = _answer_text(value)
    if text is None:
        return
    try:
        await socket.send(json.dumps({"type": "answer", "id": ask.ask_id, "text": text}))
    except ConnectionClosed:
        pass


def _answer_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Answer):
        value = value.text if value.text is not None else value.approved
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
