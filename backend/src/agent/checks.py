import difflib
import re

from src.agent import screen as screens

TARGETED = {"tap", "type_text", "confirm_and_tap"}


def check_decision(tool: str, args: dict, view, apps: dict[str, str], no_effect_actions: set[tuple]) -> tuple[dict, str] | str:
    """Returns (possibly corrected args, a note for the log), or a plain warning for the AI."""
    args = dict(args)
    note = ""

    if tool in TARGETED or (tool == "scroll" and args.get("target") is not None):
        target = args.get("target")
        if target not in view.by_index:
            numbers = sorted(view.by_index)
            span = f"numbers go {numbers[0]} to {numbers[-1]}" if numbers else "the screen is empty"
            return f"there is no [{target}] on this screen; {span}"

    if tool in TARGETED and args.get("target_text"):
        wanted = screens.normalize(args["target_text"])
        current = screens.normalize(view.by_index[args["target"]].label)
        if wanted and wanted not in current and current not in wanted:
            others = [line for line in screens.find_lines(view, args["target_text"]) if line.index != args["target"]]
            if len(others) == 1:
                if tool == "confirm_and_tap":
                    return f"target_text doesn't match [{args['target']}]; check the number"
                note = f"used [{others[0].index}] because it matches \"{args['target_text']}\""
                args["target"] = others[0].index

    if tool == "type_text":
        node = view.by_index[args["target"]].node
        if not node.editable:
            if node.clickable or screens.effective_target(view.screen, node.index).clickable:
                args["tap_first"] = True
            else:
                return f"[{node.index}] is not a text box"

    if tool == "scroll":
        target = args.get("target")
        if target is None:
            args["target"] = _main_list(view)
            if args["target"] is None:
                return "nothing on this screen scrolls"
        else:
            ancestor = _scrollable_ancestor(view, target)
            args["target"] = target if ancestor is None else ancestor  # the phone finds the list above the item itself

    if tool == "open_app":
        found = find_app(args.get("name", ""), apps)
        if isinstance(found, str) and found not in apps:
            return found
        if found == view.screen.package_name:
            return "that app is already open"
        args["package_name"] = found

    if tool == "ask_user" and args.get("choices"):
        args["choices"] = [choice[:60] for choice in args["choices"]][:6]

    if tool in TARGETED | {"scroll"} and args.get("target") is not None:
        label = view.by_index[args["target"]].label if args["target"] in view.by_index else ""
        if (tool, label, view.signature) in no_effect_actions:
            return "already tried; nothing happened. Try something different"
    elif (tool, "", view.signature) in no_effect_actions and tool in {"go_back", "scroll", "look"}:
        return "already tried; nothing happened. Try something different"
    return args, note


def find_app(name: str, apps: dict[str, str]) -> str:
    """Returns a package name, or a plain warning when no single app matches."""
    def normalize_name(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    wanted = normalize_name(name)
    if not wanted:
        return "open_app needs the app's name"
    by_label = {normalize_name(label): package for package, label in apps.items()}
    if wanted in by_label:
        return by_label[wanted]
    if wanted in {normalize_name(package) for package in apps}:
        return next(package for package in apps if normalize_name(package) == wanted)
    partial = [package for key, package in by_label.items() if key.startswith(wanted) or wanted in key]
    if len(partial) == 1:
        return partial[0]
    closest = difflib.get_close_matches(name, list(apps.values()), n=3, cutoff=0.3)
    hint = f"; closest: {', '.join(closest)}" if closest else ""
    return f"no app named \"{name}\"{hint}"


def check_finish(args: dict, view, *, goal: str, corrections: list[str], typed: list[str],
                 app_label: str, committed_steps: list) -> str | None:
    proof = [str(phrase) for phrase in (args.get("proof") or []) if str(phrase).strip()]
    if not 1 <= len(proof) <= 3:
        return "finish needs 1 to 3 short phrases copied exactly from the current screen"
    shown = [screens.normalize(text) for text in screens.proof_texts(view)]
    own_words = [screens.normalize(text) for text in [goal, *corrections, *typed] if text]
    new_info = False
    for phrase in proof:
        wanted = screens.normalize(phrase)
        if not 4 <= len(wanted) <= 120:
            return f'"{phrase}" is too short or too long to count as proof'
        if not any(wanted in text or _word_overlap(wanted, text) >= 0.85 for text in shown):
            return _proof_not_found(phrase, view)
        if wanted != screens.normalize(app_label) and not any(wanted in words for words in own_words):
            new_info = True
    if not new_info:
        return "that proof only repeats the request or what you typed; quote something the app shows as a result"
    if committed_steps:
        done = [step for step in committed_steps if step.status in ("ok", "unknown")]
        if not done:
            return "the order hasn't been placed yet; finish only after the confirmation screen"
        if view.signature == done[-1].signature:
            return "this is still the screen where you tapped; wait for the confirmation screen"
    return None


def _proof_not_found(phrase: str, view) -> str:
    texts = screens.proof_texts(view)
    close = difflib.get_close_matches(phrase, texts, n=5, cutoff=0.2) or texts[:5]
    options = "; ".join(f'"{text}"' for text in close)
    return f'"{phrase}" is not on the current screen. Copy proof exactly, for example: {options}'


def _word_overlap(a: str, b: str) -> float:
    words = a.split()
    if not words:
        return 0.0
    other = set(b.split())
    return sum(word in other for word in words) / len(words)


def _main_list(view) -> int | None:
    def area(node) -> int:
        left, top, right, bottom = node.bounds
        return (right - left) * (bottom - top)

    lists = [node for node in view.screen.nodes if node.scrollable]
    return max(lists, key=area).index if lists else None


def _scrollable_ancestor(view, index: int) -> int | None:
    nodes = {node.index: node for node in view.screen.nodes}
    seen = set()
    current = nodes.get(index)
    while current is not None and current.index not in seen:
        if current.scrollable and current.index in view.by_index:
            return current.index
        seen.add(current.index)
        current = nodes.get(current.parent)
    return None
