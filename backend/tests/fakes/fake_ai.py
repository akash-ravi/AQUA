import asyncio
import inspect
import re
from dataclasses import dataclass, field
from typing import Callable

from jarvis.llm.common import BadReply, Decision
from jarvis.llm.tools import Tool, clean

TARGET_TOOLS = {"tap", "type_text", "confirm_and_tap", "scroll"}
ACTIONABLE_ROLES = {"button", "item", "field", "radio", "check", "switch", "tab", "list"}


@dataclass
class FakeStep:
    tool: str
    args: dict = field(default_factory=dict)
    find: str | None = None
    optional: bool = False
    delay: float = 0.0


@dataclass
class ScreenLine:
    index: int
    role: str
    label: str
    rest: str


FAKEDASH_SCRIPT: list[FakeStep] = [
    FakeStep("open_app", {"why": "Open DoorDash", "name": "DoorDash",
                          "remember": "Plan: search pizza, pick Tony's, large pepperoni, check out"}),
    FakeStep("tap", {"why": "Close the DashPass offer"}, find="No thanks", optional=True),
    FakeStep("tap", {"why": "Open search"}, find="Search DoorDash"),
    FakeStep("type_text", {"why": "Search for pizza", "text": "pizza", "submit": True}, find="Search stores"),
    FakeStep("tap", {"why": "Open Tony's Pizzeria"}, find="Tony's Pizzeria"),
    FakeStep("tap", {"why": "Add the pepperoni pizza"}, find="Add (for Pepperoni Pizza)"),
    FakeStep("tap", {"why": "Pick the large size"}, find="Large"),
    FakeStep("tap", {"why": "Add it to the cart"}, find="Add to cart"),
    FakeStep("tap", {"why": "Skip the drink offer"}, find="No thanks", optional=True),
    FakeStep("tap", {"why": "Open the cart"}, find="View cart"),
    FakeStep("tap", {"why": "Go to checkout"}, find="Checkout"),
    FakeStep("scroll", {"why": "See the total and Place order", "direction": "down"}),
    FakeStep("confirm_and_tap", {"why": "Place the order after you approve",
                                 "summary": "Place order at Tony's Pizzeria: 1 Large Pepperoni Pizza, total $31.20"},
             find="Place order"),
    FakeStep("wait", {"why": "Wait for the order to go through", "seconds": 2}, find="Placing", optional=True),
    FakeStep("finish", {"summary": "Ordered 1 Large Pepperoni Pizza from Tony's Pizzeria. Total $31.20.",
                        "proof": ["Order placed!", "Order #4512"]}),
]

Script = list[FakeStep] | Callable[[str], tuple[str, dict]]


class FakeAI:
    def __init__(self, script: Script, sees_images: bool = False):
        self.name = "fake"
        self.script = script
        self.sees_images = sees_images
        self.position = 0
        self.messages: list[str] = []
        self.retry_notes: list[str] = []

    async def decide(self, system: str, message: str, tools: list[Tool], *, image: bytes | None = None,
                     retry_note: str | None = None) -> Decision:
        self.messages.append(message)
        if retry_note:
            self.retry_notes.append(retry_note)
        if callable(self.script):
            reply = self.script(message)
            if inspect.isawaitable(reply):
                reply = await reply
            return self._decision(*reply, tools)

        lines = screen_lines(message)
        while self.position < len(self.script):
            step = self.script[self.position]
            args = dict(step.args)
            if step.find:
                line = find_line(lines, step.find)
                if line is None and step.optional:
                    self.position += 1
                    continue
                if line is None:
                    raise BadReply(f"'{step.find}' is not on the screen")
                if step.tool in TARGET_TOOLS:
                    args["target"] = line.index
                    args.setdefault("target_text", line.label or step.find)
            if step.delay:
                await asyncio.sleep(step.delay)
            self.position += 1
            return self._decision(step.tool, args, tools)
        return Decision(tool="give_up", args={"reason": "script finished"}, model="fake")

    def _decision(self, tool: str, args: dict, tools: list[Tool]) -> Decision:
        tool, args = clean(tool, args, tools)
        return Decision(tool=tool, args=args, model="fake")


def screen_lines(message: str) -> list[ScreenLine]:
    _, found, screen = message.partition("SCREEN NOW:")
    lines = []
    for raw in (screen if found else message).splitlines():
        match = re.match(r"\s*\[(\d+)\]\s+(\w+)\s*(.*)$", raw)
        if match:
            rest = match.group(3)
            lines.append(ScreenLine(int(match.group(1)), match.group(2), _label(rest), rest))
    return lines


def find_line(lines: list[ScreenLine], find: str) -> ScreenLine | None:
    wanted = find.casefold()
    label_part, for_part = wanted, None
    match = re.fullmatch(r"(.*?)\s*\(for (.*)\)", wanted)
    if match:
        label_part, for_part = match.group(1), match.group(2)

    def fits(line: ScreenLine) -> bool:
        rest = line.rest.casefold()
        if for_part is not None:
            return label_part in line.label.casefold() and f"(for {for_part}" in rest
        return wanted in rest

    matches = [line for line in lines if fits(line)]
    actionable = [line for line in matches if line.role in ACTIONABLE_ROLES]
    return (actionable or matches or [None])[0]


def _label(rest: str) -> str:
    # labels can hold quotes (Large 16" $24.99), so the closing quote is the one followed by " (" or the end
    if not rest.startswith('"'):
        return ""
    for position in range(1, len(rest)):
        if rest[position] == '"' and (position == len(rest) - 1 or rest[position + 1:].startswith(" (")):
            return rest[1:position]
    return rest[1:]
