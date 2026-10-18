import json
import re
from dataclasses import dataclass

from jarvis.llm.common import BadReply


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # string | integer | boolean | array
    description: str
    required: bool = True
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: tuple[Param, ...]
    needs: str | None = None  # the phone action this tool depends on


WHY = Param("why", "string", "Short reason the user sees, under 12 words.")
TARGET = Param("target", "integer", "Number of the item on the current screen, like 12 for [12].")
TARGET_TEXT = Param("target_text", "string", "That item's label as shown on the screen.")
REMEMBER = Param("remember", "string", "A fact to keep for later steps.", required=False)

TOOLS: tuple[Tool, ...] = (
    Tool("tap", "Tap one item on the current screen.", (WHY, TARGET, TARGET_TEXT, REMEMBER), "tap"),
    Tool("type_text", "Replace the text in a box. submit=true runs a search.",
         (WHY, TARGET, TARGET_TEXT, Param("text", "string", "The full text for the box."),
          Param("submit", "boolean", "Press Enter after typing, to run a search.", required=False), REMEMBER),
         "type_text"),
    Tool("scroll", "Scroll a list. down shows what is below. No target means the main list.",
         (WHY, Param("direction", "string", "Which way to move the content into view.",
                     choices=("down", "up", "left", "right")),
          Param("target", "integer", "Number of the list to scroll.", required=False), REMEMBER),
         "scroll"),
    Tool("go_back", "Press the phone's Back button.", (WHY, REMEMBER), "go_back"),
    Tool("go_home", "Go to the phone's home screen.", (WHY,), "go_home"),
    Tool("open_app", "Open an app on the phone by its name.",
         (WHY, Param("name", "string", "The app's name, like DoorDash."), REMEMBER), "open_app"),
    Tool("wait", "Wait for the screen to finish loading, then look again.",
         (WHY, Param("seconds", "integer", "How long to wait, 1 to 10 seconds.", required=False))),
    Tool("look", "Get a picture of the screen when the text is not enough.", (WHY,), "screenshot"),
    Tool("ask_user", "Ask the user a question and wait for the answer.",
         (Param("question", "string", "The question, short and plain."),
          Param("choices", "array", "Up to 6 short answers the user can pick.", required=False), REMEMBER)),
    Tool("confirm_and_tap", "Ask the user to approve, then tap a button that can't be undone.",
         (WHY, TARGET, TARGET_TEXT,
          Param("summary", "string", "What will happen, with the items and the total shown on screen."))),
    Tool("finish", "End the task when the current screen proves it is done.",
         (Param("summary", "string", "What was done, in one or two sentences."),
          Param("proof", "array", "1 to 3 short phrases copied exactly from the current screen."))),
    Tool("give_up", "Stop because the task can't be done, and say why.",
         (Param("reason", "string", "Why the task can't be finished."),)),
)

ALIASES = {
    "click": "tap", "press": "tap", "select": "tap", "tap_element": "tap",
    "type": "type_text", "input_text": "type_text", "enter_text": "type_text", "set_text": "type_text",
    "back": "go_back", "home": "go_home",
    "open": "open_app", "launch": "open_app", "launch_app": "open_app",
    "screenshot": "look", "look_at_screen": "look",
    "ask": "ask_user",
    "confirm": "confirm_and_tap", "confirm_tap": "confirm_and_tap",
    "done": "finish", "complete": "finish", "task_complete": "finish",
    "abort": "give_up", "fail": "give_up",
    "swipe": "scroll",
}
DIRECTION_WORDS = {"downward": "down", "downwards": "down", "next": "down", "upward": "up", "upwards": "up",
                   "previous": "up", "forward": "down", "backward": "up"}
ARG_ALIASES = {"index": "target", "element": "target", "target_index": "target", "label": "target_text",
               "app": "name", "app_name": "name"}
TOOL_KEYS = ("tool", "name", "action", "function")
ARGS_KEYS = ("args", "arguments", "parameters", "input")


def tools_for(actions: set[str], images: bool) -> list[Tool]:
    def offered(tool: Tool) -> bool:
        if tool.name == "look":
            return images and "screenshot" in actions
        return tool.needs is None or tool.needs in actions
    return [tool for tool in TOOLS if offered(tool)]


def _schema(param: Param) -> dict:
    if param.type == "array":
        schema = {"type": "array", "items": {"type": "string"}}
    else:
        schema = {"type": param.type}
    if param.choices:
        schema["enum"] = list(param.choices)
    return schema


def openai_tools(tools: list[Tool]) -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": {p.name: {**_schema(p), "description": p.description} for p in tool.params},
                "required": [p.name for p in tool.params if p.required],
                "additionalProperties": False,
            },
        }}
        for tool in tools
    ]


def claude_tools(tools: list[Tool]) -> list[dict]:
    def prop(param: Param) -> dict:
        if param.required:
            return {**_schema(param), "description": param.description}
        return {"anyOf": [_schema(param), {"type": "null"}], "description": param.description}

    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": {
                "type": "object",
                "properties": {p.name: prop(p) for p in tool.params},
                "required": [p.name for p in tool.params],
                "additionalProperties": False,
            },
            "strict": True,
        }
        for tool in tools
    ]


def json_menu(tools: list[Tool]) -> str:
    kinds = {"string": "text", "integer": "number", "boolean": "true/false", "array": "list of text"}
    lines = ["ACTIONS:"]
    for tool in tools:
        params = ", ".join(f"{p.name}{'' if p.required else '?'} ({kinds[p.type]})" for p in tool.params)
        lines.append(f"- {tool.name} {{{params}}}: {tool.description}")
    lines.append('Reply with only one JSON object: {"tool": "...", "args": {...}}')
    return "\n".join(lines)


