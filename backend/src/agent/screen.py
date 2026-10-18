import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from src.phone.messages import Node, Screen

MAX_LABEL = 80
MAX_LINE = 160
MAX_DEPTH = 6
KEEP_LAST_LINES = 8
SHARED_BUTTON_LABELS = {"add", "+", "-", "edit", "remove", "select", "more"}
SAME_SCREEN_SHARE = 0.6

QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"'})
DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−"})
TRAILING_PUNCTUATION = ".,;:!?…"


@dataclass
class Line:
    index: int
    role: str
    label: str
    states: list[str]
    depth: int
    for_item: str | None
    node: Node


@dataclass
class View:
    screen: Screen
    text: str
    lines: list[Line]
    by_index: dict[int, Line]
    app: str
    signature: str


class Tree:
    def __init__(self, screen: Screen):
        self.nodes: dict[int, Node] = {}
        self.children: dict[int | None, list[Node]] = {}
        for node in screen.nodes:
            if node.index in self.nodes:
                continue
            self.nodes[node.index] = node
        for node in self.nodes.values():
            self.children.setdefault(self.parent_index(node), []).append(node)

    def parent_index(self, node: Node) -> int | None:
        if node.parent in self.nodes and node.parent != node.index:
            return node.parent
        return None

    def ancestors(self, node: Node) -> list[Node]:
        found = []
        seen = {node.index}
        current = self.parent_index(node)
        while current is not None and current not in seen:
            seen.add(current)
            found.append(self.nodes[current])
            current = self.parent_index(self.nodes[current])
        return found

    def descendants(self, node: Node) -> list[Node]:
        found = []
        seen = {node.index}
        stack = list(reversed(self.children.get(node.index, [])))
        while stack:
            child = stack.pop()
            if child.index in seen:
                continue
            seen.add(child.index)
            found.append(child)
            stack.extend(reversed(self.children.get(child.index, [])))
        return found


def render(screen: Screen, apps: dict[str, str] | None = None, max_lines: int = 150) -> View:
    tree = Tree(screen)
    folded: set[int] = set()
    labels: dict[int, str] = {}
    first_texts: dict[int, str] = {}
    shown: list[Node] = []
    for node in tree.nodes.values():
        if node.index in folded:
            continue
        label = node_text(node)
        if not label and merges_child_text(node):
            parts = text_parts(tree, node)
            folded.update(part.index for part, _ in parts)
            label = " · ".join(text for _, text in parts)
            if parts:
                first_texts[node.index] = parts[0][1]
        if not is_interactive(node) and not label:
            continue
        if not label and node.resource_id and not node.editable and not node.scrollable:
            label = "id: " + node.resource_id.split("/")[-1]
        labels[node.index] = label
        shown.append(node)

    shown_ids = {node.index for node in shown}
    shown_parent = {node.index: next((a.index for a in tree.ancestors(node) if a.index in shown_ids), None) for node in shown}
    has_shown_children = {parent for parent in shown_parent.values() if parent is not None}

    lines = []
    for node in shown:
        role = role_of(node, node.index in has_shown_children)
        label = labels[node.index]
        depth = min(MAX_DEPTH, sum(1 for a in tree.ancestors(node) if a.index in shown_ids))
        lines.append(Line(node.index, role, truncate(label, MAX_LABEL), states_of(node, role, label, screen), depth, None, node))

    button_counts = Counter(normalize(line.label) for line in lines if line.role == "button" and line.label)
    for line in lines:
        key = normalize(line.label)
        if line.role == "button" and line.label and (button_counts[key] >= 2 or key in SHARED_BUTTON_LABELS):
            name = item_name(tree, line.node, first_texts)
            if name and normalize(name) != key:
                line.for_item = truncate(name, MAX_LABEL)

    lines, hidden_count = limit_lines(tree, lines, max_lines)
    by_index = {line.index: line for line in lines}
    app = (apps or {}).get(screen.package_name) or screen.package_name

    text_lines = [f"App: {app}"]
    if not lines:
        text_lines.append("(nothing readable on this screen)")
    text_lines += [line_text(line) for line in lines]
    if hidden_count:
        text_lines.append(f"(… {hidden_count} more lines not shown)")
    if screen.truncated:
        text_lines.append("(the phone sent only part of this screen)")

    view = View(screen, "\n".join(text_lines), lines, by_index, app, "")
    view.signature = signature(view)
    return view


