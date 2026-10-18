import asyncio
import json

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from jarvis.messages import ACTIONS, Done, Result
from jarvis.phone import Interrupted, new_command

APPS = [{"label": "DoorDash", "package_name": "com.dd.doordash"}]


def hello(device_id: str = "fake-test", protocol: int = 1) -> str:
    return json.dumps({"type": "hello", "protocol": protocol, "device": {"id": device_id}, "actions": list(ACTIONS), "apps": APPS})


def task(text: str = "order a pizza") -> str:
    return json.dumps({"type": "task", "text": text})


async def receive(ws, seconds: float = 3) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), seconds))


async def closed_code(ws, seconds: float = 3) -> int | None:
    with pytest.raises(ConnectionClosed):
        while True:
            await asyncio.wait_for(ws.recv(), seconds)
    return ws.close_code


async def start_task(ws, device_id: str = "fake-test") -> dict:
    await ws.send(hello(device_id))
    await ws.send(task())
    ready = await receive(ws)
    assert ready["type"] == "ready"
    return ready


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
    assert done.outcome == "completed"
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


async def test_bad_json_is_not_fatal(start_server):
    server = await start_server(run_task=finish_now)
    async with connect(server.url) as ws:
        await ws.send("{not json")
        error = await receive(ws)
        assert error == {"type": "error", "code": "bad_message", "message": "not valid JSON", "fatal": False}
        await start_task(ws)
        assert (await receive(ws))["type"] == "done"
        assert await closed_code(ws) == 1000


async def test_wrong_stage_gets_error(start_server):
    server = await start_server(run_task=wait_for_stop)
    async with connect(server.url) as ws:
        await ws.send(task())
        error = await receive(ws)
        assert error["code"] == "unexpected_message" and error["fatal"] is False
        await start_task(ws)
        await ws.send(hello())
        error = await receive(ws)
        assert error["code"] == "unexpected_message" and error["fatal"] is False
        await ws.send(json.dumps({"type": "stop"}))
        done = await receive(ws)
        assert done["type"] == "done" and done["outcome"] == "stopped"


async def test_hello_timeout_closes(start_server):
    server = await start_server(run_task=finish_now, hello_seconds=0.3)
    async with connect(server.url) as ws:
        error = await receive(ws)
        assert error["code"] == "timeout" and error["fatal"] is True
        assert await closed_code(ws) == 1000


async def test_wrong_protocol_is_fatal(start_server):
    server = await start_server(run_task=finish_now)
    async with connect(server.url) as ws:
        await ws.send(hello(protocol=2))
        error = await receive(ws)
        assert error["code"] == "protocol_version" and error["fatal"] is True
        await closed_code(ws)


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
            assert (await receive(ws))["code"] == "bad_message"
        assert await closed_code(ws) == 1003


async def test_result_timeout_then_late_result(start_server):
    seen: dict = {}
    late_arrived = asyncio.Event()

    async def routine(run):
        def on_late(result: Result) -> None:
            seen["late"] = result
            late_arrived.set()

        run.link.on_late_result = on_late
        command = new_command("read_screen", run.link.revision).model_copy(update={"timeout_ms": 100})
        seen["command_id"] = command.command_id
        seen["result"] = await run.link.run(command, grace=0.1)
        await asyncio.wait_for(late_arrived.wait(), 3)
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        command = await receive(ws)
        assert command["type"] == "command" and command["action"] == "read_screen"
        await asyncio.sleep(0.5)
        await ws.send(json.dumps({"type": "result", "command_id": command["command_id"], "status": "ok"}))
        assert (await receive(ws))["type"] == "done"

    assert seen["result"].status == "unknown"
    assert seen["result"].error.code == "no_result"
    assert seen["late"].command_id == seen["command_id"]
    assert seen["late"].status == "ok"


