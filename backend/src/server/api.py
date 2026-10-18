import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import PlainTextResponse

from src.agent.brain import Brain
from src.agent.prompt import SYSTEM_PROMPT
from src.agent.task import Notebook, Task
from src.ai.elevenlabs import TOKEN_TYPES, ElevenLabs
from src.ai.model import AI
from src.config import Settings
from src.phone.link import PhoneDisconnected, PhoneLink
from src.phone.messages import Done, new_id

log = logging.getLogger("jarvis.server")

START_SECONDS = 10.0
FOLLOW_UP_SECONDS = 1800
MAX_HISTORY = 20


def create_app(settings: Settings, ai: AI) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        warmer = asyncio.create_task(ai.keep_warm(SYSTEM_PROMPT))
        yield
        warmer.cancel()

    app = FastAPI(lifespan=lifespan)
    key = settings.elevenlabs_api_key.get_secret_value() if settings.elevenlabs_api_key else ""
    elevenlabs = ElevenLabs(key) if key else None

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        return "ok"

    @app.post("/voice/{kind}")
    async def voice_token(kind: str, request: Request) -> dict:
        # Same rule as /agent: a web page must not mint tokens on the Mac's account.
        if "origin" in request.headers:
            raise HTTPException(403)
        if kind not in TOKEN_TYPES:
            raise HTTPException(404)
        if elevenlabs is None:
            raise HTTPException(503, "ELEVENLABS_API_KEY is not set in backend/.env")
        token = await elevenlabs.token(kind)
        if token is None:
            raise HTTPException(502, "ElevenLabs refused the token request")
        return {"token": token, "voice_id": settings.voice_id} if kind == "speak" else {"token": token}

    # shortcut: in memory and keyed by phone IP, so a restart or a new IP forgets follow-up context.
    histories: defaultdict[str, deque[tuple[float, str]]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

    @app.websocket("/agent")
    async def agent(websocket: WebSocket) -> None:
        # Browsers always send Origin; the phone app never does, so this keeps web pages out.
        if "origin" in websocket.headers:
            log.info("Refused a connection with an Origin header.")
            await websocket.close(code=1008)
            return
        await websocket.accept()
        link = PhoneLink(websocket)
        device_id = websocket.client.host if websocket.client else ""
        try:
            await handle_phone(link, settings, ai, device_id, histories[device_id])
        except PhoneDisconnected:
            log.info("The phone disconnected.")
        finally:
            await link.close()

    return app


async def handle_phone(link: PhoneLink, settings: Settings, ai: AI, device_id: str,
                       history: deque[tuple[float, str]]) -> None:
    start = await wait_for_start(link)
    if start is None:
        return

    log.info("New request from %s: %s", device_id, start.task)
    try:
        link.start(start)
        parts, _ = await asyncio.gather(ai.split_tasks(start.task), link.load_apps())
        await run_chain(parts, link, settings, ai, device_id, history)
    except PhoneDisconnected:
        log.info("The phone disconnected.")
    except Exception:
        log.exception("The request crashed: %s", start.task)
        await link.fail("Something went wrong on the Mac.")


async def run_chain(parts: list[str], link: PhoneLink, settings: Settings, ai: AI, device_id: str,
                    history: deque[tuple[float, str]]) -> None:
    """Runs each part in order and stops the chain at the first part that doesn't complete."""
    summaries = []
    for n, goal in enumerate(parts):
        while history and time.monotonic() - history[0][0] > FOLLOW_UP_SECONDS:
            history.popleft()
        task = Task(goal=goal, id=new_id("t"), earlier=[line for _, line in history])
        log.info("Task %s (%d of %d): %s", task.id, n + 1, len(parts), goal)
        notebook = Notebook(settings.data_dir, task.id)
        notebook.write("task", task_id=task.id, goal=goal, chain=parts, device_id=device_id, ai=ai.name)
        if n:
            await link.progress("running", f"Next: {goal}", 0)
        end = await Brain(task, link, ai, notebook).run()
        if end is None:
            return
        said = "; ".join(f"{question} → {answer}" for question, answer in task.answers)
        if end.summary:  # an empty summary means the mic only heard noise
            history.append((time.monotonic(), f"{goal}{f' (user said: {said})' if said else ''} → {end.outcome}: {end.summary}"))
        summaries.append(end.summary)
        if end.outcome != "completed":
            break
    skipped = parts[n + 1:]
    summary = " ".join(summaries) + (f" Didn't get to: {'; '.join(skipped)}." if skipped else "")
    await link.finish(Done(outcome=end.outcome, summary=summary[:600]))


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