def line_text(line: Line) -> str:
    text = "  " * line.depth + f"[{line.index}] {line.role}"
    if line.label:
        text += f' "{line.label}"'
    for state in line.states:
        text += f" ({state})"
    if line.for_item:
        text += f" (for {line.for_item})"
    return truncate(text, MAX_LINE)


def role_of(node: Node, has_shown_children: bool) -> str:
    kind = node.class_name or ""
    if node.editable:
        return "field"
    if node.checkable:
        if "Radio" in kind:
            return "radio"
        if "Switch" in kind or "Toggle" in kind:
            return "switch"
        return "check"
    if "Tab" in kind and "Table" not in kind and not node.scrollable:
        return "tab"
    if node.scrollable:
        return "list"
    if node.clickable:
        return "item" if has_shown_children else "button"
    if "Image" in kind:
        return "image"
    return "text"


def states_of(node: Node, role: str, label: str, screen: Screen) -> list[str]:
    states = []
    if role == "field":
        if not label:
            states.append("empty")
    elif not label and role != "list":
        states.append(f"no label, {screen_region(node, screen)}" if node.clickable else "no label")
    if role == "switch":
        states.append("on" if node.checked else "off")
    elif node.checkable:
        states.append("checked" if node.checked else "not checked")
    return states


def screen_region(node: Node, screen: Screen) -> str:
    left, top, right, bottom = node.bounds
    x = (left + right) / 2 / screen.width
    y = (top + bottom) / 2 / screen.height
    row = "top" if y < 1 / 3 else "middle" if y < 2 / 3 else "bottom"
    column = "left" if x < 1 / 3 else "center" if x < 2 / 3 else "right"
    return "center" if (row, column) == ("middle", "center") else f"{row}-{column}"


def item_name(tree: Tree, button: Node, first_texts: dict[int, str]) -> str | None:
    for ancestor in tree.ancestors(button):
        if ancestor.scrollable:
            break
        name = node_text(ancestor) or first_texts.get(ancestor.index)
        if not name:
            parts = text_parts(tree, ancestor)
            name = parts[0][1] if parts else None
        if name:
            return name
    siblings = tree.children.get(tree.parent_index(button), [])
    earlier = siblings[: next(i for i, s in enumerate(siblings) if s.index == button.index)]
    for sibling in reversed(earlier):
        parts = text_parts(tree, sibling)
        name = node_text(sibling) or (parts[0][1] if parts else None)
        if name:
            return name
    return None


def limit_lines(tree: Tree, lines: list[Line], max_lines: int) -> tuple[list[Line], int]:
    extra = len(lines) - max_lines
    if extra <= 0:
        return lines, 0
    droppable = range(len(lines) - KEEP_LAST_LINES - 1, -1, -1)

    def in_list(line: Line) -> bool:
        return any(a.scrollable for a in tree.ancestors(line.node))

    list_texts = [i for i in droppable if lines[i].role == "text" and in_list(lines[i])]
    others = [i for i in droppable if i not in list_texts]
    dropped = set((list_texts + others)[:extra])
    return [line for i, line in enumerate(lines) if i not in dropped], len(dropped)


def is_interactive(node: Node) -> bool:
    return node.clickable or node.editable or node.checkable or node.scrollable


def merges_child_text(node: Node) -> bool:
    return (node.clickable or node.checkable) and not node.editable and not node.scrollable


def node_text(node: Node) -> str:
    return clean_text(node.text) or clean_text(node.description)


def clean_text(text: str | None) -> str:
    return " ".join(text.split()) if text else ""


def text_parts(tree: Tree, node: Node) -> list[tuple[Node, str]]:
    parts = []
    seen = {node.index}
    stack = list(reversed(tree.children.get(node.index, [])))
    while stack:
        child = stack.pop()
        if child.index in seen:
            continue
        seen.add(child.index)
        if is_interactive(child):
            continue
        text = node_text(child)
        if text:
            parts.append((child, text))
        stack.extend(reversed(tree.children.get(child.index, [])))
    return parts


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def effective_target(screen: Screen, index: int) -> Node:
    tree = Tree(screen)
    node = tree.nodes[index]
    if node.clickable:
        return node
    return next((a for a in tree.ancestors(node) if a.clickable), node)


def subtree_text(screen: Screen, index: int) -> str:
    tree = Tree(screen)
    node = tree.nodes[index]
    parts = []
    for each in [node, *tree.descendants(node)]:
        text = clean_text(each.text)
        description = clean_text(each.description)
        for part in (text, description if description != text else "", each.resource_id or ""):
            if part:
                parts.append(part)
    return " ".join(parts)


