import asyncio
import json
import logging
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
    Result,
    Start,
    Stop,
    WireResult,
    clean_screen,
    new_id,
    parse_phone_message,
)
from src.phone.wire import message_to_wire, result_from_wire

log = logging.getLogger("jarvis.phone")

MAX_BAD_MESSAGES = 5
TIMEOUT_MS = {"read_screen": 5_000, "open_app": 15_000, "screenshot": 8_000}
DEFAULT_TIMEOUT_MS = 10_000
YES_WORDS = {"yes", "y", "yeah", "yep", "ok", "okay", "sure", "confirm", "approve", "go ahead"}
NO_WORDS = {"no", "n", "nope", "cancel", "stop", "don't"}


class Interrupted(Exception):
    """The user pressed Stop."""


class PhoneGone(Interrupted):
    pass


@dataclass
class AskOutcome:
    status: Literal["approved", "declined", "text", "expired", "cancelled"]
    ask_id: str = ""
    text: str = ""


def new_command(action: str, **fields) -> Command:
    timeout_ms = TIMEOUT_MS.get(action, DEFAULT_TIMEOUT_MS)
    return Command(command_id=new_id("c"), action=action, timeout_ms=timeout_ms, **fields)


class PhoneLink:
    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.width = 0
        self.height = 0
        self.apps: dict[str, str] = {}
        self.stop_requested = False
        self.gone = False
        self.closed = False
        self.on_late_result: Callable[[Result], None] | None = None
        self.bad_messages = 0
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
                log.info("The phone sent a binary frame; closing.")
                await self.close(1003)
                return None
            try:
                return parse_phone_message(raw)
            except BadMessage as problem:
                await self.reject(problem.message)
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
            if self.gone or self.closed:
                raise PhoneGone()
            if check_stop and self.stop_requested:
                raise Interrupted()
            try:
                await self.ws.send_text(json.dumps(message_to_wire(message)))
            except (OSError, RuntimeError, WebSocketDisconnect):
                self._mark_gone()
                raise PhoneGone() from None

    async def run(self, command: Command, *, grace: float = 5.0) -> Result:
        assert self._open_command is None, "a command is already open"
        future = asyncio.get_running_loop().create_future()
        self._open_command = (command.command_id, future, command)
        try:
            await self.send(command, check_stop=True)
            try:
                # shield keeps a late result matchable; the wait is not cut short by Stop
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
                await self.send(Ask(ask_id=ask_id, kind=kind, text=with_choices(text, choices)), check_stop=True)
            except PhoneGone:
                raise
            except Interrupted:
                return AskOutcome("cancelled", ask_id)
            deadline = asyncio.get_running_loop().time() + expires_in
            while not future.done():
                if self.gone:
                    raise PhoneGone()
                if self.stop_requested:
                    return AskOutcome("cancelled", ask_id)
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return AskOutcome("expired", ask_id)
                await self._wait_for_change(future, remaining)
            return _outcome(ask_id, kind, future.result())
        finally:
            self._open_ask = None

    async def interruptible(self, awaitable: Awaitable):
        job = asyncio.ensure_future(awaitable)
        try:
            while not job.done():
                if self.gone:
                    raise PhoneGone()
                if self.stop_requested:
                    raise Interrupted()
                await self._wait_for_change(job, None)
            return job.result()
        finally:
            if not job.done():
                job.cancel()
                await asyncio.wait({job})

    async def finish(self, done: Done) -> None:
        if not self.stop_requested:
            await self.send(done)
        await self.close(1000)

    async def fail(self, message: str) -> None:
        try:
            await self.send(ErrorMessage(message=message))
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

    async def _wait_for_change(self, future: asyncio.Future, timeout: float | None) -> None:
        change = asyncio.ensure_future(self._poke.wait())
        try:
            await asyncio.wait({future, change}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            change.cancel()

    def _notify(self) -> None:
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
            log.info("The user pressed Stop.")
            self._notify()
        else:
            await self.reject(f"{message.type} is not expected now")

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
