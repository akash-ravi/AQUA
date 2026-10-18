import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from jarvis.messages import (
    ACTIONS,
    Answer,
    Ask,
    BadMessage,
    Command,
    Done,
    ErrorInfo,
    ErrorMessage,
    Message,
    Progress,
    Result,
    Start,
    Stop,
    Pause,
    Resume,
    WireResult,
    clean_screen,
    new_id,
    parse_phone_message,
)
from jarvis.wire import message_to_wire, result_from_wire

log = logging.getLogger("jarvis.phone")

MAX_BAD_MESSAGES = 5
TIMEOUT_MS = {"read_screen": 5_000, "open_app": 15_000, "screenshot": 8_000}
DEFAULT_TIMEOUT_MS = 10_000
ASK_CANCEL_REASONS = {"stopped": "stopped", "paused": "paused", "updated": "replaced"}
YES_WORDS = {"yes", "y", "yeah", "yep", "ok", "okay", "sure", "confirm", "approve", "go ahead"}
NO_WORDS = {"no", "n", "nope", "cancel", "stop", "don't"}


class Interrupted(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # stopped | paused | updated | gone


class PhoneGone(Interrupted):
    def __init__(self):
        super().__init__("gone")


@dataclass
class AskOutcome:
    status: Literal["approved", "declined", "text", "expired", "cancelled"]
    ask_id: str = ""
    text: str = ""
    reason: str = ""  # for cancelled: stopped | paused | replaced


def new_command(action: str, **fields) -> Command:
    timeout_ms = TIMEOUT_MS.get(action, DEFAULT_TIMEOUT_MS)
    return Command(command_id=new_id("c"), action=action, timeout_ms=timeout_ms, **fields)


class PhoneLink:
    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.width = 0
        self.height = 0
        self.actions: set[str] = set(ACTIONS)
        self.apps: dict[str, str] = {}
        self.revision = 0
        self.stop_requested = False
        self.stopped_by_phone = False
        self.pause_requested = False
        self.changed = asyncio.Event()
        self.gone = False
        self.closed = False
        self.on_late_result: Callable[[Result], None] | None = None
        self.last_progress: Progress | None = None
        self.bad_messages = 0
        self._updates: list[str] = []
        self._open_command: tuple[str, asyncio.Future, Command] | None = None
        self._open_ask: tuple[str, asyncio.Future, str] | None = None
        self._late_commands: dict[str, Command] = {}
        self._send_lock = asyncio.Lock()
        self._poke = asyncio.Event()  # replaced on every change so each waiter sees one pulse
        self._reader: asyncio.Task | None = None

    def start(self, start: Start) -> None:
        self.width, self.height = start.device.width, start.device.height
        self._reader = asyncio.create_task(self._read())

    async def load_apps(self) -> None:
        result = await self.run(new_command("list_apps"))
        if result.apps is None:
            log.info("The phone did not list its apps (%s).", result.error.code if result.error else result.status)
            return
        self.apps = {app.package_name: app.label for app in result.apps}

    async def receive(self):
        """Next valid phone message, or None once the socket is gone. Bad frames are answered here."""
        while not self.closed:
            try:
                frame = await self.ws.receive()
            except (WebSocketDisconnect, RuntimeError):
                return None
            if frame["type"] == "websocket.disconnect":
                return None
            raw = frame.get("text")
            if raw is None:
                raw = frame.get("bytes") or b""
            try:
                return parse_phone_message(raw)
            except BadMessage as problem:
                if isinstance(raw, bytes):
                    log.info("The phone sent a binary frame; closing.")
                    await self.close(1003)
                    return None
                await self.reject(problem.code, problem.message)
        return None

    async def reject(self, code: str, message: str) -> None:
        self.bad_messages += 1
        log.info("Bad message from the phone: %s", message)
        if self.bad_messages >= MAX_BAD_MESSAGES:
            log.info("Too many bad messages; closing.")
            await self.close(1003)
            return
        await self.send_error(code, message)

    async def send(self, message: Message, check: Callable[[], None] | None = None) -> None:
        async with self._send_lock:
            if check:
                check()
            if self.gone or self.closed:
                raise PhoneGone()
            payload = message_to_wire(message)
            if payload is None:
                return
            try:
                await self.ws.send_text(json.dumps(payload))
            except (OSError, RuntimeError, WebSocketDisconnect):
                self._mark_gone()
                raise PhoneGone() from None

    async def send_error(self, code: str, message: str, *, task_id: str | None = None) -> None:
        await self.send(ErrorMessage(code=code, message=message, task_id=task_id))

    async def run(self, command: Command, *, grace: float = 5.0) -> Result:
        assert self._open_command is None, "a command is already open"
        future = asyncio.get_running_loop().create_future()
        self._open_command = (command.command_id, future, command)
        try:
            await self.send(command, check=lambda: self._check_can_send(command))
            try:
                # shield keeps a late result matchable; the wait is not cut short by stop/pause/update
                return await asyncio.wait_for(asyncio.shield(future), command.timeout_ms / 1000 + grace)
            except TimeoutError:
                log.info("No result for %s %s in time.", command.action, command.command_id)
                self._late_commands[command.command_id] = command
                return Result(
                    command_id=command.command_id,
                    status="unknown",
                    error=ErrorInfo(code="no_result", message="the phone did not answer in time"),
                )
        finally:
            self._open_command = None

    async def ask(self, kind: str, text: str, choices: list[str] | None = None, expires_in: int = 600) -> AskOutcome:
        ask_id = new_id("q")
        future = asyncio.get_running_loop().create_future()
        self._open_ask = (ask_id, future, kind)
        try:
            try:
                await self.send(
                    Ask(ask_id=ask_id, kind=kind, text=with_choices(text, choices)),
                    check=self._check_can_ask,
                )
            except PhoneGone:
                raise
            except Interrupted as stopped_first:
                return AskOutcome("cancelled", ask_id, reason=ASK_CANCEL_REASONS[stopped_first.reason])
            deadline = asyncio.get_running_loop().time() + expires_in
            while not future.done():
                reason = self._interruption(include_pause=True)
                if reason == "gone":
                    raise PhoneGone()
                if reason:
                    return await self._cancel_ask(ask_id, ASK_CANCEL_REASONS[reason])
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return await self._cancel_ask(ask_id, "expired")
                await self._wait_for_change(future, remaining)
            return _outcome(ask_id, kind, future.result())
        finally:
            self._open_ask = None

    async def interruptible(self, awaitable: Awaitable, *, include_pause: bool = True):
        job = asyncio.ensure_future(awaitable)
        try:
            while not job.done():
                reason = self._interruption(include_pause)
                if reason == "gone":
                    raise PhoneGone()
                if reason:
                    raise Interrupted(reason)
                await self._wait_for_change(job, None)
            return job.result()
        finally:
            if not job.done():
                job.cancel()
                await asyncio.wait({job})

    async def progress(self, state: str, text: str, step: int = 0) -> None:
        was_paused = self.last_progress is not None and self.last_progress.state == "paused"
        self.last_progress = Progress(state=state, text=text, step=step)
        if state == "paused" or was_paused:
            await self.send(self.last_progress)

    def request_stop(self) -> None:
        """Stop asked for from outside the task's own connection (the app's HTTP stop button)."""
        self.stop_requested = True
        log.info("Stop requested through the app.")
        self._notify()

    async def finish(self, done: Done) -> None:
        if not self.stopped_by_phone:
            await self.send(done)
        await self.close(1000)

    async def fail(self, code: str, message: str, *, task_id: str | None = None) -> None:
        try:
            await self.send_error(code, message, task_id=task_id)
        except PhoneGone:
            pass
        await self.close(1000)

    async def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            async with self._send_lock:
                await self.ws.close(code)
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass
        self._mark_gone()
        if self._reader and self._reader is not asyncio.current_task():
            self._reader.cancel()

    def pending_updates(self) -> list[str]:
        updates, self._updates = self._updates, []
        return updates

    async def wait_changed(self) -> None:
        await self.changed.wait()
        self.changed.clear()

    def _check_can_send(self, command: Command) -> None:
        if self.gone:
            raise PhoneGone()
        if self.stop_requested:
            raise Interrupted("stopped")
        if self.pause_requested:
            raise Interrupted("paused")

    def _check_can_ask(self) -> None:
        reason = self._interruption(include_pause=True)
        if reason == "gone":
            raise PhoneGone()
        if reason:
            raise Interrupted(reason)

    def _interruption(self, include_pause: bool) -> str | None:
        if self.gone:
            return "gone"
        if self.stop_requested:
            return "stopped"
        if include_pause and self.pause_requested:
            return "paused"
        if self._updates:
            return "updated"
        return None

    async def _cancel_ask(self, ask_id: str, reason: str) -> AskOutcome:
        return AskOutcome("expired" if reason == "expired" else "cancelled", ask_id, reason=reason)

    async def _wait_for_change(self, future: asyncio.Future, timeout: float | None) -> None:
        change = asyncio.ensure_future(self._poke.wait())
        try:
            await asyncio.wait({future, change}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            change.cancel()

    def _notify(self) -> None:
        self.changed.set()
        self._poke.set()
        self._poke = asyncio.Event()

    def _mark_gone(self) -> None:
        if self.gone:
            return
        self.gone = True
        for opened in (self._open_command, self._open_ask):
            if opened and not opened[1].done():
                opened[1].set_exception(PhoneGone())
                opened[1].exception()  # mark retrieved so an unawaited future doesn't warn
        self._notify()

    async def _read(self) -> None:
        try:
            while (message := await self.receive()) is not None:
                await self._dispatch(message)
        except PhoneGone:
            pass
        finally:
            if not self.gone:
                log.info("The phone disconnected.")
            self._mark_gone()

    async def _dispatch(self, message) -> None:
        if isinstance(message, WireResult):
            self._take_result(message)
        elif isinstance(message, Answer):
            if self._open_ask and self._open_ask[0] == message.ask_id and not self._open_ask[1].done():
                self._open_ask[1].set_result(message)
            else:
                log.info("Ignored an answer to %s (no open question with that id).", message.ask_id)
        elif isinstance(message, Stop):
            self.stop_requested = True
            self.stopped_by_phone = True
            log.info("The user pressed Stop.")
            self._notify()
        elif isinstance(message, (Pause, Resume)):
            if not self.stop_requested:
                self.pause_requested = isinstance(message, Pause)
                self._notify()
        else:
            await self.reject("unexpected_message", f"{message.type} is not expected now")

    def _take_result(self, wire: WireResult) -> None:
        if self._open_command and self._open_command[0] == wire.id:
            result = self._convert(wire, self._open_command[2])
            if not self._open_command[1].done():
                self._open_command[1].set_result(result)
            return
        late = self._late_commands.pop(wire.id, None)
        if late:
            result = self._convert(wire, late)
            log.info("Late result for %s: %s", wire.id, result.status)
            if self.on_late_result:
                self.on_late_result(result)
            return
        log.info("Ignored a result for unknown command %s.", wire.id)

    def _convert(self, wire: WireResult, command: Command) -> Result:
        result = result_from_wire(wire, command, self.width, self.height)
        if result.screen:
            result.screen = clean_screen(result.screen)
        return result


def with_choices(text: str, choices: list[str] | None) -> str:
    return f"{text} ({' / '.join(choices)})" if choices else text


def _outcome(ask_id: str, kind: str, answer: Answer) -> AskOutcome:
    reply = (answer.text or "").strip()
    if kind == "confirm" and reply.casefold() in YES_WORDS:
        return AskOutcome("approved", ask_id)
    if kind == "confirm" and reply.casefold() in NO_WORDS | {""}:
        return AskOutcome("declined", ask_id)
    return AskOutcome("text", ask_id, text=reply)
