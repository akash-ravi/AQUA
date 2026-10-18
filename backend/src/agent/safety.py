import re
from dataclasses import dataclass

from src.agent import screen as screens

PAYMENT_ACTION = re.compile(
    r"\b(place order|order now|pay now|pay|buy now|buy|purchase|confirm order|confirm payment|"
    r"confirm purchase|submit order|submit payment|donate|send money|transfer money)\b", re.IGNORECASE)
POSSIBLY_PAID = re.compile(r"\b(subscribe|start trial|start free trial|book|reserve|confirm booking|request ride|transfer|send|call|dial|post|publish)\b", re.IGNORECASE)
COMMERCE = re.compile(r"\b(payment|checkout|check out|beneficiary|bank transfer|subscription|billing|fare|charge|fee|per minute|premium sms|paid)\b", re.IGNORECASE)
FREE = re.compile(r"\b(free|no charge|no cost|zero cost)\b", re.IGNORECASE)
NOT_PAYMENT = re.compile(r"\bbuy \d+\b.*\bget\b|\b(google|apple|samsung) pay\b|\bpay with\b|\bpaypal\b", re.IGNORECASE)
DUPLICATE_EFFECT = re.compile(r"\b(send|delete|call|dial|post|publish|submit|book|reserve|subscribe|transfer|donate|place order|pay|buy|purchase|request ride)\b", re.IGNORECASE)
MONEY = re.compile(r"((?:US)?\$|€|£|₹|\b(?:USD|EUR|GBP|INR|CAD|AUD)\s*)\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?", re.IGNORECASE)
# Picking options (color, size, "Select", cookie "Accept") stays allowed; PAYMENT_ACTION still blocks the final pay/order tap.
BLIND_EFFECT = re.compile(r"\b(request|confirm|book|reserve|send|submit|subscribe|donate|"
                          r"transfer|call|dial|post|publish|delete|remove|cancel)\b", re.IGNORECASE)
SLIDE_GESTURE = re.compile(r"\b(slide|swipe) to\b", re.IGNORECASE)
SECRET_FIELD = re.compile(r"otp|one.?time|\bcode\b|cvv|cvc|card number|password|passcode|\bpin\b", re.IGNORECASE)
SECRET_QUESTION = re.compile(r"code|otp|password|passcode|\bpin\b", re.IGNORECASE)


@dataclass
class ConfirmPlan:
    text: str
    must_see: list[str]
    label: str
    role: str
    package: str
    details: tuple[str, ...]
    total: str | None


def target_text(view, index: int) -> str:
    line = view.by_index.get(index)
    if line is None:
        return ""
    return line.label + " " + screens.subtree_text(view.screen, screens.effective_target(view.screen, index).index)


def payment_requirement(view, index: int) -> str | None:
    """payment, unknown cost, or no payment approval. Never equate Send with spending."""
    text = NOT_PAYMENT.sub(" ", target_text(view, index))
    context = " ".join(line.label for line in view.lines)
    charges = MONEY.findall(context)
    if (charges and all(int(whole.replace(',', '')) == 0 and int(cents or '0') == 0 for _, whole, cents in charges)
            and screen_total(view) is not None and not re.search(r"renew|recurring|trial|per month|per year", context, re.I)):
        return None
    if PAYMENT_ACTION.search(text):
        return "payment"
    if re.search(r"\b(confirm|submit|continue|complete|finish)\b", text, re.I) and COMMERCE.search(context) and find_amounts(context):
        return "unknown"
    if not POSSIBLY_PAID.search(text):
        return None
    if find_amounts(text):
        return "payment"
    if COMMERCE.search(context) and (find_amounts(context) or re.search(r"\b(paid|fee|charge|per minute|premium sms)\b", context, re.I)):
        return "payment"
    if re.search(r"\btrial\b", context, re.I) and re.search(r"\b(subscribe|trial)\b", text, re.I):
        return "unknown"
    if FREE.search(text) or (FREE.search(context) and not find_amounts(context)):
        return None
    if re.search(r"\b(subscribe|trial|book|reserve|confirm booking|request ride|transfer)\b", text, re.I):
        return "unknown"
    return None


def duplicate_sensitive(tool: str, args: dict, view) -> bool:
    return tool in {"tap", "confirm_and_tap"} and bool(DUPLICATE_EFFECT.search(target_text(view, args["target"])))


def transaction_details(view) -> tuple[str, ...]:
    # Ignore node IDs/order/coordinates, but bind approval to every visible semantic detail.
    return tuple(sorted("|".join([node.class_name or "", node.resource_id or "",
                                 screens.normalize(node.text or ""), screens.normalize(node.description or ""), str(node.checked)])
                        for node in view.screen.nodes))


def redact_screen(screen):
    nodes = []
    for node in screen.nodes:
        secret = node.sensitive or (node.editable and SECRET_FIELD.search(" ".join(filter(None, [node.resource_id, node.description]))))
        nodes.append(node.model_copy(update={"text": "[hidden]", "description": "Secret field", "sensitive": True}) if secret else node)
    return screen.model_copy(update={"nodes": nodes})


def action_key(tool: str, args: dict, view) -> str:
    import hashlib
    import json
    line = view.by_index.get(args.get("target"))
    identity = ["tap" if tool == "confirm_and_tap" else tool, view.screen.package_name, line.node.resource_id if line else None,
                screens.normalize(line.label) if line else "", line.for_item if line else None,
                args.get("direction"), args.get("text")]
    if line and duplicate_sensitive(tool, args, view):
        # Distinguish messages to different recipients/with different contents, without storing contents.
        identity.append([(n.resource_id, n.text) for n in view.screen.nodes if n.editable])
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def looks_like_search(view, index: int) -> bool:
    node = _node_at(view, index)
    if node is None:
        return False
    text = " ".join(filter(None, [node.resource_id, node.description, node.class_name, node.text,
                                  view.by_index[index].label if index in view.by_index else ""]))
    return "search" in text.lower()


