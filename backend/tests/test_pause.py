import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from jarvis.brain import brain_runner
from jarvis.llm.common import Decision
from tests.test_link import receive, start_task


def screen_reply(command, screen_id):
    return {"type": "result", "id": command["id"], "ok": True, "data": {
        "screenId": screen_id, "package": "test.app", "nodes": [
            {"index": 0, "text": "Example screen", "bounds": [0, 0, 500, 100]}]}}


@pytest.mark.parametrize("in_flight", [False, True])
async def test_pause_continue_keeps_task_and_reads_fresh_screen(start_server, in_flight):
    class AI:
        sees_images = False
        calls = 0
        started = asyncio.Event()

        async def decide(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                if in_flight:
                    return Decision(tool="go_home", args={"why": "Open home"})
                await asyncio.Event().wait()
            return Decision(tool="give_up", args={"reason": "Test complete"})

    ai = AI()
    server = await start_server(run_task=brain_runner(ai))
    async with connect(server.url) as ws:
        await start_task(ws)
        first_read = await receive(ws)
        await ws.send(json.dumps(screen_reply(first_read, "before")))
        await asyncio.wait_for(ai.started.wait(), 2)
        if in_flight:
            action = await receive(ws)
            assert action["name"] == "home"
        await ws.send(json.dumps({"type": "pause"}))
        if in_flight:
            # Pause must account for the outstanding action before acknowledging it.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.05)
            await ws.send(json.dumps({"type": "result", "id": action["id"], "ok": True, "data": {}}))
        paused = await receive(ws)
        assert paused["type"] == "progress" and paused["state"] == "paused"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.05)
        await ws.send(json.dumps({"type": "resume"}))
        assert (await receive(ws))["state"] == "running"
        fresh = await receive(ws)
        assert fresh["name"] == "readScreen"
        await ws.send(json.dumps(screen_reply(fresh, "after")))
        assert (await receive(ws))["type"] == "done"
        assert ai.calls == 2


@pytest.mark.parametrize("stop", [False, True])
async def test_stopping_or_disconnecting_a_paused_task_releases_it(start_server, stop):
    class AI:
        sees_images = False
        started = asyncio.Event()

        async def decide(self, *args, **kwargs):
            self.started.set()
            await asyncio.Event().wait()

    ai = AI()
    server = await start_server(run_task=brain_runner(ai))
    async with connect(server.url) as ws:
        await start_task(ws)
        await ws.send(json.dumps(screen_reply(await receive(ws), "before")))
        await asyncio.wait_for(ai.started.wait(), 2)
        await ws.send(json.dumps({"type": "pause"}))
        assert (await receive(ws))["state"] == "paused"
        if stop:
            await ws.send(json.dumps({"type": "stop"}))
    for _ in range(50):
        if not server.runs:
            break
        await asyncio.sleep(0.01)
    assert not server.runs
