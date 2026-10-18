import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from src.phone.messages import (
    Answer,
    Ask,
    BadMessage,
    Command,
    Done,
    ErrorInfo,
    ErrorMessage,
    Message,
    Pause,
    Progress,
    Result,
    Resume,
    Start,
    Stop,
    WireResult,
    sanitize_screen,
    new_id,
    parse_phone_message,
)
from src.phone.wire import message_to_wire, result_from_wire

log = logging.getLogger("jarvis.phone")

MAX_BAD_MESSAGES = 5
TIMEOUT_MS = {"read_screen": 5_000, "open_app": 15_000, "screenshot": 8_000}
DEFAULT_TIMEOUT_MS = 10_000
LARGE_FRAME_CHARS = 16_000  # --debug logs the size and parse time of frames at least this big
APPROVE_WORDS = {"yes", "y", "yeah", "yep", "ok", "okay", "sure", "confirm", "approve", "go ahead"}
DECLINE_WORDS = {"no", "n", "nope", "cancel", "stop", "don't"}


class Interrupted(Exception):
    """The user pressed Stop or Pause."""


class PhoneDisconnected(Interrupted):
    pass


@dataclass
class AskOutcome:
    status: Literal["approved", "declined", "text", "expired", "cancelled", "secret"]
    ask_id: str = ""
    text: str = ""
    secret_ref: str | None = None


def new_command(action: str, **fields) -> Command:
    timeout_ms = TIMEOUT_MS.get(action, DEFAULT_TIMEOUT_MS)
    return Command(command_id=new_id("c"), action=action, timeout_ms=timeout_ms, **fields)


