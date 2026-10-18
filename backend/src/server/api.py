import asyncio
import logging
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
from src.phone.messages import new_id

log = logging.getLogger("jarvis.server")

START_SECONDS = 10.0


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
            await handle_phone(link, settings, ai, websocket.client.host if websocket.client else "")
        except PhoneDisconnected:
            log.info("The phone disconnected.")
        finally:
            await link.close()

    return app


async def handle_phone(link: PhoneLink, settings: Settings, ai: AI, device_id: str) -> None:
    start = await wait_for_start(link)
    if start is None:
        return

    task = Task(goal=start.task, id=new_id("t"))
    log.info("New task %s from %s.", task.id, device_id)
    try:
        link.start(start)
        await link.load_apps()
        notebook = Notebook(settings.data_dir, task.id)
        notebook.write("task", task_id=task.id, goal=task.goal, device_id=device_id, ai=ai.name)
        await Brain(task, link, ai, notebook).run()
    except PhoneDisconnected:
        log.info("Task %s: the phone disconnected.", task.id)
    except Exception:
        log.exception("Task %s crashed.", task.id)
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