def signature(view: View) -> str:
    items = set()
    for line in view.lines:
        label, states = line.label, line.states
        if line.role == "text":
            label = re.sub(r"\d", "#", label)
            states = [re.sub(r"\d", "#", s) for s in states]
        items.add(json.dumps([line.role, label, states], ensure_ascii=False))
    data = json.dumps([view.screen.package_name, sorted(items)], ensure_ascii=False)
    return hashlib.sha1(data.encode()).hexdigest()[:16]


def describe_change(before: View, after: View) -> str:
    if before.signature == after.signature:
        return "no change"
    if before.screen.package_name != after.screen.package_name or overlap_ratio(before, after) < SAME_SCREEN_SHARE:
        return f"new screen: {after.app}"

    changes = []
    pairs, gone, new = pair_lines(before.lines, after.lines)
    edits = []
    for old, line in pairs:
        if old.label != line.label:
            edits.append(f'{line.role} now "{line.label}"' if line.label else f"{line.role} [{line.index}] now empty")
        elif old.states != line.states:
            edits.append(state_change(old, line))
    changes += edits[:3]
    changes += ([f"new {line.role} {display_name(line)}" for line in new] + [f"{display_name(line)} gone" for line in gone])[:2]
    if not changes:
        return "same screen; small changes"
    return "same screen; changed: " + "; ".join(changes)


def overlap_ratio(before: View, after: View) -> float:
    size = max(len(before.lines), len(after.lines))
    if size == 0:
        return 1.0
    old = Counter((line.role, normalize(line.label)) for line in before.lines)
    now = Counter((line.role, normalize(line.label)) for line in after.lines)
    return sum((old & now).values()) / size


def pair_lines(before: list[Line], after: list[Line]) -> tuple[list[tuple[Line, Line]], list[Line], list[Line]]:
    def key(line: Line):
        return line.role, line.label, line.for_item

    waiting: dict[tuple, list[Line]] = {}
    for line in before:
        waiting.setdefault(key(line), []).append(line)
    pairs, new = [], []
    for line in after:
        same = waiting.get(key(line))
        if same:
            pairs.append((same.pop(0), line))
        else:
            new.append(line)
    gone = [line for lines in waiting.values() for line in lines]

    gone_by_index = {line.index: line for line in gone}
    still_new = []
    for line in new:
        old = gone_by_index.get(line.index)
        if old is not None and old.role == line.role:
            pairs.append((old, line))
            del gone_by_index[line.index]
        else:
            still_new.append(line)
    gone = list(gone_by_index.values())
    for role in {line.role for line in still_new}:
        olds = [line for line in gone if line.role == role]
        nows = [line for line in still_new if line.role == role]
        if len(olds) == 1 and len(nows) == 1:
            pairs.append((olds[0], nows[0]))
            gone.remove(olds[0])
            still_new.remove(nows[0])
    order = {line.index: i for i, line in enumerate(after)}
    pairs.sort(key=lambda pair: order.get(pair[1].index, 0))
    return pairs, gone, still_new


def state_change(old: Line, line: Line) -> str:
    gained = [s for s in line.states if s not in old.states]
    lost = [s for s in old.states if s not in line.states]
    if gained:
        return f"{display_name(line)} now {', '.join(gained)}"
    return f"{display_name(line)} no longer {', '.join(lost)}"


def display_name(line: Line) -> str:
    return f'"{line.label}"' if line.label else f"{line.role} [{line.index}]"


def proof_texts(view: View) -> list[str]:
    tree = Tree(view.screen)
    texts: list[str] = []
    for line in view.lines:
        node = line.node
        if node.editable:
            continue
        candidates = [line.label, clean_text(node.text), clean_text(node.description)]
        if merges_child_text(node) and not node_text(node):
            candidates += [text for _, text in text_parts(tree, node)]
        for text in candidates:
            if text and text not in texts:
                texts.append(text)
    return texts


def field_texts(view: View) -> list[str]:
    return [text for line in view.lines if line.node.editable and (text := clean_text(line.node.text))]


def find_lines(view: View, label: str) -> list[Line]:
    wanted = normalize(label)
    return [line for line in view.lines if normalize(line.label) == wanted]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(QUOTES).translate(DASHES).casefold()
    return " ".join(text.split()).rstrip(TRAILING_PUNCTUATION).strip()
