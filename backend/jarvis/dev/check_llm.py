import asyncio
import io
import json
import logging
import sys
import time
from dataclasses import dataclass

from jarvis.config import Settings, load_settings
from jarvis.llm.common import AI, AIError, Decision, check_route, make_ai
from jarvis.llm.tools import tools_for
from jarvis.messages import ACTIONS
from jarvis.prompt import SYSTEM_PROMPT, build_step_message

STORE_SCREEN = """\
App: DoorDash | Screen: Tony's Pizzeria | Keyboard: closed
[1] button "Back"
[2] heading "Tony's Pizzeria"
[3] text "4.7★ (2,300+) · 1.2 mi · $0.99 delivery"
[4] list (more below)
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


def store_screen() -> str:
    return STORE_SCREEN


async def timed(name: str, ai: AI, message: str, judge, image: bytes | None = None) -> Row:
    tools = tools_for(set(ACTIONS), ai.sees_images)
    started = time.monotonic()
    try:
        decision = await ai.decide(SYSTEM_PROMPT, message, tools, image=image)
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
    route = check_route(settings)
    try:
        ai = make_ai(settings)
    except AIError as error:
        print(error.message)
        return 1
    print(f"AI: {route.model} via {route.host} ({'free' if route.free else 'paid'}: {route.note})")
    print(f"Pictures: {'yes' if ai.sees_images else 'no'}\n")

    rows = [await timed("plain reply", ai, step_message(
        'Check that you can reply: call give_up with reason "hello".',
        'App: Test | Screen: Hello | Keyboard: closed\n[1] text "Hello"'), lambda d: (True, said(d)))]
    screen = store_screen()
    rows.append(await timed("action choice", ai, step_message("Add a pepperoni pizza to the cart", screen),
                            picked_pepperoni(screen)))
    if ai.sees_images:
        rows.append(await timed("picture", ai, step_message(
            "Look at the attached picture. Call give_up with a reason that names its main color in one word.",
            "App: Test | Screen: Picture | Keyboard: closed", image_attached=True), named_red, image=red_square()))
    else:
        rows.append(Row("picture", "skipped", 0.0, "this model is treated as text-only"))
    limits = getattr(ai, "rate_limits", {})
    rows.append(Row("rate limits", "info", 0.0,
                    ", ".join(f"{k}={v}" for k, v in limits.items())[:70] or "none returned"))

    print(f"{'check':<15} {'result':<8} {'seconds':>7}  notes")
    for row in rows:
        print(f"{row.check:<15} {row.status:<8} {row.seconds:>7.1f}  {row.notes}")
    picture = rows[2].status
    if picture == "ok":
        print("\nPictures work. LLM_IMAGES=auto or yes is fine.")
    elif picture == "failed":
        print("\nPictures did not work. Set LLM_IMAGES=no.")
    else:
        print("\nPictures were not tried. Set LLM_IMAGES=yes if you know this model sees images.")
    if hasattr(ai, "close"):
        await ai.close()
    return 1 if "failed" in (rows[0].status, rows[1].status) else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(asyncio.run(run_checks(load_settings())))


if __name__ == "__main__":
    main()
