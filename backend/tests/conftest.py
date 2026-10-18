import asyncio
from dataclasses import dataclass

import pytest
import uvicorn
from fastapi import FastAPI

from jarvis.config import Settings
from jarvis.server import create_app


@dataclass
class RunningServer:
    url: str  # ws://127.0.0.1:<port>/agent
    http_url: str
    app: FastAPI

    @property
    def runs(self) -> dict:
        return self.app.state.runs


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")


@pytest.fixture
async def start_server(test_settings):
    """Call `await start_server(smoke=..., run_task=..., hello_seconds=...)` to get a live server; all stop at teardown."""
    started: list[tuple[uvicorn.Server, asyncio.Task]] = []

    async def start(*, settings: Settings | None = None, **options) -> RunningServer:
        app = create_app(settings or test_settings, **options)
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, lifespan="off", log_level="warning", timeout_graceful_shutdown=2
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        started.append((server, task))
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        return RunningServer(f"ws://127.0.0.1:{port}/agent", f"http://127.0.0.1:{port}", app)

    yield start

    for server, task in started:
        server.should_exit = True
    for server, task in started:
        await asyncio.wait_for(task, 10)
