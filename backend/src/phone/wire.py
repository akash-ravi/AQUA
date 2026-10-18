from pydantic import ValidationError

from src.phone.messages import App, Ask, Command, Done, ErrorInfo, ErrorMessage, Image, Node, Progress, Result, Screen

COMMAND_NAMES = {
    "read_screen": "readScreen", "tap": "click", "type_text": "setText", "scroll": "scroll", "go_back": "back",
    "go_home": "home", "open_app": "openApp", "screenshot": "screenshot", "list_apps": "listApps", "tap_at": "tap",
}
ERROR_CODES = {
    "staleScreen": "stale_screen", "nodeNotFound": "stale_target", "actionFailed": "action_failed",
    "appNotFound": "app_not_found", "needsConfirmation": "needs_confirmation", "secureWindow": "secure_window",
    "badCommand": "bad_command", "cantSubmit": "cant_submit",
}
NO_EFFECT_ERRORS = {"stale_screen", "stale_target", "app_not_found", "needs_confirmation", "bad_command"}


def message_to_wire(message) -> dict:
    if isinstance(message, Progress):
        return {"type": "progress", **message.model_dump()}
    if isinstance(message, Command):
        return {"type": "command", "id": message.command_id, "name": COMMAND_NAMES[message.action],
                "args": command_args(message)}
    if isinstance(message, Ask):
        return {"type": "ask", "id": message.ask_id, "question": message.text}
    if isinstance(message, Done):
        return {"type": "done", "summary": message.summary}
    if isinstance(message, ErrorMessage):
        return {"type": "error", "message": message.message}
    raise TypeError(f"{type(message).__name__} is not sent to the phone")


def command_args(command: Command) -> dict:
    args = {
        "tap": {"screenId": command.screen_id, "index": command.target, "confirmed": command.confirmed or None},
        "tap_at": {"x": command.x, "y": command.y},
        "type_text": {"screenId": command.screen_id, "index": command.target, "text": command.text,
                      "submit": command.submit or None},
        "scroll": {"screenId": command.screen_id, "index": command.target, "direction": command.direction},
        "open_app": {"name": command.package_name},
        "screenshot": {"maxWidth": command.max_side},
    }.get(command.action, {})
    return {key: value for key, value in args.items() if value is not None}


def result_from_wire(data, command: Command, width: int, height: int) -> Result:
    if not data.ok:
        return failed_result(command, ERROR_CODES.get(data.error or "", data.error or "action_failed"), data.message or "")
    payload = data.data or {}
    try:
        if command.action == "read_screen":
            return Result(command_id=data.id, status="ok", screen=screen_from_wire(payload, width, height))
        if command.action == "screenshot":
            return Result(command_id=data.id, status="ok", image=Image(data=payload["png"]))
        if command.action == "list_apps":
            apps = [App(label=app["label"], package_name=app["package"]) for app in payload["apps"]]
            return Result(command_id=data.id, status="ok", apps=apps)
    except (KeyError, TypeError, ValidationError):
        return failed_result(command, "bad_result", "the phone sent a result in the wrong shape")
    return Result(command_id=data.id, status="ok", screen=_attached_screen(payload, width, height))


def _attached_screen(payload: dict, width: int, height: int) -> Screen | None:
    if "screen" not in payload:
        return None
    try:
        return screen_from_wire(payload["screen"], width, height)
    except (KeyError, TypeError, ValidationError):
        return None  # the backend falls back to reading the screen itself


def failed_result(command: Command, code: str, message: str) -> Result:
    if command.action == "screenshot" and code == "secure_window":
        code = "screenshot_blocked"
    status = "rejected" if code in NO_EFFECT_ERRORS else "failed"
    return Result(command_id=command.command_id, status=status,
                  error=ErrorInfo(code=code, message=message))


def screen_from_wire(payload: dict, width: int, height: int) -> Screen:
    nodes = assign_parents([node_from_wire(node) for node in payload["nodes"]])
    return Screen(screen_id=payload["screenId"], package_name=payload.get("package") or "", width=width,
                  height=height, truncated=bool(payload.get("truncated")), nodes=nodes)


def assign_parents(nodes: list[Node]) -> list[Node]:
    # shortcut: the wire has no parent field; assumes nodes arrive in tree order, so the nearest earlier node that contains a node is its parent
    open_ancestors: list[Node] = []
    for node in nodes:
        while open_ancestors and not bounds_contain(open_ancestors[-1].bounds, node.bounds):
            open_ancestors.pop()
        node.parent = open_ancestors[-1].index if open_ancestors else None
        open_ancestors.append(node)
    return nodes


def bounds_contain(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def node_from_wire(node: dict) -> Node:
    return Node(
        index=node["index"], text=node.get("text"), description=node.get("desc"), resource_id=node.get("id"),
        class_name=node.get("class"), bounds=node["bounds"], clickable=bool(node.get("clickable")),
        editable=bool(node.get("editable")), scrollable=bool(node.get("scrollable")),
        checkable=node.get("checked") is not None, checked=bool(node.get("checked")),
    )
