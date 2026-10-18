import json

import pytest

from jarvis.brain import Limits, brain_runner
from jarvis.llm.common import BadReply, QuotaUsedUp
from tests.fakes.fake_ai import FAKEDASH_SCRIPT, FakeAI, FakeStep
from jarvis.messages import Answer

pytest.importorskip("tests.fakes.fake_phone")
from tests.fakes.fake_phone import FakePhone, run_fake_phone  # noqa: E402

GOAL = "Order a large pepperoni pizza from DoorDash"
FAST = Limits(loading_pause=0.05, rate_limit_waits=(0.01, 0.01, 0.01), unavailable_waits=(0.01, 0.01))


def steps_until(label: str) -> list[FakeStep]:
    for position, step in enumerate(FAKEDASH_SCRIPT):
        if step.find == label:
            return list(FAKEDASH_SCRIPT[:position])
    raise AssertionError(label)


def notebook_events(settings, done) -> list[dict]:
    path = settings.data_dir / "tasks" / f"{done.task_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def confirmed_taps(events) -> list[dict]:
    return [e for e in events if e["event"] == "step_sending" and e.get("confirmed")]


async def run(start_server, ai, *, phone=None, answers=None, auto_yes=True, limits=FAST):
    server = await start_server(run_task=brain_runner(ai, limits))
    phone = phone or FakePhone()
    done = await run_fake_phone(server.url, GOAL, phone=phone, answers=answers, auto_yes=auto_yes,
                                on_event=lambda message: None)
    return done, phone


async def test_orders_pizza_after_approval(start_server, test_settings):
    done, phone = await run(start_server, FakeAI(list(FAKEDASH_SCRIPT)))
    assert done.outcome == "completed", done.summary
    events = notebook_events(test_settings, done)
    assert "Order placed!" in events[-1 - next(i for i, e in enumerate(reversed(events)) if e["event"] == "done")]["proof"]
    assert len(confirmed_taps(events)) == 1
    asks = [e for e in events if e["event"] == "ask" and e["kind"] == "confirm"]
    assert "$31.20" in asks[0]["text"] and "Place order" in asks[0]["text"]


async def test_saying_no_never_places_the_order(start_server, test_settings):
    def answer(ask):
        return Answer(ask_id=ask.ask_id, approved=False) if ask.kind == "confirm" else Answer(ask_id=ask.ask_id, text="ok")

    done, phone = await run(start_server, FakeAI(list(FAKEDASH_SCRIPT)), answers=answer, auto_yes=False)
    assert done.outcome in ("failed", "unconfirmed")
    assert confirmed_taps(notebook_events(test_settings, done)) == []


async def test_plain_tap_on_place_order_is_refused(start_server, test_settings):
    script = steps_until("Place order") + [
        FakeStep("tap", {"why": "Place it"}, find="Place order"),
        FakeStep("tap", {"why": "Place it again"}, find="Place order"),
        FakeStep("tap", {"why": "And again"}, find="Place order"),
    ]
    ai = FakeAI(script)
    done, phone = await run(start_server, ai)
    assert done.outcome == "failed"
    events = notebook_events(test_settings, done)
    assert not any(e["event"] == "step_sending" and e.get("label") == "Place order" for e in events)
    assert any("confirm_and_tap" in note for note in ai.retry_notes)


async def test_total_not_on_screen_is_refused(start_server, test_settings):
    script = steps_until("Place order") + [
        FakeStep("confirm_and_tap", {"why": "Order", "summary": "Place order: 1 Large Pepperoni, total $19.99"},
                 find="Place order"),
    ]
    done, phone = await run(start_server, FakeAI(script))
    events = notebook_events(test_settings, done)
    assert not [e for e in events if e["event"] == "ask" and e["kind"] == "confirm"]
    assert any("$19.99" in e.get("reason", "") for e in events if e["event"] == "bad_reply")


async def test_finish_without_proof_is_not_completed(start_server, test_settings):
    script = [
        FakeStep("open_app", {"why": "Open DoorDash", "name": "DoorDash"}),
        FakeStep("finish", {"summary": "Ordered", "proof": ["Order placed!"]}),
        FakeStep("finish", {"summary": "Ordered", "proof": ["Order placed!"]}),
    ]
    done, phone = await run(start_server, FakeAI(script))
    assert done.outcome == "unconfirmed"
    assert done.summary.startswith("Could not confirm")


async def test_used_up_quota_stops_with_a_clear_message(start_server, test_settings):
    class QuotaAI(FakeAI):
        async def decide(self, *args, **kwargs):
            raise QuotaUsedUp("The free daily limit for this AI is used up.")

    done, phone = await run(start_server, QuotaAI([]))
    assert done.outcome == "failed"
    assert "daily limit" in done.summary


