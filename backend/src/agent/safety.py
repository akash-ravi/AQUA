import re
from dataclasses import dataclass

from src.agent import screen as screens

RISKY = re.compile(
    r"\b(place order|order now|pay now|pay|buy now|buy|purchase|send|delete|confirm order|confirm payment|"
    r"confirm purchase|confirm booking|submit order|submit payment|book now|book|reserve|request ride|"
    r"subscribe|donate|transfer|post|publish)\b",
    re.IGNORECASE,
)
NOT_RISKY = re.compile(r"\bbuy \d+\b.*\bget\b|\b(google|apple|samsung) pay\b|\bpay with\b|\bpaypal\b", re.IGNORECASE)
PURCHASE = re.compile(r"\b(place order|order now|pay|buy|purchase|checkout|check out)\b", re.IGNORECASE)
MONEY = re.compile(r"(?:US)?\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
SLIDE = re.compile(r"\b(slide|swipe) to\b", re.IGNORECASE)
SECRET_FIELD = re.compile(r"otp|one.?time|\bcode\b|cvv|cvc|card number|password|passcode|\bpin\b", re.IGNORECASE)
SECRET_QUESTION = re.compile(r"code|otp|password|passcode|\bpin\b", re.IGNORECASE)


@dataclass
class ConfirmPlan:
    text: str
    must_see: list[str]
    label: str
    role: str


def risky_word(view, index: int) -> str | None:
    line = view.by_index.get(index)
    if line is None:
        return None
    text = line.label + " " + screens.subtree_text(view.screen, screens.effective_target(view.screen, index).index)
    if NOT_RISKY.search(text) and not RISKY.search(NOT_RISKY.sub(" ", text)):
        return None
    found = RISKY.search(NOT_RISKY.sub(" ", text))
    return found.group(0) if found else None


def looks_like_search(view, index: int) -> bool:
    node = _node(view, index)
    if node is None:
        return False
    text = " ".join(filter(None, [node.resource_id, node.description, node.class_name, node.text,
                                  view.by_index[index].label if index in view.by_index else ""]))
    return "search" in text.lower()


def is_secret_field(view, index: int) -> bool:
    node = _node(view, index)
    if node is None:
        return False
    text = " ".join(filter(None, [node.resource_id, node.description,
                                  view.by_index[index].label if index in view.by_index else ""]))
    return bool(SECRET_FIELD.search(text))


def check_action(tool: str, args: dict, view) -> str | None:
    """Rules for plain actions. Returns a warning for the AI, or None when it may go ahead."""
    target = args.get("target")
    if tool == "tap" and target is not None:
        word = risky_word(view, target)
        if word:
            return (f'That button can\'t be undone ("{word}"). Use confirm_and_tap with a summary of what will '
                    f"happen, including the total.")
    if tool == "type_text" and target is not None:
        if is_secret_field(view, target):
            return "Don't type secrets. Use ask_user so the user types it on the phone and replies done."
        if args.get("submit") and not looks_like_search(view, target):
            return "Type without submit, then use confirm_and_tap on the Send button."
    if tool == "scroll" and target is not None and target in view.by_index and SLIDE.search(view.by_index[target].label):
        return "Ask the user to do that slide themselves with ask_user."
    return None


def money(text: str) -> list[str]:
    amounts = []
    for whole, cents in MONEY.findall(text or ""):
        value = f"${whole}.{cents or '00'}"
        if value not in amounts:
            amounts.append(value)
    return amounts


def screen_money(view) -> set[str]:
    found = set()
    for line in view.lines:
        found.update(money(line.label))
    for node in view.screen.nodes:
        found.update(money(" ".join(filter(None, [node.text, node.description]))))
    return found


def screen_total(view) -> str | None:
    lines = view.lines
    for position, line in enumerate(lines):
        label = line.label.lower()
        if re.search(r"\btotal\b", label) and "subtotal" not in label:
            amounts = money(line.label)
            if amounts:
                return amounts[-1]
            for later in lines[position + 1:position + 3]:
                amounts = money(later.label)
                if amounts:
                    return amounts[0]
    return None


def plan_confirm(view, args: dict, unsure: list[str]) -> ConfirmPlan | str:
    target = args["target"]
    summary = args["summary"]
    line = view.by_index[target]
    on_screen = screen_money(view)
    amounts = money(summary)
    missing = [amount for amount in amounts if amount not in on_screen]
    if missing:
        return (f"The summary says {missing[0]} but the screen doesn't show it. Scroll until the total and the "
                f"button are both visible.")
    button_text = line.label + " " + screens.subtree_text(view.screen, screens.effective_target(view.screen, target).index)
    total = screen_total(view)
    if PURCHASE.search(button_text):
        if not amounts:
            return "Include the total shown on screen in the summary." if not total else \
                f"Include the total shown on screen ({total}) in the summary."
        if total and total not in amounts:
            return f"Include the total shown on screen ({total}) in the summary."
    text = summary + f"\nButton: “{line.label}”"
    if total:
        text += f"\nTotal on screen: {total}"
    if any(line.label.lower() in note.lower() for note in unsure):
        text += f"\nAn earlier attempt at “{line.label}” may already have gone through. Check before approving."
    return ConfirmPlan(text=text, must_see=amounts, label=line.label, role=line.role)


def recheck(plan: ConfirmPlan, view) -> int | str:
    matches = [line for line in view.lines if line.label == plan.label and line.role == plan.role]
    if len(matches) != 1:
        return "The screen changed after you approved; nothing was tapped. Ask again if it still makes sense."
    on_screen = screen_money(view)
    if any(amount not in on_screen for amount in plan.must_see):
        return "The total changed after you approved; nothing was tapped. Ask again with the new total."
    return matches[0].index


def luhn_ok(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def is_secret(text: str | None, question: str = "") -> bool:
    text = text or ""
    for number in re.findall(r"\d[\d -]{11,22}\d", text):
        digits = re.sub(r"\D", "", number)
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            return True
    if SECRET_QUESTION.search(question or "") and re.fullmatch(r"\s*\d{4,8}\s*", text):
        return True
    return False


def hide_secrets(text: str | None, question: str = "") -> str:
    return "[hidden]" if is_secret(text, question) else (text or "")


def _node(view, index: int):
    line = view.by_index.get(index)
    return line.node if line is not None else None