async def test_result_for_unknown_command_is_ignored(start_server):
    seen: dict = {}

    async def routine(run):
        seen["result"] = await run.link.run(new_command("go_home", run.link.revision))
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        command = await receive(ws)
        await ws.send(json.dumps({"type": "result", "command_id": "c-nobody", "status": "failed"}))
        await ws.send(json.dumps({"type": "result", "command_id": command["command_id"], "status": "ok"}))
        assert (await receive(ws))["type"] == "done"
    assert seen["result"].status == "ok"


async def test_no_command_sent_after_stop(start_server):
    seen: dict = {}

    async def routine(run):
        await run.link.wait_changed()
        try:
            await run.link.run(new_command("go_home", run.link.revision))
        except Interrupted as stopped:
            seen["reason"] = stopped.reason
        return await finish(run, "stopped", "stopped")

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        await ws.send(json.dumps({"type": "stop"}))
        assert (await receive(ws))["type"] == "done"
    assert seen["reason"] == "stopped"


async def test_ask_answered(start_server):
    seen: dict = {}

    async def routine(run):
        seen["outcome"] = await run.link.ask("confirm", "Place order?", ["Yes", "No"], 30)
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        ask = await receive(ws)
        assert ask["type"] == "ask" and ask["kind"] == "confirm"
        await ws.send(json.dumps({"type": "answer", "ask_id": "q-other", "approved": True}))
        await ws.send(json.dumps({"type": "answer", "ask_id": ask["ask_id"], "approved": True}))
        assert (await receive(ws))["type"] == "done"
    assert seen["outcome"].status == "approved"


async def test_stop_cancels_open_ask(start_server):
    seen: dict = {}

    async def routine(run):
        seen["outcome"] = await run.link.ask("question", "Which size?", ["Small", "Large"], 30)
        return await finish(run, "stopped", "stopped")

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        ask = await receive(ws)
        await ws.send(json.dumps({"type": "stop"}))
        cancel = await receive(ws)
        assert cancel == {"type": "ask_cancel", "ask_id": ask["ask_id"], "reason": "stopped"}
        assert (await receive(ws))["type"] == "done"
    assert seen["outcome"].status == "cancelled"


async def test_ask_expires(start_server):
    seen: dict = {}

    async def routine(run):
        seen["outcome"] = await run.link.ask("question", "Which size?", None, 0)
        return await finish(run)

    server = await start_server(run_task=routine)
    async with connect(server.url) as ws:
        await start_task(ws)
        ask = await receive(ws)
        cancel = await receive(ws)
        assert cancel == {"type": "ask_cancel", "ask_id": ask["ask_id"], "reason": "expired"}
    assert seen["outcome"].status == "expired"


async def test_busy_device(start_server):
    server = await start_server(run_task=wait_for_stop)
    async with connect(server.url) as first:
        ready = await start_task(first)
        assert "fake-test" in server.runs
        async with connect(server.url) as second:
            await second.send(hello())
            await second.send(task())
            error = await receive(second)
            assert error["code"] == "busy" and error["fatal"] is True
            assert error["task_id"] == ready["task_id"]
        await first.send(json.dumps({"type": "stop"}))
        assert (await receive(first))["type"] == "done"
    await asyncio.sleep(0.1)
    assert server.runs == {}


async def test_brain_not_built_yet(start_server):
    server = await start_server()
    async with connect(server.url) as ws:
        await ws.send(hello())
        await ws.send(task())
        error = await receive(ws)
        assert error["code"] == "server_error" and error["fatal"] is True
        assert error["message"] == "the brain is not built yet"


async def test_rejoin_unknown_task(start_server):
    from jarvis.brain import brain_runner
    from tests.fakes.fake_ai import FakeAI

    server = await start_server(run_task=brain_runner(FakeAI([])))
    async with connect(server.url) as ws:
        await ws.send(hello())
        await ws.send(json.dumps({"type": "rejoin", "task_id": "t-x", "rejoin_key": "k"}))
        error = await receive(ws)
        assert error["code"] == "task_not_found" and error["fatal"] is True
