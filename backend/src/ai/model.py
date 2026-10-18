import asyncio
import base64
import logging
import re
import time
from dataclasses import dataclass

import anthropic

from src.config import Settings
from src.ai.jev import Jev
from src.ai.tools import TOOLS, tool_schemas

log = logging.getLogger("jarvis.llm")

WARM_EVERY_SECONDS = 270  # just under the 5-minute cache TTL

APPROVAL_PROMPT = ("The user was asked to approve an action that can't be undone. Their spoken reply may be "
                   "misheard. Answer APPROVE only if the reply clearly says yes to exactly this action, with no "
                   "changes or conditions. Otherwise answer NO. Answer with one word.")
CHAT_PROMPT = ("You are Aqua, a voice assistant that can also operate the user's Android phone. The user said "
               "something that isn't a phone task; it came from speech and may be misheard. Be super chill, like a "
               "laid-back friend texting back: lowercase vibe, a few words, slang is fine, never formal, never "
               "eager, no lists or markdown. \"yo what's up\" not \"Hello! How can I help you today?\". "
               "If it's about an earlier task (\"how much was it?\"), answer from that task's result. "
               "If it makes no sense, just go \"huh? say that again\". Dry, playful humour is very welcome. "
               "If the user is wrapping up (thanks, bye, all set, go away, shut down) or the chat has clearly run "
               "its course, give a short funny sign-off and end with [END]; that shuts you down until they call again.")
END_MARK = "[END]"
SPLIT_PROMPT = ("Split the user's spoken phone request into the separate tasks it asks for, in order. One goal stays "
                "one task: \"order a pizza and fries\" is one task; \"text Sam I'm late, then set a 7am alarm\" is two. "
                "Keep the user's wording and details. Reply with one task per line and nothing else.")
MAX_CHAIN_PARTS = 5
CHAIN_WORDS = re.compile(r"\b(and|then|also|after|afterwards|plus)\b|[,;]", re.IGNORECASE)
LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


@dataclass
class Decision:
    tool: str
    args: dict
    text: str = ""
    model: str = ""
    ms: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0


class AIError(Exception):
    """A problem retrying won't fix (bad key, unknown model, no credits)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RateLimited(AIError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class AIUnavailable(AIError):
    pass


class InvalidReply(AIError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class AI:
    def __init__(self, settings: Settings):
        key = settings.anthropic_api_key
        self.model = settings.model
        self.effort = settings.effort
        self.name = self.model
        self.tools = tool_schemas()
        self.client = anthropic.AsyncAnthropic(api_key=key.get_secret_value() if key else None, max_retries=0,
                                               timeout=120)
        jev_key = settings.typesafe_api_key
        self.jev = Jev(jev_key.get_secret_value()) if jev_key else None

    async def speech_kind(self, said: str) -> str:
        return await self.jev.speech_kind(said) if self.jev else "talking"

    async def phone_task_likelihood(self, goal: str) -> float:
        return await self.jev.phone_task_likelihood(goal) if self.jev else 1.0

    async def pause_reply(self, question: str, reply: str) -> str | None:
        return await self.jev.pause_reply(question, reply) if self.jev else None

    async def says_done(self, question: str, reply: str) -> bool | None:
        return await self.jev.says_done(question, reply) if self.jev else None

    async def decide(self, system: str, message: str, *, image: bytes | None = None,
                     retry_note: str | None = None) -> Decision:
        blocks: list[dict] = []
        if image:
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(image).decode()}})
        blocks.append({"type": "text", "text": f"{message}\n{retry_note}" if retry_note else message})
        started = time.monotonic()
        try:
            response = await self.decide_request(system, blocks, max_tokens=16000)
        except anthropic.APIError as error:
            raise _to_ai_error(error)
        ms = int((time.monotonic() - started) * 1000)

        usage = response.usage
        cached = usage.cache_read_input_tokens or 0
        input_tokens = usage.input_tokens + (usage.cache_creation_input_tokens or 0) + cached
        log.debug("%s answered in %d ms: %d input tokens (%d cached), %d output tokens%s", response.model, ms,
                  input_tokens, cached, usage.output_tokens, ", with a picture" if image else "")
        if response.model != self.model:
            log.info("Answered by fallback model %s", response.model)
        if response.stop_reason == "refusal":
            raise InvalidReply("the model declined this request")
        text = "".join(block.text for block in response.content if block.type == "text")
        call = next((block for block in response.content if block.type == "tool_use"), None)
        if call is None:
            raise InvalidReply("the reply was cut off" if response.stop_reason == "max_tokens" else "no action in the reply")
        tool, args = normalize_tool_call(call.name, call.input)
        return Decision(tool=tool, args=args, text=text, model=response.model, ms=ms, input_tokens=input_tokens,
                        cached_tokens=cached, output_tokens=usage.output_tokens)

    def decide_request(self, system: str, content: list[dict], *, max_tokens: int):
        # The warmup must send exactly these settings, or it writes a cache entry decide() never reads.
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            tools=self.tools,
            # forced tool_choice returns 400 on Opus 5.5
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    async def keep_warm(self, system: str) -> None:
        while True:
            try:
                response = await self.decide_request(system, [{"type": "text", "text": "warmup"}], max_tokens=0)
                log.debug("Cache warm: %d written, %d read", response.usage.cache_creation_input_tokens or 0,
                          response.usage.cache_read_input_tokens or 0)
            except anthropic.APIError as error:
                log.warning("Cache warmup failed: %s", error)
            await asyncio.sleep(WARM_EVERY_SECONDS)

    async def approves(self, question: str, reply: str) -> bool:
        if self.jev:
            return await self.jev.approves(question, reply)
        text = await self.quick_text(APPROVAL_PROMPT, f"Question: {question}\nReply: {reply}")
        return text.upper().startswith("APPROVE")

    async def chat(self, said: str) -> tuple[str, bool]:
        """The reply, and whether Claude judged the conversation over."""
        text = await self.quick_text(CHAT_PROMPT, said)
        return text.replace(END_MARK, "").strip(), END_MARK in text

    async def split_tasks(self, request: str) -> list[str]:
        """The request's tasks in order; any doubt or error keeps it whole."""
        if not CHAIN_WORDS.search(request):
            return [request]
        try:
            text = await self.quick_text(SPLIT_PROMPT, request)
        except AIError as error:
            log.info("Could not split the request: %s", error.message)
            return [request]
        parts = [LIST_MARKER.sub("", line).strip() for line in text.splitlines()]
        parts = [part for part in parts if part]
        return parts if 0 < len(parts) <= MAX_CHAIN_PARTS else [request]

    async def quick_text(self, system: str, content: str) -> str:
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=2000,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIError as error:
            raise _to_ai_error(error)
        return "".join(block.text for block in response.content if block.type == "text").strip()


