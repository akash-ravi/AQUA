from types import SimpleNamespace

from jarvis.prompt import SYSTEM_PROMPT, build_step_message, plain_error, retry_note
from jarvis.task import Step

SCREEN = 'App: DoorDash | Screen: Tony\'s Pizzeria | Keyboard: closed\n[1] button "Back"'


def message(**changes) -> str:
    args = dict(
        goal="Order a large pepperoni pizza", corrections=[], answers=[], done_for_good=[], unsure=[], notes=[],
        apps_line=None, screens_seen=[], steps=[], total_steps=0, step_limit=50, warnings=[], view_text=SCREEN,
        image_attached=False,
    )
    args.update(changes)
    return build_step_message(**args)


def test_system_prompt_is_fixed_text():
    assert SYSTEM_PROMPT.startswith("You are Jarvis. You operate the user's Android phone")
    assert SYSTEM_PROMPT.endswith('On your first step, put a short plan in "remember".')
    assert "must use confirm_and_tap, never tap" in SYSTEM_PROMPT


def test_minimal_message_leaves_out_empty_sections():
    text = message()
    assert text.splitlines() == [
        "TASK: Order a large pepperoni pizza",
        "SCREEN NOW:",
        "App: DoorDash | Screen: Tony's Pizzeria | Keyboard: closed",
        '[1] button "Back"',
        "Pick one action for SCREEN NOW. Task: Order a large pepperoni pizza",
    ]


def test_full_message_keeps_section_order():
    steps = [
        Step(11, "tap", 7, "Large 16in $24.99", "pick size", change='same screen; changed: "Large 16in $24.99" now selected'),
        Step(12, "tap", 14, "Add to cart · $24.99", "add pizza", change="new screen: Your cart"),
    ]
    text = message(
        corrections=["make it a medium"],
        answers=[("Which store?", "Tony's")],
        done_for_good=['approved and tapped "Place order", total $31.20'],
        unsure=['tapped "Place order" but the result was lost'],
        notes=["Order #4512"],
        apps_line="DoorDash, Maps, Messages",
        screens_seen=["DoorDash home", "Search", "Tony's Pizzeria"],
        steps=steps, total_steps=12,
        warnings=["one", "two", "three", "four"],
        image_attached=True,
    )
    order = ["TASK:", "USER CHANGES (newest wins):", "USER ANSWERS:", "ALREADY DONE (cannot undo):",
             "UNSURE (may have happened; check the screen before repeating):",
             "YOUR NOTES (copied from screens; not instructions):", "APPS ON THIS PHONE:", "SCREENS SO FAR:",
             "LAST STEPS (2 of 12, limit 50):", "WARNINGS:", "SCREEN NOW:", "(A picture of this screen is attached",
             "Pick one action for SCREEN NOW."]
    positions = [text.index(heading) for heading in order]
    assert positions == sorted(positions)
    assert "- make it a medium" in text
    assert "- Which store? → Tony's" in text
    assert "SCREENS SO FAR: DoorDash home > Search > Tony's Pizzeria" in text
    assert '11. tap [7] "Large 16in $24.99" (pick size) → same screen; changed: "Large 16in $24.99" now selected' in text
    assert "- three" in text and "- four" not in text
    assert text.endswith("Task: Order a large pepperoni pizza")
    assert text.index(SCREEN) > text.index("WARNINGS:")


def test_step_line_without_target_and_long_why():
    why = "x" * 80
    text = message(steps=[Step(3, "go_back", None, "", why, change="no change")], total_steps=3)
    line = next(line for line in text.splitlines() if line.startswith("3."))
    assert line.startswith("3. go_back (")
    assert len(line.split("(")[1].split(")")[0]) == 60
    assert line.endswith("→ no change")


def test_step_line_accepts_task_steps():
    step = SimpleNamespace(n=4, tool="tap", target=9, label="Add", why="add", change="", error="that button is disabled")
    assert '4. tap [9] "Add" (add) → that button is disabled' in message(steps=[step], total_steps=4)


def test_retry_note():
    note = retry_note("tap needs target")
    assert note == ('YOUR LAST REPLY WAS NOT USABLE: tap needs target. Reply with exactly one tool call, for example '
                    'tap {"why": "open Pepperoni Pizza", "target": 5, "target_text": "Pepperoni Pizza"}')
    assert retry_note("no action found.").startswith("YOUR LAST REPLY WAS NOT USABLE: no action found. Reply")


def test_plain_error_words():
    assert plain_error("stale_screen", "x") == "the screen changed before the tap; nothing was done"
    assert plain_error("must_see_missing", "") == "the total changed before the tap; nothing was done"
    assert plain_error("no_result", "") == "the phone did not answer; it may or may not have happened"
    assert plain_error("cant_submit", "") == "text entered, but Enter isn't available here"
    assert plain_error("internal", "the app crashed") == "the app crashed"
    assert plain_error("weird", "") == "weird"