def read_reply(*, tool_name: str | None = None, arguments: str | dict | None = None,
               text: str = "") -> tuple[str, dict]:
    if tool_name:
        return tool_name, _arguments(arguments)
    text = _strip_thinking(text or "")
    found = _first_json_call(text.replace("```json", "").replace("```", ""))
    if found:
        return found
    found = _glm_call(text)
    if found:
        return found
    raise BadReply("no action found in the reply")


def _arguments(arguments: str | dict | None) -> dict:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            raise BadReply("the action's arguments were not valid JSON")
    if not isinstance(arguments, dict):
        raise BadReply("the action's arguments must be a JSON object")
    return arguments


def _strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.split("<think>")[0] if "<think>" in text else text


def _first_json_call(text: str) -> tuple[str, dict] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        call = _call_from_object(value)
        if call:
            return call
    return None


def _call_from_object(value) -> tuple[str, dict] | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("function"), dict):
        value = value["function"]
    name_key = next((k for k in TOOL_KEYS if isinstance(value.get(k), str) and value[k].strip()), None)
    if not name_key:
        return None
    args_key = next((k for k in ARGS_KEYS if k in value), None)
    if args_key:
        try:
            return value[name_key], _arguments(value[args_key])
        except BadReply:
            return None
    return value[name_key], {k: v for k, v in value.items() if k != name_key}


def _glm_call(text: str) -> tuple[str, dict] | None:
    match = re.search(r"<tool_call>\s*([\w.-]+)\s*(.*?)(?:</tool_call>|$)", text, flags=re.DOTALL)
    if not match:
        return None
    args = {}
    pairs = re.findall(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", match.group(2), flags=re.DOTALL)
    for key, raw in pairs:
        try:
            args[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            args[key.strip()] = raw.strip()
    return match.group(1), args


def clean(tool: str, args: dict, tools: list[Tool]) -> tuple[str, dict]:
    offered = {t.name: t for t in tools}
    name = re.sub(r"[\s-]+", "_", str(tool).strip().lower()).split(".")[-1]
    name = ALIASES.get(name, name)
    if name not in offered:
        raise BadReply(f"unknown action '{tool}'; use one of {', '.join(offered)}")
    spec = offered[name]
    if not isinstance(args, dict):
        raise BadReply(f"{name} needs its arguments as a JSON object")
    given = {str(k).strip().lower(): v for k, v in args.items()}
    param_names = {p.name for p in spec.params}
    for alias, real in ARG_ALIASES.items():
        if real in param_names and given.get(real) in (None, "") and alias in given:
            given[real] = given[alias]

    cleaned: dict = {}
    for param in spec.params:
        value = given.get(param.name)
        if value is None or (isinstance(value, str) and not value.strip()) or value == []:
            if param.required and param.name != "why":
                raise BadReply(f"{name} needs {param.name}")
            continue
        cleaned[param.name] = _clean_value(name, param, value)
    if name == "wait":
        cleaned["seconds"] = min(10, max(1, cleaned.get("seconds", 3)))
    if "why" in param_names and "why" not in cleaned:
        cleaned["why"] = _made_up_why(name, cleaned)
    return name, cleaned


def _clean_value(tool: str, param: Param, value):
    if param.type == "integer":
        number = _to_int(value, rounded=param.name == "seconds")
        if number is None:
            raise BadReply(f"{tool} {param.name} must be a whole number like 12, not {json.dumps(value)[:40]}")
        return number
    if param.type == "boolean":
        if isinstance(value, bool):
            return value
        word = str(value).strip().lower()
        if word in ("true", "yes", "1"):
            return True
        if word in ("false", "no", "0"):
            return False
        raise BadReply(f"{tool} {param.name} must be true or false")
    if param.type == "array":
        items = [value] if isinstance(value, (str, int, float)) else value
        if not isinstance(items, list) or any(isinstance(item, (dict, list)) for item in items):
            raise BadReply(f"{tool} {param.name} must be a list of short texts")
        return [str(item).strip() for item in items if str(item).strip()]
    if isinstance(value, (dict, list)):
        raise BadReply(f"{tool} {param.name} must be text")
    text = str(value).strip()
    if param.choices:
        text = DIRECTION_WORDS.get(text.lower(), text.lower())
        if text not in param.choices:
            raise BadReply(f"{tool} {param.name} must be one of {', '.join(param.choices)}")
    return text


def _to_int(value, rounded: bool) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value) if rounded or value.is_integer() else None
    if isinstance(value, str):
        match = re.fullmatch(r"\s*[\[#]?\s*(\d+(?:\.\d+)?)\s*\]?\s*(?:s|sec|seconds)?\s*", value)
        if match:
            return _to_int(float(match.group(1)), rounded)
    return None


def _made_up_why(tool: str, args: dict) -> str:
    label = args.get("target_text") or (f"[{args['target']}]" if "target" in args else "")
    return {
        "tap": f"Tapping '{label}'",
        "confirm_and_tap": f"Asking before tapping '{label}'",
        "type_text": f"Typing '{args.get('text', '')}'",
        "scroll": f"Scrolling {args.get('direction', 'down')}",
        "go_back": "Going back",
        "go_home": "Going to the home screen",
        "open_app": f"Opening {args.get('name', 'the app')}",
        "wait": "Waiting for the screen",
        "look": "Looking at the screen",
    }.get(tool, tool)