def is_secret_field(view, index: int) -> bool:
    node = _node_at(view, index)
    if node is None:
        return False
    if node.sensitive:
        return True
    text = " ".join(filter(None, [node.resource_id, node.description,
                                  view.by_index[index].label if index in view.by_index else ""]))
    return bool(SECRET_FIELD.search(text))


def asks_for_secret(question: str) -> bool:
    return bool(re.search(r"password|passcode|\botp\b|one.?time|verification code|security code|\bpin\b|\bcvv\b|\bcvc\b|card number", question, re.I))


def check_action(tool: str, args: dict, view) -> str | None:
    """Rules for plain actions. Returns a warning for the AI, or None when it may go ahead."""
    target = args.get("target")
    if tool == "tap" and target is not None:
        if payment_requirement(view, target) == "payment":
            return "This action costs money. Use confirm_and_tap with the transaction and current charge."
    if tool == "type_text" and target is not None:
        if is_secret_field(view, target):
            return "Don't generate or type a secret. Use request_secret for this field; the user can speak it or enter it manually."
        if args.get("submit") and not looks_like_search(view, target):
            return "Type without submit, then tap the visible Send button. Use confirm_and_tap only if sending costs money."
    # Picture taps skip the screen-text money checks, so anything with an effect is left to the user.
    if tool == "tap_point" and (BLIND_EFFECT.search(args["what"]) or PAYMENT_ACTION.search(args["what"])
                                or SLIDE_GESTURE.search(args["what"]) or find_amounts(args["what"])):
        return "That may cost money or can't be undone, and it has no number. Ask the user to tap it themselves with ask_user."
    if tool == "type_focused" and is_secret(args["text"]):
        return "Don't type a secret. Ask the user to enter it on the phone."
    if tool == "scroll" and target is not None and target in view.by_index and SLIDE_GESTURE.search(view.by_index[target].label):
        return "Ask the user to do that slide themselves with ask_user."
    return None


def find_amounts(text: str) -> list[str]:
    amounts = []
    for currency, whole, cents in MONEY.findall(text or ""):
        currency = currency.strip().upper().replace("US$", "$")
        value = f"{currency}{whole.replace(',', '')}.{cents or '00'}"
        if value not in amounts:
            amounts.append(value)
    return amounts


def screen_amounts(view) -> set[str]:
    found = set()
    for line in view.lines:
        found.update(find_amounts(line.label))
    for node in view.screen.nodes:
        found.update(find_amounts(" ".join(filter(None, [node.text, node.description]))))
    return found


def screen_total(view) -> str | None:
    lines = view.lines
    for position, line in enumerate(lines):
        label = line.label.lower()
        if re.search(r"\b(total|amount due|amount to send|you pay)\b", label) and "subtotal" not in label:
            amounts = find_amounts(line.label)
            if amounts:
                return amounts[-1]
            for later in lines[position + 1:position + 3]:
                amounts = find_amounts(later.label)
                if amounts:
                    return amounts[0]
    return None


def plan_confirm(view, args: dict, uncertain_actions: list[str]) -> ConfirmPlan | str:
    target = args["target"]
    summary = args["summary"]
    line = view.by_index[target]
    if payment_requirement(view, target) is None:
        return "No money-related action is visible here. Use tap; nonfinancial actions don't require approval."
    on_screen = screen_amounts(view)
    amounts = find_amounts(summary)
    missing = [amount for amount in amounts if amount not in on_screen]
    if missing:
        return (f"The summary says {missing[0]} but the screen doesn't show it. Scroll until the total and the "
                f"button are both visible.")
    total = screen_total(view)
    if not amounts:
        return "Inspect the charge first and include its amount/currency and payee or order in the summary. If the charge cannot be established, ask the user."
    if total and total not in amounts:
        return f"Include the total shown on screen ({total}) in the summary."
    text = summary + f"\nButton: “{line.label}”"
    if total:
        text += f"\nTotal on screen: {total}"
    if any(line.label.lower() in note.lower() for note in uncertain_actions):
        text += f"\nAn earlier attempt at “{line.label}” may already have gone through. Check before approving."
    return ConfirmPlan(text=text, must_see=amounts, label=line.label, role=line.role,
                       package=view.screen.package_name, details=transaction_details(view), total=total)


def recheck_confirmation(plan: ConfirmPlan, view) -> int | str:
    if (view.screen.package_name != plan.package or transaction_details(view) != plan.details
            or screen_total(view) != plan.total):
        return "The transaction changed after approval; nothing was tapped. Ask again with the current details."
    matches = [line for line in view.lines if line.label == plan.label and line.role == plan.role]
    if len(matches) != 1:
        return "The screen changed after you approved; nothing was tapped. Ask again if it still makes sense."
    on_screen = screen_amounts(view)
    if any(amount not in on_screen for amount in plan.must_see):
        return "The total changed after you approved; nothing was tapped. Ask again with the new total."
    return matches[0].index


def passes_luhn(digits: str) -> bool:
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
        if 13 <= len(digits) <= 19 and passes_luhn(digits):
            return True
    if SECRET_QUESTION.search(question or "") and re.fullmatch(r"\s*\d{4,8}\s*", text):
        return True
    return False


def hide_secrets(text: str | None, question: str = "") -> str:
    return "[hidden]" if is_secret(text, question) else (text or "")


def _node_at(view, index: int):
    line = view.by_index.get(index)
    return line.node if line is not None else None
