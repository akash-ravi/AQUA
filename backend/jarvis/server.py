import argparse
import asyncio
import logging
import socket
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import PlainTextResponse

from jarvis.config import Settings, load_settings
from jarvis.messages import Done, Result, new_id
from jarvis.phone import Interrupted, PhoneGone, PhoneLink, new_command
from jarvis.screen import render
from jarvis.task import is_task_id, list_tasks, task_story

log = logging.getLogger("jarvis.server")

PROTOCOL = 1
START_SECONDS = 10.0


@dataclass
class Run:
    device_id: str
    task_id: str
    goal: str
    link: PhoneLink
    settings: Settings
    job: asyncio.Task | None = None


RunTask = Callable[[Run], Awaitable[Done | None]]


def create_app(
    settings: Settings,
    *,
    smoke: bool = False,
    run_task: RunTask | None = None,
) -> FastAPI:
    app = FastAPI()
    runs: dict[str, Run] = {}
    app.state.runs = runs
    app.state.settings = settings
    routine = run_smoke if smoke else run_task

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        return "ok"

    @app.get("/status")
    async def status() -> dict:
        return {"ok": True, "protocol": PROTOCOL, "mode": "smoke" if smoke else "normal",
                "ai": None if smoke else ai_info(settings), "running": [live_task(run) for run in runs.values()]}

    @app.get("/tasks")
    async def tasks(device_id: str | None = None, limit: int = Query(50, ge=1, le=200)) -> dict:
        found = list_tasks(settings.data_dir, device_id, limit)
        for task in found:
            run = next((run for run in runs.values() if run.task_id == task["task_id"]), None)
            if run:
                task.update(live_task(run))
        return {"tasks": found}

    @app.get("/tasks/{task_id}")
    async def task(task_id: str) -> dict:
        story = task_story(settings.data_dir, task_id) if is_task_id(task_id) else None
        if story is None:
            raise HTTPException(404, "No task with that id.")
        run = next((run for run in runs.values() if run.task_id == task_id), None)
        return {**story, **live_task(run)} if run else story

    @app.post("/tasks/{task_id}/stop")
    async def stop(task_id: str, request: Request) -> dict:
        if "origin" in request.headers:
            raise HTTPException(403, "Requests from web pages are not allowed.")
        run = next((run for run in runs.values() if run.task_id == task_id), None)
        if run is None:
            raise HTTPException(404, "That task is not running.")
        run.link.request_stop()
        return {"stopping": True, "task_id": task_id}

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
            await handle_phone(link, settings, runs, routine, websocket.client.host if websocket.client else "")
        except PhoneGone:
            log.info("The phone disconnected.")
        finally:
            await link.close()

    return app


async def handle_phone(
    link: PhoneLink,
    settings: Settings,
    runs: dict[str, Run],
    routine: RunTask | None,
    device_id: str,
) -> None:
    start = await wait_for_message(link, ("start",), START_SECONDS)
    if start is None:
        return
    if routine is None:
        await link.fail("server_error", "the brain is not built yet")
        return

    run = Run(device_id, new_id("t"), start.task, link, settings)
    runs[run.task_id] = run
    log.info("New task %s from %s.", run.task_id, device_id)
    try:
        link.start(start)
        await link.load_apps()
        run.job = asyncio.create_task(routine(run))
        await run.job
    except PhoneGone:
        log.info("Task %s: the phone disconnected.", run.task_id)
    except Exception:
        log.exception("Task %s crashed.", run.task_id)
        await link.fail("server_error", "Something went wrong on the Mac.", task_id=run.task_id)
    finally:
        if run.job and not run.job.done():
            run.job.cancel()
        runs.pop(run.task_id, None)


async def wait_for_message(link: PhoneLink, wanted: tuple[str, ...], seconds: float):
    try:
        async with asyncio.timeout(seconds):
            while (message := await link.receive()) is not None:
                if message.type in wanted:
                    return message
                await link.reject("unexpected_message", f"expected {' or '.join(wanted)}, got {message.type}")
    except TimeoutError:
        log.info("The phone sent no %s in time.", wanted[0])
        await link.fail("timeout", f"No {' or '.join(wanted)} within {seconds:g} seconds.")
    return None


async def run_smoke(run: Run) -> Done:
    link = run.link
    answered = 0

    async def send(text: str, action: str, show: bool, **fields) -> None:
        nonlocal answered
        if action not in link.actions:
            print(f"Skipped {action}: the phone doesn't offer it.")
            return
        await link.progress("running", text, answered + 1)
        result = await link.run(new_command(action, **fields))
        if not (result.error and result.error.code == "no_result"):
            answered += 1
        print_result(action, result, link.apps, show)

    try:
        await send("Smoke test: reading the screen", "read_screen", True)
        await send("Smoke test: going home", "go_home", False)
        app = pick_app(link.apps)
        if app:
            await send(f"Smoke test: opening {link.apps[app]}", "open_app", True, package_name=app)
        await send("Smoke test: going back", "go_back", True)
    except Interrupted as stopped:
        if isinstance(stopped, PhoneGone):
            raise
        done = Done(task_id=run.task_id, outcome="stopped", summary="Smoke test stopped.")
        await link.finish(done)
        return done

    if answered:
        done = Done(task_id=run.task_id, outcome="completed", summary=f"Smoke test passed: the phone answered {answered} commands.")
    else:
        done = Done(task_id=run.task_id, outcome="failed", summary="Smoke test failed: the phone answered no commands.")
    print(done.summary)
    await link.finish(done)
    return done


def live_task(run: Run) -> dict:
    progress = run.link.last_progress
    return {
        "task_id": run.task_id,
        "device_id": run.device_id,
        "goal": run.goal,
        "status": progress.state if progress else "running",
        "now": progress.text if progress else "Starting",
        "step": progress.step if progress else 0,
    }


def ai_info(settings: Settings) -> dict:
    from jarvis.llm.common import check_route, sees_images

    route = check_route(settings)
    return {"provider": route.provider, "model": route.model, "free": route.free, "note": route.note,
            "sees_pictures": sees_images(settings)}


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


def route_line(settings: Settings, smoke: bool) -> str:
    if smoke:
        return "Smoke test mode: no AI is used."
    try:
        from jarvis.llm.common import check_route
    except ImportError:
        return f"AI: {settings.llm_provider} · {settings.llm_model}"
    route = check_route(settings)
    line = f"AI: {route.provider} · {route.model} · {'free' if route.free else 'paid'}"
    return f"{line} ({route.note})" if route.note else line


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

    settings = load_settings()
    run_task = None
    if not args.smoke:
        from jarvis.brain import brain_runner
        from jarvis.llm.common import SetupProblem, make_ai
        from jarvis.task import mark_interrupted_after_restart

        try:
            ai = make_ai(settings)
        except SetupProblem as problem:
            print(problem.message)
            raise SystemExit(1)
        for task_id in mark_interrupted_after_restart(settings.data_dir):
            print(f"Task {task_id} was cut off by a restart; its last unanswered step is marked unknown.")
        run_task = brain_runner(ai)
    ip, port = lan_ip(), settings.port
    print(route_line(settings, args.smoke))
    print(f"Phone (Wi-Fi): ws://{ip}:{port}/agent")
    print(f"Phone (USB):   ws://127.0.0.1:{port}/agent  after: adb reverse tcp:{port} tcp:{port}")
    print(f"Check:         http://{ip}:{port}/health")
    uvicorn.run(
        create_app(settings, smoke=args.smoke, run_task=run_task),
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
