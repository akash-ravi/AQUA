import asyncio
import io
import json
import logging
import sys
import time
from dataclasses import dataclass

from jarvis.claude import AIError, ClaudeAI, Decision
from jarvis.config import Settings
from jarvis.prompt import SYSTEM_PROMPT, build_step_message

STORE_SCREEN = """\
App: DoorDash
[1] button "Back"
[2] text "Tony's Pizzeria"
[3] text "4.7★ (2,300+) · 1.2 mi · $0.99 delivery"
[4] list
  [5] item "Pepperoni Pizza · $18.99 · Classic pepperoni, mozzarella"
    [8] button "Add" (for Pepperoni Pizza)
  [9] item "Margherita Pizza · $16.49 · Fresh basil, mozzarella"
    [12] button "Add" (for Margherita Pizza)"""


@dataclass
class Row:
    check: str
    status: str
    seconds: float
    notes: str


def step_message(goal: str, view_text: str, image_attached: bool = False) -> str:
    return build_step_message(goal=goal, corrections=[], answers=[], done_for_good=[], unsure=[], notes=[],
                              apps_line=None, screens_seen=[], steps=[], total_steps=0, step_limit=50, warnings=[],
                              view_text=view_text, image_attached=image_attached)


def red_square() -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (220, 20, 20)).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


async def timed(name: str, ai: ClaudeAI, message: str, judge, image: bytes | None = None) -> Row:
    started = time.monotonic()
    try:
        decision = await ai.decide(SYSTEM_PROMPT, message, image=image)
    except AIError as error:
        return Row(name, "failed", time.monotonic() - started, error.message)
    ok, notes = judge(decision)
    return Row(name, "ok" if ok else "failed", time.monotonic() - started, notes)


def said(decision: Decision) -> str:
    return f"{decision.tool} {json.dumps(decision.args, ensure_ascii=False)}"[:70]


def picked_pepperoni(screen_text: str):
    wanted = [line for line in screen_text.splitlines() if "Pepperoni Pizza" in line and "[" in line]
    numbers = {int(line.split("[")[1].split("]")[0]) for line in wanted}

    def judge(decision: Decision):
        return decision.tool == "tap" and decision.args.get("target") in numbers, said(decision)
    return judge


def named_red(decision: Decision):
    words = f"{decision.text} {json.dumps(decision.args)}".lower()
    return "red" in words, said(decision)


async def run_checks(settings: Settings) -> int:
    ai = ClaudeAI(settings)
    print(f"AI: {ai.name} · effort {settings.effort}\n")
    rows = [
        await timed("plain reply", ai, step_message(
            'Check that you can reply: call give_up with reason "hello".',
            'App: Test\n[1] text "Hello"'), lambda d: (True, said(d))),
        await timed("action choice", ai, step_message("Add a pepperoni pizza to the cart", STORE_SCREEN),
                    picked_pepperoni(STORE_SCREEN)),
        await timed("picture", ai, step_message(
            "Look at the attached picture. Call give_up with a reason that names its main color in one word.",
            "App: Test", image_attached=True), named_red, image=red_square()),
    ]
    print(f"{'check':<15} {'result':<8} {'seconds':>7}  notes")
    for row in rows:
        print(f"{row.check:<15} {row.status:<8} {row.seconds:>7.1f}  {row.notes}")
    return 1 if any(row.status == "failed" for row in rows) else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(asyncio.run(run_checks(Settings())))


if __name__ == "__main__":
    main()
