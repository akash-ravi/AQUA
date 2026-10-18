import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse

from src.config import Settings
from src.phone.link import PhoneGone, PhoneLink
from src.phone.messages import new_id

log = logging.getLogger("jarvis.server")

START_SECONDS = 10.0


@dataclass
class Run:
    device_id: str
    task_id: str
    goal: str
    link: PhoneLink
    settings: Settings


RunTask = Callable[[Run], Awaitable[None]]


def create_app(settings: Settings, routine: RunTask) -> FastAPI:
    app = FastAPI()

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        return "ok"

    @app.websocket("/agent")
    async def agent(websocket: WebSocket) -> None:
        # Browsers always send Origin; the phone app never does, so this keeps web pages out.
        if "origin" in websocket.headers:
            log.info("Refused a connection with an Origin header.")
            await websocket.close(code=1008)
            return
        await websocket.accept()
        link = PhoneLink(websocket)
        try:
            await handle_phone(link, settings, routine, websocket.client.host if websocket.client else "")
        except PhoneGone:
            log.info("The phone disconnected.")
        finally:
            await link.close()

    return app


async def handle_phone(link: PhoneLink, settings: Settings, routine: RunTask, device_id: str) -> None:
    start = await wait_for_start(link)
    if start is None:
        return

    run = Run(device_id, new_id("t"), start.task, link, settings)
    log.info("New task %s from %s.", run.task_id, device_id)
    try:
        link.start(start)
        await link.load_apps()
        await routine(run)
    except PhoneGone:
        log.info("Task %s: the phone disconnected.", run.task_id)
    except Exception:
        log.exception("Task %s crashed.", run.task_id)
        await link.fail("Something went wrong on the Mac.")


async def wait_for_start(link: PhoneLink):
    try:
        async with asyncio.timeout(START_SECONDS):
            while (message := await link.receive()) is not None:
                if message.type == "start":
                    return message
                await link.reject(f"expected start, got {message.type}")
    except TimeoutError:
        log.info("The phone sent no start in time.")
        await link.fail(f"No start within {START_SECONDS:g} seconds.")
    return None
