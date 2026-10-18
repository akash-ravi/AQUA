import base64
import logging
import time

import anthropic

from jarvis.llm.common import (AIError, AIUnavailable, AuthProblem, BadReply, Decision, QuotaUsedUp, RateLimited,
                               SetupProblem, image_type)
from jarvis.llm.tools import Tool, claude_tools, clean, read_reply

log = logging.getLogger("jarvis.llm")


class ClaudeAI:
    def __init__(self, model: str = "claude-opus-5-5", api_key: str | None = None, effort: str = "medium",
                 sees_images: bool = True, client=None):
        self.model = model
        self.effort = effort
        self.name = f"Claude {model}"
        self.sees_images = sees_images
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=120)

    async def decide(self, system: str, message: str, tools: list[Tool], *, image: bytes | None = None,
                     retry_note: str | None = None) -> Decision:
        blocks: list[dict] = []
        if image:
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": image_type(image), "data": base64.b64encode(image).decode()}})
        blocks.append({"type": "text", "text": f"{message}\n{retry_note}" if retry_note else message})
        started = time.monotonic()
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": blocks}],
                tools=claude_tools(tools),
                # forced tool_choice returns 400 on Opus 5.5
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIError as error:
            raise _plain_error(error)
        ms = int((time.monotonic() - started) * 1000)

        if response.model != self.model:
            log.info("Claude answered with %s (fallback)", response.model)
        if response.stop_reason == "refusal":
            raise BadReply("the model declined this request")
        text = "".join(block.text for block in response.content if block.type == "text")
        call = next((block for block in response.content if block.type == "tool_use"), None)
        if call is not None:
            tool, args = call.name, call.input
        elif response.stop_reason == "max_tokens":
            raise BadReply("the reply was cut off")
        else:
            try:
                tool, args = read_reply(text=text)
            except BadReply:
                raise BadReply("no action in the reply")
        tool, args = clean(tool, args, tools)
        usage = response.usage
        return Decision(tool=tool, args=args, text=text, model=response.model, ms=ms, usage={
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        })


def _plain_error(error: anthropic.APIError) -> AIError:
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return AuthProblem("The Claude key was refused. Check LLM_API_KEY or ANTHROPIC_API_KEY.")
    if isinstance(error, anthropic.NotFoundError):
        return SetupProblem("Claude has no such model. Check LLM_MODEL.")
    if isinstance(error, anthropic.RateLimitError):
        return RateLimited("Claude is busy (rate limited).", _retry_after(error))
    if isinstance(error, anthropic.BadRequestError):
        if "credit balance" in error.message.lower():
            return QuotaUsedUp("The Claude account is out of credits.")
        return SetupProblem(f"Claude refused the request: {error.message[:300]}")
    if isinstance(error, anthropic.APIStatusError):
        if error.status_code >= 500:
            return AIUnavailable(f"Claude is having trouble (HTTP {error.status_code}).")
        return SetupProblem(f"Claude refused the request: {error.message[:300]}")
    if isinstance(error, anthropic.APITimeoutError):
        return AIUnavailable("Claude did not answer in time.")
    if isinstance(error, anthropic.APIConnectionError):
        return AIUnavailable("Could not reach Claude.")
    return AIUnavailable(f"Claude failed: {error.message[:200]}")


def _retry_after(error: anthropic.APIStatusError) -> float | None:
    try:
        return float(error.response.headers.get("retry-after"))
    except (TypeError, ValueError):
        return None