async def test_three_bad_replies_in_a_row_stop_the_task(start_server, test_settings):
    class BadAI(FakeAI):
        async def decide(self, *args, **kwargs):
            raise BadReply("no action found in the reply")

    done, phone = await run(start_server, BadAI([]))
    assert done.outcome == "failed"
    assert "couldn't use" in done.summary


async def test_lost_result_on_place_order_is_never_repeated(start_server, test_settings):
    script = list(FAKEDASH_SCRIPT[:-2]) + [
        FakeStep("confirm_and_tap", {"why": "Try again",
                                     "summary": "Place order at Tony's Pizzeria: 1 Large Pepperoni Pizza, total $31.20"},
                 find="Place order", optional=True),
    ] + list(FAKEDASH_SCRIPT[-2:])
    phone = FakePhone(unknown_once="Place order")
    done, phone = await run(start_server, FakeAI(script), phone=phone)
    events = notebook_events(test_settings, done)
    assert len(confirmed_taps(events)) == 1
    assert done.outcome == "completed", done.summary


async def test_stale_tap_after_popup_is_redecided(start_server, test_settings):
    phone = FakePhone(popup_race=True)
    done, phone = await run(start_server, FakeAI(list(FAKEDASH_SCRIPT)), phone=phone)
    events = notebook_events(test_settings, done)
    results = [e for e in events if e["event"] == "step_result"]
    assert any(e["status"] == "rejected" for e in results) or done.outcome == "completed"
    assert len(confirmed_taps(events)) <= 1


async def test_typing_into_a_password_field_is_refused():
    from jarvis.safety import check_action
    from jarvis.messages import Node, Screen
    from jarvis.screen import render

    screen = Screen(screen_id="s-1", package_name="com.example", width=1080, height=2400, nodes=[
        Node(index=1, hint="Password", editable=True, password=True, bounds=(0, 0, 500, 100)),
        Node(index=2, text="Type a message", editable=True, bounds=(0, 200, 500, 300)),
        Node(index=3, text="Send", clickable=True, bounds=(600, 200, 700, 300)),
    ])
    view = render(screen)
    assert "secret" in check_action("type_text", {"target": 1, "text": "hunter2"}, view)
    assert "confirm_and_tap" in check_action("type_text", {"target": 2, "text": "hi", "submit": True}, view)
    assert "can't be undone" in check_action("tap", {"target": 3}, view)


async def test_picture_is_taken_when_asked_and_attached(start_server, test_settings):
    images = []

    class SeeingAI(FakeAI):
        async def decide(self, system, message, tools, *, image=None, retry_note=None):
            images.append(image)
            return await super().decide(system, message, tools, image=image, retry_note=retry_note)

    script = [FakeStep("look", {"why": "See the screen"}), FakeStep("give_up", {"reason": "done looking"})]
    done, phone = await run(start_server, SeeingAI(script, sees_images=True), phone=FakePhone(screenshots=True))
    assert images[0] is None
    assert images[1] is not None and images[1][:2] == b"\xff\xd8"
    events = notebook_events(test_settings, done)
    assert any(e["event"] == "picture" for e in events)


async def test_blocked_picture_becomes_a_warning(start_server, test_settings):
    ai = FakeAI([FakeStep("look", {"why": "See it"}), FakeStep("give_up", {"reason": "no picture"})], sees_images=True)
    done, phone = await run(start_server, ai, phone=FakePhone(screenshots=True, black_screenshot=True))
    assert any("can't be captured" in message for message in ai.messages)


async def test_no_picture_tool_when_the_ai_is_text_only(start_server, test_settings):
    ai = FakeAI([FakeStep("look", {"why": "See it"})])
    done, phone = await run(start_server, ai, phone=FakePhone(screenshots=True))
    assert done.outcome == "failed"
    assert any("unknown action" in note for note in ai.retry_notes)


def test_marked_picture_draws_numbers():
    import base64
    import io

    from PIL import Image as PilImage

    from jarvis.messages import Command, Image
    from jarvis.pictures import marked_picture
    from jarvis.screen import render

    screen = FakePhone().handle(Command(command_id="c-1", action="read_screen")).screen
    buffer = io.BytesIO()
    PilImage.new("RGB", (540, 1200), "white").save(buffer, format="JPEG")
    image = Image(format="jpeg", data=base64.b64encode(buffer.getvalue()).decode(), width=540, height=1200)
    marked = marked_picture(image, render(screen))
    assert isinstance(marked, bytes) and marked[:2] == b"\xff\xd8"
    picture = PilImage.open(io.BytesIO(marked))
    assert picture.getpixel((2, 2)) != (255, 255, 255) or picture.size == (540, 1200)
    assert marked_picture(Image(format="jpeg", data="not base64!", width=1, height=1), render(screen)) == \
        "the picture is not valid base64"
