import asyncio
import json

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from jarvis.messages import Done, Result
from jarvis.phone import Interrupted, new_command

APPS = [{"label": "DoorDash", "package": "com.dd.doordash"}]
START = {"type": "start", "task": "order a pizza", "device": {"width": 1080, "height": 2400, "sdk": 36}}


async def receive(ws, seconds: float = 3) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), seconds))


async def closed_code(ws, seconds: float = 3) -> int | None:
    with pytest.raises(ConnectionClosed):
        while True:
            await asyncio.wait_for(ws.recv(), seconds)
    return ws.close_code


async def start_task(ws) -> None:
    await ws.send(json.dumps(START))
    listing = await receive(ws)
    assert listing["type"] == "command" and listing["name"] == "listApps"
    await ws.send(json.dumps({"type": "result", "id": listing["id"], "ok": True, "data": {"apps": APPS}}))


async def finish(run, outcome: str = "completed", summary: str = "ok") -> Done:
    done = Done(task_id=run.task_id, outcome=outcome, summary=summary)
    await run.link.finish(done)
    return done


async def finish_now(run) -> Done:
    return await finish(run)


async def wait_for_stop(run) -> Done:
    try:
        await run.link.interruptible(asyncio.Event().wait())
    except Interrupted:
        pass
    return await finish(run, "stopped", "stopped")


async def test_health(start_server):
    server = await start_server(run_task=finish_now)
    async with httpx.AsyncClient() as client:
        response = await client.get(server.http_url + "/health")
    assert response.status_code == 200
    assert response.text == "ok"


async def test_smoke_routine_with_fake_phone(start_server, capsys):
    from tests.fakes.fake_phone import run_fake_phone

    server = await start_server(smoke=True)
    events = []
    done = await run_fake_phone(server.url, "smoke test", auto_yes=True, on_event=events.append)
    assert done is not None
    assert done.summary == "Smoke test passed: the phone answered 4 commands."
    printed = capsys.readouterr().out
    assert "open_app: ok" in printed
    assert "DoorDash" in printed


async def test_origin_header_refused(start_server):
    server = await start_server(run_task=finish_now)
    with pytest.raises(InvalidStatus) as refused:
        async with connect(server.url, origin="http://example.com"):
            pass
    assert refused.value.response.status_code == 403


async def test_apps_are_listed_first_and_done_is_just_a_summary(start_server):
    seen: dict = {}

    async def routine(run):
        seen["apps"] = dict(run.link.apps)
        seen["goal"] = run.goal
        return await finish(run, summary="Burrito added to cart")

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        assert await receive(ws) == {"type": "done", "summary": "Burrito added to cart"}
        assert await closed_code(ws) == 1000
    assert seen == {"apps": {"com.dd.doordash": "DoorDash"}, "goal": "order a pizza"}


async def test_commands_use_the_phone_names_and_args(start_server):
    seen: list[Result] = []

    async def routine(run):
        seen.append(await run.link.run(new_command("read_screen")))
        screen_id = seen[0].screen.screen_id
        seen.append(await run.link.run(new_command("tap", screen_id=screen_id, target=4, confirmed=True)))
        seen.append(await run.link.run(new_command("open_app", package_name="com.dd.doordash")))
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        read = await receive(ws)
        assert read["name"] == "readScreen" and read["args"] == {}
        nodes = [{"index": 0, "class": "Button", "text": "Pay", "desc": None, "id": None, "bounds": [0, 0, 100, 50],
                  "clickable": True, "editable": False, "scrollable": False, "checked": None}]
        await ws.send(json.dumps({"type": "result", "id": read["id"], "ok": True,
                                  "data": {"screenId": "s7", "package": "com.dd.doordash", "nodes": nodes}}))
        click = await receive(ws)
        assert click["name"] == "click" and click["args"] == {"screenId": "s7", "index": 4, "confirmed": True}
        await ws.send(json.dumps({"type": "result", "id": click["id"], "ok": False, "error": "staleScreen",
                                  "message": "old"}))
        opened = await receive(ws)
        assert opened["name"] == "openApp" and opened["args"] == {"name": "com.dd.doordash"}
        await ws.send(json.dumps({"type": "result", "id": opened["id"], "ok": True, "data": {}}))
        assert (await receive(ws))["type"] == "done"

    screen = seen[0].screen
    assert screen.package_name == "com.dd.doordash" and screen.width == 1080 and screen.nodes[0].text == "Pay"
    assert seen[1].status == "rejected" and seen[1].error.code == "stale_screen"
    assert seen[2].status == "ok"


async def test_start_timeout_closes(start_server):
    server = await start_server(run_task=finish_now, start_seconds=0.3)
    async with connect(server.url) as ws:
        error = await receive(ws)
        assert error["type"] == "error"
        assert await closed_code(ws) == 1000


