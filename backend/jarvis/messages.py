import json
import secrets
import uuid
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

MAX_RESULT_CHARS = 12_000_000
MAX_OTHER_CHARS = 64_000
MAX_TEXT = 2000
MAX_NODE_TEXT = 500
MAX_NODES = 1500
MAX_APPS = 500
MAX_IMAGE_CHARS = 11_000_000
MAX_PROGRESS_TEXT = 120

ACTIONS = ("read_screen", "tap", "type_text", "scroll", "go_back", "go_home", "open_app", "screenshot")
DIRECTIONS = ("down", "up", "left", "right")

Status = Literal["ok", "failed", "rejected", "unknown"]
Direction = Literal["down", "up", "left", "right"]
Action = Literal["read_screen", "tap", "type_text", "scroll", "go_back", "go_home", "open_app", "screenshot"]


class Message(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---- the screen ----

class Node(Message):
    index: int
    parent: int | None = None
    text: str | None = None
    description: str | None = None
    hint: str | None = None
    resource_id: str | None = None
    class_name: str | None = None
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    clickable: bool = False
    long_clickable: bool = False
    editable: bool = False
    scrollable: bool = False
    checkable: bool = False
    checked: bool = False
    selected: bool = False
    focused: bool = False
    heading: bool = False
    password: bool = False
    enabled: bool = True
    scroll: list[Direction] = []
    state: str | None = None
    error: str | None = None

    @field_validator("scroll", mode="before")
    @classmethod
    def known_directions_only(cls, value):
        if not isinstance(value, list):
            return []
        return [d for d in value if d in DIRECTIONS]


class Screen(Message):
    screen_id: str
    at: int | None = None
    package_name: str = ""
    title: str | None = None
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    rotation: int = 0
    keyboard_top: int | None = None
    loading: bool = False
    truncated: bool = False
    note: str | None = None
    nodes: list[Node] = []


# ---- phone -> backend ----

class Device(Message):
    id: str = Field(min_length=1, max_length=100)
    name: str | None = None
    sdk_int: int | None = None


class App(Message):
    label: str
    package_name: str


class Hello(Message):
    type: Literal["hello"] = "hello"
    protocol: int
    device: Device
    app_version: str | None = None
    actions: list[str] = []
    apps: list[App] = []


class TaskRequest(Message):
    type: Literal["task"] = "task"
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class ErrorInfo(Message):
    code: str
    message: str = ""


class LastResult(Message):
    command_id: str
    status: Status
    error: ErrorInfo | None = None


class Rejoin(Message):
    type: Literal["rejoin"] = "rejoin"
    task_id: str
    rejoin_key: str
    last_result: LastResult | None = None


class Image(Message):
    format: Literal["jpeg", "png"]
    data: str = Field(max_length=MAX_IMAGE_CHARS)
    width: int
    height: int


class Result(Message):
    type: Literal["result"] = "result"
    command_id: str
    status: Status
    error: ErrorInfo | None = None
    settled: bool = False
    settle_ms: int | None = None
    acted_on: int | None = None
    method: Literal["action", "gesture"] | None = None
    screen: Screen | None = None
    image: Image | None = None


class Answer(Message):
    type: Literal["answer"] = "answer"
    ask_id: str
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    approved: bool | None = None


class Pause(Message):
    type: Literal["pause"] = "pause"


class Resume(Message):
    type: Literal["resume"] = "resume"


class Stop(Message):
    type: Literal["stop"] = "stop"


class Update(Message):
    type: Literal["update"] = "update"
    text: str = Field(min_length=1, max_length=MAX_TEXT)


PhoneMessage = Annotated[
    Union[Hello, TaskRequest, Rejoin, Result, Answer, Pause, Resume, Stop, Update],
    Field(discriminator="type"),
]
PHONE_TYPES = {"hello", "task", "rejoin", "result", "answer", "pause", "resume", "stop", "update"}


# ---- backend -> phone ----

class Ready(Message):
    type: Literal["ready"] = "ready"
    task_id: str
    rejoin_key: str
    revision: int = 0
    rejoined: bool = False


class Command(Message):
    type: Literal["command"] = "command"
    command_id: str
    action: Action
    revision: int = 0
    timeout_ms: int = 10_000
    screen_id: str | None = None
    target: int | None = None
    text: str | None = None
    submit: bool | None = None
    direction: Direction | None = None
    package_name: str | None = None
    confirmed: bool | None = None
    ask_id: str | None = None
    must_see: list[str] | None = None
    max_side: int | None = None


class Ask(Message):
    type: Literal["ask"] = "ask"
    ask_id: str
    kind: Literal["question", "confirm"]
    text: str
    choices: list[str] | None = None
    expires_in: int


class AskCancel(Message):
    type: Literal["ask_cancel"] = "ask_cancel"
    ask_id: str
    reason: Literal["expired", "replaced", "stopped", "paused"]


class Progress(Message):
    type: Literal["progress"] = "progress"
    state: Literal["running", "thinking", "waiting_for_user", "waiting_for_approval", "paused"]
    text: str
    step: int = 0
    revision: int = 0

    @field_validator("text", mode="before")
    @classmethod
    def short_text(cls, value):
        value = str(value)
        return value if len(value) <= MAX_PROGRESS_TEXT else value[: MAX_PROGRESS_TEXT - 1] + "…"


class LastAction(Message):
    label: str
    status: str


class Done(Message):
    type: Literal["done"] = "done"
    task_id: str
    outcome: Literal["completed", "unconfirmed", "failed", "stopped", "needs_user"]
    summary: str
    proof: list[str] | None = None
    last_action: LastAction | None = None


class ErrorMessage(Message):
    type: Literal["error"] = "error"
    code: str
    message: str
    fatal: bool = False
    task_id: str | None = None


BackendMessage = Annotated[
    Union[Ready, Command, Ask, AskCancel, Progress, Done, ErrorMessage],
    Field(discriminator="type"),
]
BACKEND_TYPES = {"ready", "command", "ask", "ask_cancel", "progress", "done", "error"}

_read_phone_message = TypeAdapter(PhoneMessage)
_read_backend_message = TypeAdapter(BackendMessage)


class BadMessage(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def to_json(message: BaseModel) -> str:
    return message.model_dump_json(exclude_none=True)


def parse_phone_message(raw: str | bytes):
    if isinstance(raw, (bytes, bytearray)):
        raise BadMessage("bad_message", "binary frames are not allowed")
    if len(raw) > MAX_RESULT_CHARS:
        raise BadMessage("bad_message", "message is too large")
    data = _load_object(raw)
    kind = data.get("type")
    if kind not in PHONE_TYPES:
        raise BadMessage("unexpected_message", f"unknown message type {kind!r}")
    if kind != "result" and len(raw) > MAX_OTHER_CHARS:
        raise BadMessage("bad_message", f"{kind} message is too large")
    _trim(data)
    try:
        return _read_phone_message.validate_python(data)
    except ValidationError as error:
        raise BadMessage("bad_message", _first_problem(error)) from None


def parse_backend_message(raw: str | bytes):
    if isinstance(raw, (bytes, bytearray)):
        raise BadMessage("bad_message", "binary frames are not allowed")
    data = _load_object(raw)
    if data.get("type") not in BACKEND_TYPES:
        raise BadMessage("unexpected_message", f"unknown message type {data.get('type')!r}")
    try:
        return _read_backend_message.validate_python(data)
    except ValidationError as error:
        raise BadMessage("bad_message", _first_problem(error)) from None


def clean_screen(screen: Screen) -> Screen:
    seen: set[int] = set()
    nodes = []
    for node in screen.nodes:
        if node.index in seen:
            continue
        left, top, right, bottom = node.bounds
        left, right = _clamp(left, screen.width), _clamp(right, screen.width)
        top, bottom = _clamp(top, screen.height), _clamp(bottom, screen.height)
        if right <= left or bottom <= top:
            continue
        parent = node.parent if node.parent in seen else None
        seen.add(node.index)
        nodes.append(node.model_copy(update={"bounds": (left, top, right, bottom), "parent": parent}))
    return screen.model_copy(update={"nodes": nodes})


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def new_rejoin_key() -> str:
    return secrets.token_urlsafe(18)


def _load_object(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except ValueError:
        raise BadMessage("bad_message", "not valid JSON") from None
    if not isinstance(data, dict):
        raise BadMessage("bad_message", "expected a JSON object")
    return data


def _trim(data: dict) -> None:
    screen = data.get("screen")
    if isinstance(screen, dict) and isinstance(screen.get("nodes"), list):
        nodes = screen["nodes"]
        if len(nodes) > MAX_NODES:
            screen["nodes"] = nodes[:MAX_NODES]
            screen["truncated"] = True
        for node in screen["nodes"]:
            if isinstance(node, dict):
                for key in ("text", "description", "hint"):
                    if isinstance(node.get(key), str) and len(node[key]) > MAX_NODE_TEXT:
                        node[key] = node[key][:MAX_NODE_TEXT]
    if isinstance(data.get("apps"), list):
        data["apps"] = data["apps"][:MAX_APPS]


def _first_problem(error: ValidationError) -> str:
    problem = error.errors()[0]
    where = ".".join(str(part) for part in problem.get("loc", ()))
    return f"{where}: {problem.get('msg', 'invalid value')}" if where else problem.get("msg", "invalid value")


def _clamp(value: int, limit: int) -> int:
    return max(0, min(value, limit))