def normalize_tool_call(tool: str, args: dict) -> tuple[str, dict]:
    """Strict tool use guarantees names and types; this drops empty values and fills in a missing "why"."""
    spec = next((t for t in TOOLS if t.name == tool), None)
    if spec is None:
        raise InvalidReply(f"unknown action '{tool}'")
    cleaned: dict = {}
    for param in spec.params:
        value = args.get(param.name)
        if isinstance(value, str):
            value = value.strip()
        elif isinstance(value, list):
            value = [item.strip() for item in value if item.strip()]
        if value is None or value == "" or value == []:
            if param.required and param.name != "why":
                raise InvalidReply(f"{tool} needs {param.name}")
            continue
        cleaned[param.name] = value
    if tool == "wait":
        cleaned["seconds"] = min(10, max(1, cleaned.get("seconds", 3)))
    if any(p.name == "why" for p in spec.params) and "why" not in cleaned:
        cleaned["why"] = _default_why(tool, cleaned)
    return tool, cleaned


def _default_why(tool: str, args: dict) -> str:
    label = args.get("target_text") or (f"[{args['target']}]" if "target" in args else "")
    return {
        "tap": f"Tapping '{label}'",
        "confirm_and_tap": f"Asking before tapping '{label}'",
        "type_text": f"Typing '{args.get('text', '')}'",
        "scroll": f"Scrolling {args.get('direction', 'down')}",
        "go_back": "Going back",
        "go_home": "Going to the home screen",
        "open_app": f"Opening {args.get('name', 'the app')}",
        "wait": "Waiting for the screen",
        "look": "Looking at the screen",
    }.get(tool, tool)


def _to_ai_error(error: anthropic.APIError) -> AIError:
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return AIError("The AI key was refused. Check ANTHROPIC_API_KEY.")
    if isinstance(error, anthropic.NotFoundError):
        return AIError("No such model. Check JARVIS_MODEL.")
    if isinstance(error, anthropic.RateLimitError):
        return RateLimited("The AI is busy (rate limited).", _retry_after(error))
    if isinstance(error, anthropic.BadRequestError):
        if "credit balance" in error.message.lower():
            return AIError("The AI account is out of credits.")
        return AIError(f"The AI refused the request: {error.message[:300]}")
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code >= 500:
            return AIUnavailable(f"The AI is having trouble (HTTP {error.status_code}).")
        return AIError(f"The AI refused the request: {error.message[:300]}")
    if isinstance(error, anthropic.APITimeoutError):
        return AIUnavailable("The AI did not answer in time.")
    if isinstance(error, anthropic.APIConnectionError):
        return AIUnavailable("Could not reach the AI.")
    return AIUnavailable(f"The AI failed: {error.message[:200]}")


def _retry_after(error: anthropic.APIStatusError) -> float | None:
    try:
        return float(error.response.headers.get("retry-after"))
    except (TypeError, ValueError):
        return None
