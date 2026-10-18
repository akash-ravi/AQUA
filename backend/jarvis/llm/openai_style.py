import asyncio
import base64
import email.utils
import json
import logging
import re
import time
from urllib.parse import urlparse

import httpx

from jarvis.config import LOCAL_HOSTS
from jarvis.llm.common import (AIError, AIUnavailable, AuthProblem, BadReply, Decision, QuotaUsedUp, RateLimited,
                               SetupProblem, image_type)
from jarvis.llm.tools import Tool, clean, json_menu, openai_tools, read_reply

log = logging.getLogger("jarvis.llm")

MAX_TOKENS_CAP = 16384
DAILY_WORDS = ("daily", "per day", "quota", "credit", "limit reached for")
ZAI_QUOTA = {"1113", "1308", "1310"}


class ToolsRejected(SetupProblem):
    pass


def host_kind(host: str) -> str:
    if host == "integrate.api.nvidia.com":
        return "nvidia"
    if host == "openrouter.ai":
        return "openrouter"
    if host == "api.z.ai":
        return "zai"
    if host == "api.groq.com":
        return "groq"
    if host in LOCAL_HOSTS:
        return "local"
    return "other"


class OpenAIStyleAI:
    poll_seconds = 1.0

    def __init__(self, base_url: str, model: str, api_key: str | None, sees_images: bool, timeout: float = 90,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.host = (urlparse(base_url).hostname or "").lower()
        self.kind = host_kind(self.host)
        self.name = f"{model} ({self.host})"
        self.sees_images = sees_images
        self.timeout = timeout
        self.json_mode = False
        self.rate_limits: dict[str, str] = {}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers)

    async def close(self) -> None:
        await self.client.aclose()

    async def decide(self, system: str, message: str, tools: list[Tool], *, image: bytes | None = None,
                     retry_note: str | None = None) -> Decision:
        started = time.monotonic()
        text = f"{message}\n{retry_note}" if retry_note else message
        max_tokens = 4096 if self.kind in ("local", "other") else 8192
        retried_cut_off = False
        while True:
            body = self._body(system, text, tools, image, max_tokens)
            try:
                data = await self._post(body, started)
            except ToolsRejected:
                if self.json_mode:
                    raise
                log.info("%s refused the tool list; switching to JSON replies", self.name)
                self.json_mode = True
                continue
            choice = (data.get("choices") or [{}])[0]
            reply = choice.get("message") or {}
            content = _content_text(reply.get("content"))
            try:
                tool, args = self._read(reply, content)
            except BadReply:
                if choice.get("finish_reason") != "length":
                    raise
                if retried_cut_off or max_tokens >= MAX_TOKENS_CAP:
                    raise BadReply("the reply was cut off")
                retried_cut_off = True
                max_tokens = min(max_tokens * 2, MAX_TOKENS_CAP)
                continue
            tool, args = clean(tool, args, tools)
            return Decision(tool=tool, args=args, text=content, model=data.get("model") or self.model,
                            ms=int((time.monotonic() - started) * 1000), usage=data.get("usage") or {})

    def _read(self, reply: dict, content: str) -> tuple[str, dict]:
        calls = reply.get("tool_calls") or []
        if calls:
            function = calls[0].get("function") or {}
            return read_reply(tool_name=function.get("name"), arguments=function.get("arguments"), text=content)
        return read_reply(text=content)

    def _body(self, system: str, text: str, tools: list[Tool], image: bytes | None, max_tokens: int) -> dict:
        if self.json_mode:
            system = f"{system}\n\n{json_menu(tools)}"
        user: str | list = text
        if image:
            url = f"data:{image_type(image)};base64,{base64.b64encode(image).decode()}"
            user = [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": url}}]
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
        }
        if not self.json_mode:
            body["tools"] = openai_tools(tools)
            if self.kind != "local":
                body["tool_choice"] = "auto"
        one_call = False
        if self.kind == "nvidia":
            body["max_tokens"] = max_tokens
            body["temperature"] = 0.3
            # NVIDIA accepts only low, high or max here
            if self.model.startswith(("z-ai/glm", "nvidia/nemotron")):
                body["reasoning_effort"] = "low"
            one_call = self.model.startswith("nvidia/nemotron")
        elif self.kind == "openrouter":
            body["max_tokens"] = max_tokens
            body["reasoning"] = {"effort": "low"}
            one_call = True
        elif self.kind == "groq":
            body["max_completion_tokens"] = max_tokens
            # Groq's gpt-oss models refuse reasoning_format
            if "gpt-oss" in self.model:
                body["reasoning_effort"] = "low"
            else:
                body["reasoning_format"] = "parsed"
            one_call = True
        else:
            body["max_tokens"] = max_tokens
        if one_call and not self.json_mode:
            body["parallel_tool_calls"] = False
        return body

    async def _post(self, body: dict, started: float) -> dict:
        try:
            response = await self.client.post(f"{self.base_url}/chat/completions", json=body)
            while response.status_code == 202:
                request_id = response.headers.get("NVCF-REQID")
                if not request_id:
                    raise AIUnavailable("The AI service said it was still working but gave no request id.")
                if time.monotonic() - started > self.timeout:
                    raise AIUnavailable("The AI did not answer in time.")
                await asyncio.sleep(self.poll_seconds)
                response = await self.client.get(f"{self.base_url}/status/{request_id}")
        except httpx.TimeoutException:
            raise AIUnavailable("The AI did not answer in time.")
        except httpx.TransportError:
            raise AIUnavailable(f"Could not reach the AI service at {self.host}.")
        self.rate_limits = {k: v for k, v in response.headers.items()
                            if k.lower().startswith(("x-ratelimit", "ratelimit", "retry-after"))}
        if response.status_code >= 400:
            raise self._error(response.status_code, response.text, response.headers)
        try:
            data = response.json()
        except json.JSONDecodeError:
            raise AIUnavailable("The AI service sent a reply that isn't JSON.")
        if not isinstance(data, dict):
            raise AIUnavailable("The AI service sent a reply in an unexpected shape.")
        if data.get("error") and not data.get("choices"):
            error = data["error"] if isinstance(data["error"], dict) else {"message": str(data["error"])}
            code = error.get("code")
            status = code if isinstance(code, int) and 400 <= code < 600 else 502
            raise self._error(status, json.dumps(data), response.headers)
        return data

    def _error(self, status: int, body: str, headers: httpx.Headers) -> AIError:
        message, code = _error_details(body)
        if self.kind == "zai" and code:
            if code in ZAI_QUOTA:
                return QuotaUsedUp("The Z.ai free quota is used up for now. Try later or pick another AI.")
            if code == "1302":
                return RateLimited("The AI is busy (too many requests).", _retry_after(headers))
            if code == "1305":
                return AIUnavailable("The Z.ai model is overloaded right now.")
            if code == "1211":
                return SetupProblem(f"Z.ai has no model named {self.model}. Check LLM_MODEL.")
            if code == "1301":
                return BadReply("blocked by the provider's content filter")
        if status in (401, 403):
            return AuthProblem("The AI key was refused. Check LLM_API_KEY.")
        if status == 404:
            return SetupProblem(f"The AI service has no model {self.model} at {self.base_url}. "
                                "Check LLM_MODEL and LLM_BASE_URL.")
        if status == 402:
            return QuotaUsedUp("The AI account is out of credits or quota.")
        if status == 429:
            if any(word in message.lower() for word in DAILY_WORDS):
                return QuotaUsedUp("The AI's daily limit or quota is used up. Try later or pick another free AI.")
            return RateLimited("The AI is busy (too many requests).", _retry_after(headers))
        if status >= 500:
            return AIUnavailable(f"The AI service is having trouble (HTTP {status}).")
        text = f"The AI service refused the request: {message[:300]}"
        if status in (400, 422) and re.search(r"tool|function", message, re.I):
            return ToolsRejected(text)
        return SetupProblem(text)


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _error_details(body: str) -> tuple[str, str]:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return (body or "").strip()[:500], ""
    error = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or data.get("message") or json.dumps(data)
        code = error.get("code")
        return str(message), "" if code is None else str(code)
    return str(error), ""


def _retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())
