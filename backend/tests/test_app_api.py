import asyncio

import httpx

from jarvis.brain import Limits, brain_runner
from jarvis.messages import Command
from tests.fakes.fake_ai import FAKEDASH_SCRIPT, FakeAI, FakeStep
from tests.fakes.fake_phone import FakePhone, run_fake_phone

GOAL = "Order a large pepperoni pizza from DoorDash"
FAST = Limits(loading_pause=0.05)


async def test_history_and_task_story_after_an_order(start_server):
    server = await start_server(run_task=brain_runner(FakeAI(list(FAKEDASH_SCRIPT)), FAST))
    done = await run_fake_phone(server.url, GOAL, phone=FakePhone(), auto_yes=True, on_event=lambda m: None)

    async with httpx.AsyncClient(base_url=server.http_url) as client:
        tasks = (await client.get("/tasks")).json()["tasks"]
        assert tasks[0]["task_id"] == done.task_id
        assert tasks[0]["status"] == "completed" and tasks[0]["goal"] == GOAL and tasks[0]["steps"] > 10

        story = (await client.get(f"/tasks/{done.task_id}")).json()
        assert story["proof"] == ["Order placed!", "Order #4512"]
        assert story["steps"][0]["action"] == "open_app"
        confirm = [q for q in story["questions"] if q["kind"] == "confirm"][0]
        assert "$31.20" in confirm["text"] and confirm["answer"] == "approved"
        assert story["done_for_good"] and "Place order" in story["done_for_good"][0]

        assert (await client.get("/tasks", params={"device_id": "someone-else"})).json()["tasks"] == []
        assert (await client.get("/tasks/t-0000000000000000")).status_code == 404
        assert (await client.get("/tasks/..%2F..%2Fetc")).status_code == 404


async def test_status_shows_the_ai_and_running_tasks(start_server):
    script = [FakeStep("go_home", {"why": "Go home"}), FakeStep("wait", {"why": "Wait a bit", "seconds": 1}, delay=1.0)]
    server = await start_server(run_task=brain_runner(FakeAI(script), FAST))
    seen = {}

    async def look(message):
        if isinstance(message, Command) and message.action == "go_home" and "running" not in seen:
            async with httpx.AsyncClient(base_url=server.http_url) as client:
                seen["running"] = (await client.get("/status")).json()

    await run_fake_phone(server.url, "go home", phone=FakePhone(), auto_yes=True, on_event=look)
    status = seen["running"]
    assert status["ok"] is True and status["mode"] == "normal"
    assert status["ai"]["model"] == "z-ai/glm-5.3-flash" and status["ai"]["free"] is True
    assert status["running"][0]["goal"] == "go home"
    assert status["running"][0]["now"] == "Go home"


async def test_stop_button_in_the_app(start_server):
    script = [FakeStep("go_home", {"why": "Go home"}), FakeStep("tap", {"why": "Never"}, find="DoorDash", delay=3.0)]
    server = await start_server(run_task=brain_runner(FakeAI(script), FAST))
    phone = FakePhone()
    replies = []

    async def press_stop(message):
        if isinstance(message, Command) and message.action == "go_home" and not replies:
            await asyncio.sleep(0.2)
            async with httpx.AsyncClient(base_url=server.http_url) as client:
                replies.append(await client.post(f"/tasks/{phone.task_id}/stop"))
                replies.append(await client.post(f"/tasks/{phone.task_id}/stop", headers={"Origin": "http://evil.example"}))

    done = await run_fake_phone(server.url, "open DoorDash", phone=phone, auto_yes=True, on_event=press_stop)
    assert replies[0].json() == {"stopping": True, "task_id": phone.task_id}
    assert replies[1].status_code == 403
    assert done.outcome == "stopped"


async def test_stop_for_a_task_that_is_not_running(start_server):
    server = await start_server(run_task=brain_runner(FakeAI([]), FAST))
    async with httpx.AsyncClient(base_url=server.http_url) as client:
        assert (await client.post("/tasks/t-0000000000000000/stop")).status_code == 404