async def test_bad_json_is_not_fatal(start_server):
    server = await start_server(run_task=finish_now)
    async with connect(server.url) as ws:
        await ws.send("{not json")
        assert (await receive(ws))["type"] == "error"
        await start_task(ws)
        assert (await receive(ws))["type"] == "done"


async def test_binary_frame_closes(start_server):
    server = await start_server(run_task=finish_now)
    async with connect(server.url) as ws:
        await ws.send(b"\x00\x01")
        assert await closed_code(ws) == 1003


async def test_five_bad_messages_close(start_server):
    server = await start_server(run_task=finish_now)
    async with connect(server.url) as ws:
        for _ in range(5):
            await ws.send("nope")
        for _ in range(4):
            assert (await receive(ws))["type"] == "error"
        assert await closed_code(ws) == 1003


async def test_result_timeout_then_late_result(start_server):
    seen: dict = {}
    late_arrived = asyncio.Event()

    async def routine(run):
        def on_late(result: Result) -> None:
            seen["late"] = result
            late_arrived.set()

        run.link.on_late_result = on_late
        command = new_command("go_home").model_copy(update={"timeout_ms": 100})
        seen["command_id"] = command.command_id
        seen["result"] = await run.link.run(command, grace=0.1)
        await asyncio.wait_for(late_arrived.wait(), 3)
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        command = await receive(ws)
        await asyncio.sleep(0.5)
        await ws.send(json.dumps({"type": "result", "id": command["id"], "ok": True, "data": {}}))
        assert (await receive(ws))["type"] == "done"

    assert seen["result"].status == "unknown" and seen["result"].error.code == "no_result"
    assert seen["late"].command_id == seen["command_id"] and seen["late"].status == "ok"


async def test_result_for_unknown_command_is_ignored(start_server):
    seen: dict = {}

    async def routine(run):
        seen["result"] = await run.link.run(new_command("go_home"))
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        command = await receive(ws)
        await ws.send(json.dumps({"type": "result", "id": "c-nobody", "ok": False, "error": "actionFailed"}))
        await ws.send(json.dumps({"type": "result", "id": command["id"], "ok": True, "data": {}}))
        assert (await receive(ws))["type"] == "done"
    assert seen["result"].status == "ok"


async def test_stop_from_the_phone_closes_without_done(start_server):
    server = await start_server(run_task=wait_for_stop)
    async with connect(server.url) as ws:
        await start_task(ws)
        await ws.send(json.dumps({"type": "stop"}))
        assert await closed_code(ws) == 1000


async def test_no_command_is_sent_after_stop(start_server):
    seen: dict = {}

    async def routine(run):
        await asyncio.sleep(0.3)
        with pytest.raises(Interrupted):
            await run.link.run(new_command("go_home"))
        seen["stopped"] = run.link.stop_requested
        return await finish(run, "stopped", "stopped")

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        await ws.send(json.dumps({"type": "stop"}))
        assert await closed_code(ws) == 1000
    assert seen["stopped"] is True


async def test_confirm_question_is_a_plain_question_and_yes_approves(start_server):
    seen: dict = {}

    async def routine(run):
        seen["outcome"] = await run.link.ask("confirm", "Place order for $14.20?", ["Yes", "No"])
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        ask = await receive(ws)
        assert ask["type"] == "ask" and ask["question"] == "Place order for $14.20? (Yes / No)"
        await ws.send(json.dumps({"type": "answer", "id": ask["id"], "text": "yes"}))
        assert (await receive(ws))["type"] == "done"
    assert seen["outcome"].status == "approved"


async def test_other_answers_to_a_confirm_are_text_or_no(start_server):
    seen: list = []

    async def routine(run):
        for _ in range(2):
            seen.append(await run.link.ask("confirm", "Order?", ["Yes", "No"]))
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        for reply in ("no", "make it two"):
            ask = await receive(ws)
            await ws.send(json.dumps({"type": "answer", "id": ask["id"], "text": reply}))
        assert (await receive(ws))["type"] == "done"
    assert [(o.status, o.text) for o in seen] == [("declined", ""), ("text", "make it two")]


async def test_ask_expires(start_server):
    seen: dict = {}

    async def routine(run):
        seen["outcome"] = await run.link.ask("question", "Which size?", expires_in=1)
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        assert (await receive(ws))["type"] == "ask"
        assert (await receive(ws, 3))["type"] == "done"
    assert seen["outcome"].status == "expired"


async def test_brain_not_built_yet(start_server):
    server = await start_server()
    async with connect(server.url) as ws:
        await ws.send(json.dumps(START))
        assert (await receive(ws))["type"] == "error"
        assert await closed_code(ws) == 1000
