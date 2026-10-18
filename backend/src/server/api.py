import argparse
import asyncio
import logging
import socket
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse

from jarvis.config import Settings
from jarvis.messages import Done, Result, new_id
from jarvis.phone import Interrupted, PhoneGone, PhoneLink, new_command
from jarvis.screen import render

log = logging.getLogger("jarvis.server")

START_SECONDS = 10.0


@dataclass
class Run:
    device_id: str
    task_id: str
    goal: str
    link: PhoneLink
    settings: Settings


RunTask = Callable[[Run], Awaitable[Done | None]]


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


async def run_smoke(run: Run) -> Done:
    link = run.link
    answered = 0

    async def send(action: str, show: bool, **fields) -> None:
        nonlocal answered
        result = await link.run(new_command(action, **fields))
        if not (result.error and result.error.code == "no_result"):
            answered += 1
        print_result(action, result, link.apps, show)

    try:
        await send("read_screen", True)
        await send("go_home", False)
        app = pick_app(link.apps)
        if app:
            await send("open_app", True, package_name=app)
        await send("go_back", True)
    except Interrupted as stopped:
        if isinstance(stopped, PhoneGone):
            raise
        done = Done(outcome="stopped", summary="Smoke test stopped.")
        await link.finish(done)
        return done

    if answered:
        done = Done(outcome="completed", summary=f"Smoke test passed: the phone answered {answered} commands.")
    else:
        done = Done(outcome="failed", summary="Smoke test failed: the phone answered no commands.")
    print(done.summary)
    await link.finish(done)
    return done


def pick_app(apps: dict[str, str]) -> str | None:
    for package, label in apps.items():
        if label.strip().lower() == "doordash":
            return package
    return next(iter(apps), None)


def print_result(action: str, result: Result, apps: dict[str, str], show: bool) -> None:
    line = f"{action}: {result.status}"
    if result.error:
        line += f" ({result.error.code}: {result.error.message})"
    print(line)
    if show and result.screen:
        print(render(result.screen, apps).text)
        print()


def lan_ip() -> str:
    # Connecting a UDP socket picks the outgoing interface without sending any packet.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Jarvis backend.")
    parser.add_argument("--smoke", action="store_true", help="test the phone connection without an AI")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # show the addresses at once, even when output is piped
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    settings = Settings()
    routine: RunTask = run_smoke
    if args.smoke:
        print("Smoke test mode: no AI is used.")
    else:
        from jarvis.brain import brain_runner
        from jarvis.claude import ClaudeAI
        from jarvis.task import mark_interrupted_after_restart

        for task_id in mark_interrupted_after_restart(settings.data_dir):
            print(f"Task {task_id} was cut off by a restart; its last unanswered step is marked unknown.")
        ai = ClaudeAI(settings)
        routine = brain_runner(ai)
        print(f"AI: {ai.name} · effort {settings.effort}")
    ip, port = lan_ip(), settings.port
    print(f"Phone (Wi-Fi): ws://{ip}:{port}/agent")
    print(f"Phone (USB):   ws://127.0.0.1:{port}/agent  after: adb reverse tcp:{port} tcp:{port}")
    print(f"Check:         http://{ip}:{port}/health")
    uvicorn.run(
        create_app(settings, routine),
        host="0.0.0.0",
        port=port,
        ws_max_size=16 * 1024 * 1024,
        ws_ping_interval=20,
        access_log=False,
        log_level="info",
        log_config=None,
    )


if __name__ == "__main__":
    main()
