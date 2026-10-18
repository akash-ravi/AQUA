import asyncio
import json

from jarvis.brain import Limits, brain_runner
from tests.fakes.fake_phone import FakePhone, run_fake_phone
from tests.fakes.fake_ai import FAKEDASH_SCRIPT, FakeAI, FakeStep
from jarvis.messages import Progress, Rejoin

GOAL = "Order a large pepperoni pizza from DoorDash"
FAST = Limits(loading_pause=0.05)


def events_of(settings, task_id) -> list[dict]:
    path = settings.data_dir / "tasks" / f"{task_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def sent(events) -> list[dict]:
    return [e for e in events if e["event"] == "step_sending"]


async def test_stop_while_the_ai_is_thinking_sends_nothing_more(start_server, test_settings):
    script = list(FAKEDASH_SCRIPT[:2]) + [FakeStep("tap", {"why": "Open search"}, find="Search DoorDash", delay=2.0)]
    server = await start_server(run_task=brain_runner(FakeAI(script), FAST))
    phone = FakePhone()
    stopped = False

    def on_event(message):
        nonlocal stopped
        if isinstance(message, Progress) and message.text == "Close the DashPass offer" and not stopped:
            stopped = True
            asyncio.get_running_loop().call_later(0.5, phone.user_stop)

    done = await run_fake_phone(server.url, GOAL, phone=phone, auto_yes=True, on_event=on_event)
    assert done.outcome == "stopped"
    events = events_of(test_settings, done.task_id)
    assert not any(e.get("label") == "Search DoorDash" for e in sent(events))


async def test_pause_then_resume_continues_the_task(start_server, test_settings):
    server = await start_server(run_task=brain_runner(FakeAI(list(FAKEDASH_SCRIPT)), FAST))
    phone = FakePhone()
    paused = False

    def on_event(message):
        nonlocal paused
        if isinstance(message, Progress) and message.text == "Open search" and not paused:
            paused = True
            phone.user_pause()
        elif isinstance(message, Progress) and message.state == "paused":
            asyncio.get_running_loop().call_later(0.2, phone.user_resume)

    done = await run_fake_phone(server.url, GOAL, phone=phone, auto_yes=True, on_event=on_event)
    events = events_of(test_settings, done.task_id)
    statuses = [e.get("to") for e in events if e["event"] == "status"]
    assert statuses[:2] == ["paused", "running"]
    resumed_at = next(i for i, e in enumerate(events) if e["event"] == "status" and e.get("to") == "running")
    assert any(e["event"] == "step_sending" for e in events[resumed_at:])


async def test_correction_reaches_the_ai(start_server, test_settings):
    ai = FakeAI(list(FAKEDASH_SCRIPT))
    server = await start_server(run_task=brain_runner(ai, FAST))
    phone = FakePhone()
    sent_update = False

    def on_event(message):
        nonlocal sent_update
        if isinstance(message, Progress) and message.text == "Open search" and not sent_update:
            sent_update = True
            phone.user_update("make it a medium")

    done = await run_fake_phone(server.url, GOAL, phone=phone, auto_yes=True, on_event=on_event)
    assert any("make it a medium" in message for message in ai.messages)
    events = events_of(test_settings, done.task_id)
    assert any(e["event"] == "update" and e["revision"] == 1 for e in events)


async def test_drop_after_place_order_then_rejoin_does_not_order_twice(start_server, test_settings):
    script = list(FAKEDASH_SCRIPT[:-2]) + [
        FakeStep("confirm_and_tap", {"why": "Try again",
                                     "summary": "Place order at Tony's Pizzeria: 1 Large Pepperoni Pizza, total $31.20"},
                 find="Place order", optional=True),
    ] + list(FAKEDASH_SCRIPT[-2:])
    server = await start_server(run_task=brain_runner(FakeAI(script), FAST))
    phone = FakePhone(drop_after="Place order")

    first = await run_fake_phone(server.url, GOAL, phone=phone, auto_yes=True, on_event=lambda m: None)
    assert first is None
    await asyncio.sleep(0.3)
    rejoin = Rejoin(task_id=phone.task_id, rejoin_key=phone.rejoin_key, last_result=phone.last_result)
    done = await run_fake_phone(server.url, GOAL, phone=phone, auto_yes=True, rejoin=rejoin, on_event=lambda m: None)

    assert done is not None and done.outcome == "completed", done and done.summary
    events = events_of(test_settings, done.task_id)
    confirmed = [e for e in sent(events) if e.get("confirmed")]
    assert len(confirmed) == 1


async def test_rejoin_with_the_wrong_key_is_refused(start_server, test_settings):
    server = await start_server(run_task=brain_runner(FakeAI(list(FAKEDASH_SCRIPT)), FAST))
    phone = FakePhone(drop_after="Search DoorDash")
    await run_fake_phone(server.url, GOAL, phone=phone, auto_yes=True, on_event=lambda m: None)
    await asyncio.sleep(0.2)
    errors = []
    rejoin = Rejoin(task_id=phone.task_id, rejoin_key="wrong", last_result=phone.last_result)
    done = await run_fake_phone(server.url, GOAL, phone=FakePhone(), rejoin=rejoin,
                                on_event=lambda m: errors.append(m))
    assert done is None
    assert any(getattr(m, "code", "") == "task_not_found" for m in errors)


def test_restart_marks_unanswered_steps_unknown(tmp_path):
    from jarvis.task import Notebook, mark_interrupted_after_restart

    notebook = Notebook.for_task(tmp_path, "t-1")
    notebook.write("task", task_id="t-1", goal="x")
    notebook.write("step_sending", n=1, command_id="c-1", action="tap")
    assert mark_interrupted_after_restart(tmp_path) == ["t-1"]
    events = notebook.read()
    assert events[-2]["event"] == "step_result" and events[-2]["status"] == "unknown"
    assert events[-1]["event"] == "status" and events[-1]["to"] == "interrupted"
    assert mark_interrupted_after_restart(tmp_path) == []
