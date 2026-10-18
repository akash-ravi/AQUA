import asyncio
import json

from jarvis.brain import Limits, brain_runner
from tests.fakes.fake_phone import FakePhone, run_fake_phone
from tests.fakes.fake_ai import FAKEDASH_SCRIPT, FakeAI, FakeStep
from jarvis.messages import Command

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
        if isinstance(message, Command) and message.action == "tap" and not stopped:
            stopped = True
            asyncio.get_running_loop().call_later(0.5, phone.user_stop)

    done = await run_fake_phone(server.url, GOAL, phone=phone, auto_yes=True, on_event=on_event)
    assert done.outcome == "stopped"
    events = events_of(test_settings, done.task_id)
    assert not any(e.get("label") == "Search DoorDash" for e in sent(events))


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