class PhoneLink:
    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.width = 0
        self.height = 0
        self.apps: dict[str, str] = {}
        self.capabilities: set[str] = set()
        self.stop_requested = False
        self.pause_requested = False
        self.disconnected = False
        self.closed = False
        self.on_late_result: Callable[[Result], None] | None = None
        self.bad_messages = 0
        self._open_command: tuple[str, asyncio.Future, Command] | None = None
        self._open_ask: tuple[str, asyncio.Future, str] | None = None
        self._late_commands: dict[str, Command] = {}
        self._send_lock = asyncio.Lock()
        self._state_changed = asyncio.Event()  # replaced on every change so each waiter sees one pulse
        self._reader: asyncio.Task | None = None

    def start(self, start: Start) -> None:
        self.width, self.height = start.device.width, start.device.height
        self.capabilities = set(start.device.capabilities)
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
                log.info("The phone sent a binary frame; closing.")
                await self.close(1003)
                return None
            started = time.monotonic()
            try:
                message = parse_phone_message(raw)
            except BadMessage as problem:
                await self.reject(problem.message)
                continue
            if len(raw) >= LARGE_FRAME_CHARS:
                log.debug("got a %d KB %s, parsed in %d ms", len(raw) // 1024, message.type, _ms_since(started))
            return message
        return None

    async def reject(self, message: str) -> None:
        self.bad_messages += 1
        log.info("Bad message from the phone: %s", message)
        if self.bad_messages >= MAX_BAD_MESSAGES:
            log.info("Too many bad messages; closing.")
            await self.close(1003)
            return
        await self.send(ErrorMessage(message=message))

    async def send(self, message: Message, check_stop: bool = False) -> None:
        async with self._send_lock:
            if self.disconnected or self.closed:
                raise PhoneDisconnected()
            if check_stop and (self.stop_requested or self.pause_requested):
                raise Interrupted()
            try:
                await self.ws.send_text(json.dumps(message_to_wire(message)))
            except (OSError, RuntimeError, WebSocketDisconnect):
                self._mark_disconnected()
                raise PhoneDisconnected() from None

    async def run(self, command: Command, *, grace: float = 5.0) -> Result:
        assert self._open_command is None, "a command is already open"
        future = asyncio.get_running_loop().create_future()
        self._open_command = (command.command_id, future, command)
        started = time.monotonic()
        try:
            await self.send(command, check_stop=True)
            try:
                # shield keeps a late result matchable; the wait is not cut short by Stop
                result = await asyncio.wait_for(asyncio.shield(future), command.timeout_ms / 1000 + grace)
                log.debug("%s: %s in %d ms", command.action, result.status, _ms_since(started))
                return result
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

    async def ask(self, kind: str, text: str, choices: list[str] | None = None, expires_in: int = 600, *, secret_target=None,
                  while_paused: bool = False) -> AskOutcome:
        ask_id = new_id("q")
        future = asyncio.get_running_loop().create_future()
        self._open_ask = (ask_id, future, kind)
        try:
            try:
                await self.send(Ask(ask_id=ask_id, kind=kind, text=format_question(text, choices), secret_target=secret_target), check_stop=not while_paused)
            except PhoneDisconnected:
                raise
            except Interrupted:
                return AskOutcome("cancelled", ask_id)
            deadline = asyncio.get_running_loop().time() + expires_in
            while not future.done():
                if self.disconnected:
                    raise PhoneDisconnected()
                # A question asked during a pause ends when the user resumes instead.
                if self.stop_requested or self.pause_requested != while_paused:
                    return AskOutcome("cancelled", ask_id)
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return AskOutcome("expired", ask_id)
                await self._wait_for_change(future, remaining)
            return _to_outcome(ask_id, kind, future.result())
        finally:
            self._open_ask = None

    async def run_interruptible(self, awaitable: Awaitable):
        job = asyncio.ensure_future(awaitable)
        try:
            while not job.done():
                if self.disconnected:
                    raise PhoneDisconnected()
                if self.stop_requested or self.pause_requested:
                    raise Interrupted()
                await self._wait_for_change(job, None)
            return job.result()
        finally:
            if not job.done():
                job.cancel()
                await asyncio.wait({job})

    async def progress(self, state: str, text: str, step: int = 0) -> None:
        await self.send(Progress(state=state, text=text, step=step))

    async def wait_while_paused(self) -> None:
        while self.pause_requested and not self.stop_requested:
            if self.disconnected:
                raise PhoneDisconnected()
            await self._state_changed.wait()
        if self.disconnected:
            raise PhoneDisconnected()

    async def finish(self, done: Done) -> None:
        if not self.stop_requested:
            await self.send(done)
        await self.close(1000)

    async def fail(self, message: str) -> None:
        try:
            await self.send(ErrorMessage(message=message))
        except PhoneDisconnected:
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
        self._mark_disconnected()
        if self._reader and self._reader is not asyncio.current_task():
            self._reader.cancel()

    async def _wait_for_change(self, future: asyncio.Future, timeout: float | None) -> None:
        change = asyncio.ensure_future(self._state_changed.wait())
        try:
            await asyncio.wait({future, change}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            change.cancel()

    def _signal_change(self) -> None:
        self._state_changed.set()
        self._state_changed = asyncio.Event()

    def _mark_disconnected(self) -> None:
        if self.disconnected:
            return
        self.disconnected = True
        for opened in (self._open_command, self._open_ask):
            if opened and not opened[1].done():
                opened[1].set_exception(PhoneDisconnected())
                opened[1].exception()  # mark retrieved so an unawaited future doesn't warn
        self._signal_change()

    async def _read(self) -> None:
        try:
            while (message := await self.receive()) is not None:
                await self._dispatch(message)
        except PhoneDisconnected:
            pass
        finally:
            if not self.disconnected:
                log.info("The phone disconnected.")
            self._mark_disconnected()

    async def _dispatch(self, message) -> None:
        if isinstance(message, WireResult):
            self._resolve_result(message)
        elif isinstance(message, Answer):
            if self._open_ask and self._open_ask[0] == message.ask_id and not self._open_ask[1].done():
                self._open_ask[1].set_result(message)
            else:
                log.info("Ignored an answer to %s (no open question with that id).", message.ask_id)
        elif isinstance(message, Stop):
            self.stop_requested = True
            log.info("The user pressed Stop.")
            self._signal_change()
        elif isinstance(message, (Pause, Resume)):
            if not self.stop_requested:
                self.pause_requested = isinstance(message, Pause)
                self._signal_change()
        else:
            await self.reject(f"{message.type} is not expected now")

    def _resolve_result(self, wire: WireResult) -> None:
        if self._open_command and self._open_command[0] == wire.id:
            result = self._to_result(wire, self._open_command[2])
            if not self._open_command[1].done():
                self._open_command[1].set_result(result)
            return
        late = self._late_commands.pop(wire.id, None)
        if late:
            result = self._to_result(wire, late)
            log.info("Late result for %s: %s", wire.id, result.status)
            if self.on_late_result:
                self.on_late_result(result)
            return
        log.info("Ignored a result for unknown command %s.", wire.id)

    def _to_result(self, wire: WireResult, command: Command) -> Result:
        result = result_from_wire(wire, command, self.width, self.height)
        if result.screen:
            result.screen = sanitize_screen(result.screen)
        return result


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def format_question(text: str, choices: list[str] | None) -> str:
    return f"{text} ({' / '.join(choices)})" if choices else text


def _to_outcome(ask_id: str, kind: str, answer: Answer) -> AskOutcome:
    if kind == "secret":
        if answer.source == "voice" and answer.secret_ref:
            return AskOutcome("secret", ask_id, secret_ref=answer.secret_ref)
        # Legacy/manual clients may only acknowledge direct entry on the phone.
        manual = (answer.text or "").casefold().strip(" .!?")
        return AskOutcome("text", ask_id, text="done" if manual in {"done", "entered", "finished"} else "manual entry needed")
    reply = (answer.text or "").strip()
    spoken = reply.casefold().strip(" .,!?")  # speech-to-text adds punctuation: "Yes."
    if kind == "confirm" and spoken in APPROVE_WORDS:
        return AskOutcome("approved", ask_id)
    if kind == "confirm" and spoken in DECLINE_WORDS | {""}:
        return AskOutcome("declined", ask_id)
    return AskOutcome("text", ask_id, text=reply)
