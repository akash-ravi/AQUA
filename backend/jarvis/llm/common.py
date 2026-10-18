from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from jarvis.config import LOCAL_HOSTS, Settings

if TYPE_CHECKING:
    from jarvis.llm.tools import Tool


@dataclass
class Decision:
    tool: str
    args: dict
    text: str = ""
    model: str = ""
    ms: int = 0
    usage: dict = field(default_factory=dict)


class AIError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RateLimited(AIError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class QuotaUsedUp(AIError):
    pass


class AuthProblem(AIError):
    pass


class AIUnavailable(AIError):
    pass


class BadReply(AIError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SetupProblem(AIError):
    pass


class AI(Protocol):
    name: str
    sees_images: bool

    async def decide(self, system: str, message: str, tools: list[Tool], *, image: bytes | None = None,
                     retry_note: str | None = None) -> Decision: ...


@dataclass
class Route:
    provider: str
    host: str
    model: str
    free: bool
    note: str


ZAI_FREE_MODELS = {"glm-4.6v-flash", "glm-4.7-flash", "glm-4.5-flash"}
IMAGE_MODELS = ("glm-5.3-flash", "glm-4.6v", "nemotron-3.5-super-vl", "gemma-4", "gemma4", "qwen3.8-27b",
                "qwen3.5", "llama-3.2-90b-vision", "llama-4", "-vl", "vision")


def check_route(settings: Settings) -> Route:
    provider = settings.llm_provider
    model = settings.llm_model
    host = settings.llm_host
    if provider == "claude":
        return Route(provider, "api.anthropic.com", model, False, "Claude is a paid API")
    if host == "integrate.api.nvidia.com":
        return Route(provider, host, model, True, "NVIDIA free trial — no personal or card data under its terms")
    if host == "openrouter.ai":
        if model.endswith(":free"):
            return Route(provider, host, model, True, "OpenRouter free model")
        return Route(provider, host, model, False, "OpenRouter models without ':free' are paid")
    if host == "api.z.ai":
        if model.lower() in ZAI_FREE_MODELS:
            return Route(provider, host, model, True, "Z.ai free model")
        return Route(provider, host, model, False, f"only {', '.join(sorted(ZAI_FREE_MODELS))} are free on Z.ai")
    if host == "api.groq.com":
        return Route(provider, host, model, True, "free only on Groq's free plan")
    if host in LOCAL_HOSTS:
        return Route(provider, host, model, True, "runs on this computer")
    return Route(provider, host, model, False, f"{host or 'this address'} is not a known free service")


def sees_images(settings: Settings) -> bool:
    if settings.llm_images != "auto":
        return settings.llm_images == "yes"
    if settings.llm_provider == "claude":
        return True
    model = settings.llm_model.lower()
    return any(part in model for part in IMAGE_MODELS)


def image_type(data: bytes) -> str:
    return "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"


def make_ai(settings: Settings) -> AI:
    route = check_route(settings)
    if not route.free and not settings.allow_paid_models:
        raise SetupProblem(
            f"{route.model} ({route.provider}) costs money: {route.note}. "
            "Pick a free AI in backend/.env, or set ALLOW_PAID_MODELS=true to allow paid ones."
        )
    images = sees_images(settings)
    if settings.llm_provider == "claude":
        from jarvis.llm.claude import ClaudeAI
        return ClaudeAI(settings.llm_model, settings.api_key(), settings.llm_effort, sees_images=images)
    from jarvis.llm.openai_style import OpenAIStyleAI
    key = settings.api_key()
    if not key:
        raise SetupProblem(f"No AI key for {route.host}. Put it in LLM_API_KEY in backend/.env.")
    return OpenAIStyleAI(settings.llm_base_url, settings.llm_model, key, images)
