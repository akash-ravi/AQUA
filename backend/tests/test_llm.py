import json
import logging
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest

from jarvis.config import Settings
from jarvis.llm.claude import ClaudeAI
from jarvis.llm.common import (AIUnavailable, AuthProblem, BadReply, QuotaUsedUp, RateLimited, SetupProblem,
                               check_route, make_ai, sees_images)
from jarvis.llm.openai_style import OpenAIStyleAI
from jarvis.llm.tools import TOOLS, claude_tools, clean, json_menu, openai_tools, read_reply, tools_for
from jarvis.messages import ACTIONS

ALL = list(TOOLS)
PHONE = set(ACTIONS)


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


# ---- read_reply ----

def test_read_reply_native_call_with_string_or_dict_arguments():
    assert read_reply(tool_name="tap", arguments='{"target": 5}') == ("tap", {"target": 5})
    assert read_reply(tool_name="tap", arguments={"target": 5}) == ("tap", {"target": 5})
    assert read_reply(tool_name="go_back", arguments="") == ("go_back", {})
    with pytest.raises(BadReply, match="not valid JSON"):
        read_reply(tool_name="tap", arguments="{target: 5")


def test_read_reply_fenced_json_after_thinking():
    text = '<think>maybe {"tool": "go_back"}</think>\nSure.\n```json\n{"tool": "tap", "args": {"target": 8}}\n```'
    assert read_reply(text=text) == ("tap", {"target": 8})


def test_read_reply_name_arguments_and_flat_shapes():
    assert read_reply(text='{"name": "open_app", "arguments": "{\\"name\\": \\"DoorDash\\"}"}') == \
        ("open_app", {"name": "DoorDash"})
    assert read_reply(text='I will tap. {"tool": "tap", "target": 12, "why": "x"}') == \
        ("tap", {"target": 12, "why": "x"})
    assert read_reply(text='{"note": 1} then {"tool": "wait"}') == ("wait", {})


def test_read_reply_glm_tags():
    text = ("<tool_call>finish<arg_key>summary</arg_key><arg_value>Ordered</arg_value>"
            '<arg_key>proof</arg_key><arg_value>["Order placed!"]</arg_value></tool_call>')
    assert read_reply(text=text) == ("finish", {"summary": "Ordered", "proof": ["Order placed!"]})


def test_read_reply_nothing_usable():
    with pytest.raises(BadReply, match="no action found in the reply"):
        read_reply(text="I think the pizza looks great.")


# ---- clean ----

def test_clean_aliases_and_values():
    assert clean("click", {"target": "[12]", "target_text": "Large"}, ALL) == \
        ("tap", {"target": 12, "target_text": "Large", "why": "Tapping 'Large'"})
    assert clean("Type", {"why": "w", "target": "#3", "target_text": "box", "text": "pizza", "submit": "true"}, ALL)[1] == \
        {"why": "w", "target": 3, "target_text": "box", "text": "pizza", "submit": True}
    assert clean("tap", {"why": "w", "target": 12.0, "target_text": "x"}, ALL)[1]["target"] == 12
    assert clean("swipe", {"direction": "downward"}, ALL)[1] == {"direction": "down", "why": "Scrolling down"}
    assert clean("scroll", {"why": "w", "direction": "previous", "target": None}, ALL)[1] == {"why": "w", "direction": "up"}
    assert clean("done", {"summary": "ok", "proof": "Order placed!"}, ALL)[1]["proof"] == ["Order placed!"]
    assert clean("ask", {"question": "Which?", "choices": "Large"}, ALL)[1]["choices"] == ["Large"]
    assert clean("abort", {"reason": "no store"}, ALL) == ("give_up", {"reason": "no store"})
    assert clean("back", {}, ALL) == ("go_back", {"why": "Going back"})
    assert clean("launch_app", {"app": "DoorDash"}, ALL) == ("open_app", {"name": "DoorDash", "why": "Opening DoorDash"})


def test_clean_wait_seconds():
    assert clean("wait", {}, ALL)[1]["seconds"] == 3
    assert clean("wait", {"seconds": 60}, ALL)[1]["seconds"] == 10
    assert clean("wait", {"seconds": "0"}, ALL)[1]["seconds"] == 1
    assert clean("wait", {"seconds": 2.6}, ALL)[1]["seconds"] == 3


def test_clean_problems():
    with pytest.raises(BadReply, match="unknown action 'fly'; use one of tap, "):
        clean("fly", {}, ALL)
    with pytest.raises(BadReply, match="unknown action 'look'"):
        clean("look", {"why": "x"}, tools_for(PHONE, images=False))
    with pytest.raises(BadReply, match="tap needs target"):
        clean("tap", {"why": "x", "target_text": "Large"}, ALL)
    with pytest.raises(BadReply, match="tap target must be a whole number"):
        clean("tap", {"why": "x", "target": "Large", "target_text": "Large"}, ALL)
    with pytest.raises(BadReply, match="must be true or false"):
        clean("type_text", {"why": "x", "target": 1, "target_text": "b", "text": "a", "submit": "maybe"}, ALL)
    with pytest.raises(BadReply, match="direction must be one of down, up, left, right"):
        clean("scroll", {"why": "x", "direction": "sideways"}, ALL)
    with pytest.raises(BadReply, match="give_up needs reason"):
        clean("give_up", {}, ALL)


# ---- tool menu and schemas ----

def test_tools_for_filters_by_phone_actions():
    names = [t.name for t in tools_for({"read_screen", "tap"}, images=True)]
    assert names == ["tap", "wait", "ask_user", "confirm_and_tap", "finish", "give_up"]
    assert "look" in [t.name for t in tools_for(PHONE, images=True)]
    assert "look" not in [t.name for t in tools_for(PHONE - {"screenshot"}, images=True)]
    assert [t.name for t in TOOLS] == ["tap", "type_text", "scroll", "go_back", "go_home", "open_app", "wait", "look",
                                       "ask_user", "confirm_and_tap", "finish", "give_up"]


def walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def test_openai_schema():
    tap = openai_tools(ALL)[0]
    assert tap["type"] == "function"
    params = tap["function"]["parameters"]
    assert params["required"] == ["why", "target", "target_text"]
    assert params["additionalProperties"] is False
    assert list(params["properties"]) == ["why", "target", "target_text", "remember"]


def test_claude_schema_is_strict():
    rendered = claude_tools(ALL)
    for tool in rendered:
        schema = tool["input_schema"]
        assert tool["strict"] is True
        assert schema["required"] == list(schema["properties"])
        for part in walk(schema):
            if isinstance(part, dict) and part.get("type") == "object":
                assert part["additionalProperties"] is False
            if isinstance(part, dict):
                assert not {"minimum", "maximum", "minLength", "maxLength", "maxItems", "minItems"} & set(part)
    scroll = next(t for t in rendered if t["name"] == "scroll")["input_schema"]["properties"]
    assert scroll["target"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert scroll["direction"]["enum"] == ["down", "up", "left", "right"]


def test_json_menu():
    menu = json_menu(tools_for({"tap"}, images=False))
    assert "- tap {why (text), target (number), target_text (text), remember? (text)}" in menu
    assert menu.endswith('Reply with only one JSON object: {"tool": "...", "args": {...}}')


# ---- check_route / make_ai ----

@pytest.mark.parametrize("values, free", [
    ({}, True),
    ({"llm_base_url": "https://openrouter.ai/api/v1", "llm_model": "nvidia/nemotron-3.5-super-vl:free"}, True),
    ({"llm_base_url": "https://openrouter.ai/api/v1", "llm_model": "nvidia/nemotron-3.5-super-vl"}, False),
    ({"llm_base_url": "https://api.z.ai/api/paas/v4", "llm_model": "glm-4.6v-flash"}, True),
    ({"llm_base_url": "https://api.z.ai/api/paas/v4", "llm_model": "glm-4.6v"}, False),
    ({"llm_base_url": "https://api.groq.com/openai/v1", "llm_model": "qwen/qwen3.8-27b"}, True),
    ({"llm_base_url": "http://localhost:11434/v1", "llm_model": "qwen3.5:9b"}, True),
    ({"llm_base_url": "https://api.openai.com/v1", "llm_model": "gpt-5"}, False),
    ({"llm_provider": "claude", "llm_model": "claude-opus-5-5"}, False),
])
def test_check_route(values, free):
    assert check_route(settings(**values)).free is free


def test_route_notes():
    assert "NVIDIA free trial" in check_route(settings()).note
    assert "Groq's free plan" in check_route(settings(llm_base_url="https://api.groq.com/openai/v1")).note


def test_make_ai_blocks_paid_routes():
    with pytest.raises(SetupProblem, match="ALLOW_PAID_MODELS"):
        make_ai(settings(llm_provider="claude", llm_model="claude-opus-5-5"))
    ai = make_ai(settings(llm_provider="claude", llm_model="claude-opus-5-5", allow_paid_models=True, llm_api_key="k"))
    assert isinstance(ai, ClaudeAI)
    assert isinstance(make_ai(settings(llm_api_key="k")), OpenAIStyleAI)


def test_sees_images_rule():
    assert sees_images(settings()) is True
    assert sees_images(settings(llm_model="some/text-model")) is False
    assert sees_images(settings(llm_model="some/text-model", llm_images="yes")) is True
    assert sees_images(settings(llm_images="no")) is False
    assert sees_images(settings(llm_provider="claude", llm_model="claude-opus-5-5")) is True


# ---- openai_style ----

def tool_reply(name="tap", arguments='{"why": "w", "target": 8, "target_text": "Add"}', **extra) -> dict:
    return {"model": "z-ai/glm-5.3-flash", "usage": {"total_tokens": 10}, "choices": [{
        "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "reasoning_content": '{"tool": "go_home"}',
                    "tool_calls": [{"type": "function", "function": {"name": name, "arguments": arguments}}]},
    }], **extra}


def text_reply(content, finish_reason="stop") -> dict:
    return {"choices": [{"finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}]}


class Server:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    def body(self, n: int = 0) -> dict:
        return json.loads(self.requests[n].content)


def make_openai(server: Server, base_url="https://integrate.api.nvidia.com/v1", model="z-ai/glm-5.3-flash",
                images=True) -> OpenAIStyleAI:
    ai = OpenAIStyleAI(base_url, model, "secret", images, transport=httpx.MockTransport(server))
    ai.poll_seconds = 0
    return ai


async def test_openai_tool_call_reply_and_request_shape():
    server = Server(tool_reply())
    ai = make_openai(server)
    decision = await ai.decide("SYS", "MSG", ALL, image=b"\xff\xd8jpeg", retry_note="YOUR LAST REPLY WAS NOT USABLE")
    assert (decision.tool, decision.args["target"]) == ("tap", 8)
    assert decision.model == "z-ai/glm-5.3-flash" and decision.usage == {"total_tokens": 10}
    request = server.requests[0]
    assert str(request.url) == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    body = server.body()
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    text, picture = body["messages"][1]["content"]
    assert text == {"type": "text", "text": "MSG\nYOUR LAST REPLY WAS NOT USABLE"}
    assert picture["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert body["tool_choice"] == "auto" and body["stream"] is False
    assert body["reasoning_effort"] == "low" and body["max_tokens"] == 8192 and body["temperature"] == 0.3


async def test_openai_per_host_settings():
    server = Server(tool_reply(), tool_reply(), tool_reply(), tool_reply(), tool_reply())
    await make_openai(server, model="nvidia/nemotron-3.5-super-vl-120b-a12b").decide("s", "m", ALL)
    await make_openai(server, "https://openrouter.ai/api/v1", "x:free").decide("s", "m", ALL)
    await make_openai(server, "https://api.groq.com/openai/v1", "qwen/qwen3.8-27b").decide("s", "m", ALL)
    await make_openai(server, "http://localhost:11434/v1", "qwen3.5:9b").decide("s", "m", ALL)
    await make_openai(server, "https://api.z.ai/api/paas/v4", "glm-4.6v-flash").decide("s", "m", ALL)
    nemotron, openrouter, groq, ollama, zai = (server.body(n) for n in range(5))
    assert nemotron["reasoning_effort"] == "low" and nemotron["parallel_tool_calls"] is False
    assert openrouter["reasoning"] == {"effort": "low"} and openrouter["parallel_tool_calls"] is False
    assert groq["max_completion_tokens"] == 8192 and "max_tokens" not in groq and groq["reasoning_format"] == "parsed"
    assert "tool_choice" not in ollama and ollama["max_tokens"] == 4096
    assert zai["tool_choice"] == "auto" and zai["max_tokens"] == 8192 and "reasoning_effort" not in zai


async def test_openai_text_json_reply():
    server = Server(text_reply('<think>hmm</think>```json\n{"tool": "click", "args": {"target": "5", "target_text": "Pizza"}}\n```'))
    decision = await make_openai(server).decide("s", "m", ALL)
    assert decision.tool == "tap" and decision.args["target"] == 5
    server = Server(text_reply([{"type": "text", "text": '{"tool": "go_back", "why": "leave"}'}]))
    assert (await make_openai(server).decide("s", "m", ALL)).tool == "go_back"


async def test_openai_auth_and_quota_errors():
    with pytest.raises(AuthProblem, match="LLM_API_KEY"):
        await make_openai(Server(httpx.Response(401, json={"error": {"message": "bad key"}}))).decide("s", "m", ALL)
    with pytest.raises(QuotaUsedUp):
        await make_openai(Server(httpx.Response(402, json={"error": {"message": "pay"}}))).decide("s", "m", ALL)
    with pytest.raises(SetupProblem, match="LLM_MODEL"):
        await make_openai(Server(httpx.Response(404, text="not found"))).decide("s", "m", ALL)
    with pytest.raises(AIUnavailable):
        await make_openai(Server(httpx.Response(503, text="down"))).decide("s", "m", ALL)


async def test_openai_429_rate_vs_daily():
    busy = httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"message": "Too many requests"}})
    with pytest.raises(RateLimited) as caught:
        await make_openai(Server(busy)).decide("s", "m", ALL)
    assert caught.value.retry_after == 7.0
    daily = httpx.Response(429, json={"error": {"message": "Rate limit reached for requests per day (RPD)"}})
    with pytest.raises(QuotaUsedUp):
        await make_openai(Server(daily)).decide("s", "m", ALL)


async def test_openai_202_polling():
    server = Server(httpx.Response(202, headers={"NVCF-REQID": "abc"}), httpx.Response(202, headers={"NVCF-REQID": "abc"}),
                    tool_reply())
    decision = await make_openai(server).decide("s", "m", ALL)
    assert decision.tool == "tap"
    assert [str(r.url) for r in server.requests[1:]] == ["https://integrate.api.nvidia.com/v1/status/abc"] * 2
    assert server.requests[1].method == "GET"


async def test_openai_tools_rejected_switches_to_json_mode():
    rejected = httpx.Response(400, json={"error": {"message": "tool_choice is not supported for this model"}})
    server = Server(rejected, text_reply('{"tool": "go_home", "args": {"why": "start"}}'), text_reply('{"tool": "go_back"}'))
    ai = make_openai(server)
    assert (await ai.decide("SYS", "m", ALL)).tool == "go_home"
    assert ai.json_mode
    second = server.body(1)
    assert "tools" not in second and "tool_choice" not in second
    assert second["messages"][0]["content"].startswith("SYS\n\nACTIONS:")
    assert second["messages"][0]["content"].endswith('{"tool": "...", "args": {...}}')
    await ai.decide("SYS", "m", ALL)
    assert "tools" not in server.body(2)


async def test_openai_other_400_is_setup_problem():
    server = Server(httpx.Response(400, json={"error": {"message": "context too long"}}))
    with pytest.raises(SetupProblem, match="context too long"):
        await make_openai(server).decide("s", "m", ALL)
    assert len(server.requests) == 1


@pytest.mark.parametrize("code, error", [
    (1113, QuotaUsedUp), (1308, QuotaUsedUp), (1310, QuotaUsedUp), (1302, RateLimited), (1305, AIUnavailable),
    (1211, SetupProblem), (1301, BadReply),
])
async def test_zai_body_codes(code, error):
    response = httpx.Response(429 if code != 1301 else 400, json={"error": {"code": str(code), "message": "x"}})
    with pytest.raises(error):
        await make_openai(Server(response), "https://api.z.ai/api/paas/v4", "glm-4.6v-flash").decide("s", "m", ALL)


async def test_openai_cut_off_retries_with_more_tokens():
    server = Server(text_reply("I will now", "length"), tool_reply())
    decision = await make_openai(server).decide("s", "m", ALL)
    assert decision.tool == "tap"
    assert [server.body(n)["max_tokens"] for n in range(2)] == [8192, 16384]
    server = Server(text_reply("", "length"), text_reply("still", "length"))
    with pytest.raises(BadReply, match="the reply was cut off"):
        await make_openai(server, "http://localhost:11434/v1", "qwen3.5:9b").decide("s", "m", ALL)
    assert [server.body(n)["max_tokens"] for n in range(2)] == [4096, 8192]


async def test_openai_timeout_and_connection_errors():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    for handler in (slow, down):
        ai = OpenAIStyleAI("https://integrate.api.nvidia.com/v1", "m", "k", False, transport=httpx.MockTransport(handler))
        with pytest.raises(AIUnavailable):
            await ai.decide("s", "m", ALL)


async def test_openai_bad_tool_name_is_bad_reply():
    with pytest.raises(BadReply, match="unknown action 'fly'"):
        await make_openai(Server(tool_reply(name="fly", arguments="{}"))).decide("s", "m", ALL)


# ---- claude ----

class FakeAnthropic:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def claude_response(*content, stop_reason="tool_use", model="claude-opus-5-5"):
    return SimpleNamespace(content=list(content), stop_reason=stop_reason, model=model,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80))


def tool_use(name="tap", **args):
    return SimpleNamespace(type="tool_use", name=name, input=args, id="t1")


def text_block(text):
    return SimpleNamespace(type="text", text=text)


async def test_claude_tool_use_and_request_shape():
    client = FakeAnthropic(claude_response(text_block("Opening."), tool_use("tap", why="w", target=8, target_text="Add",
                                                                             remember=None)))
    ai = ClaudeAI("claude-opus-5-5", None, "medium", client=client)
    decision = await ai.decide("SYS", "MSG", ALL, image=b"\x89PNGdata", retry_note="NOTE")
    assert decision.tool == "tap" and decision.args == {"why": "w", "target": 8, "target_text": "Add"}
    assert decision.text == "Opening." and decision.usage == {"input_tokens": 100, "output_tokens": 20,
                                                              "cache_read_input_tokens": 80}
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["max_tokens"] == 16000
    assert call["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    image, text = call["messages"][0]["content"]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
    assert text == {"type": "text", "text": "MSG\nNOTE"}
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert call["output_config"] == {"effort": "medium"}
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert call["tools"] == claude_tools(ALL)
    assert "thinking" not in call and "temperature" not in call


async def test_claude_reply_problems():
    with pytest.raises(BadReply, match="declined"):
        await ClaudeAI(client=FakeAnthropic(claude_response(stop_reason="refusal"))).decide("s", "m", ALL)
    with pytest.raises(BadReply, match="cut off"):
        await ClaudeAI(client=FakeAnthropic(claude_response(text_block("..."), stop_reason="max_tokens"))).decide("s", "m", ALL)
    with pytest.raises(BadReply, match="no action in the reply"):
        await ClaudeAI(client=FakeAnthropic(claude_response(text_block("hi"), stop_reason="end_turn"))).decide("s", "m", ALL)
    reply = claude_response(text_block('{"tool": "go_back"}'), stop_reason="end_turn")
    assert (await ClaudeAI(client=FakeAnthropic(reply)).decide("s", "m", ALL)).tool == "go_back"


async def test_claude_fallback_model_is_logged(caplog):
    client = FakeAnthropic(claude_response(tool_use("go_back", why="w"), model="claude-opus-5"))
    with caplog.at_level(logging.INFO, logger="jarvis.llm"):
        decision = await ClaudeAI(client=client).decide("s", "m", ALL)
    assert decision.model == "claude-opus-5"
    assert "Claude answered with claude-opus-5 (fallback)" in caplog.text


def api_error(kind, status, message="error", headers=None):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, headers=headers or {}, request=request)
    return kind(message, response=response, body=None)


@pytest.mark.parametrize("error, expected", [
    (api_error(anthropic.AuthenticationError, 401), AuthProblem),
    (api_error(anthropic.PermissionDeniedError, 403), AuthProblem),
    (api_error(anthropic.NotFoundError, 404), SetupProblem),
    (api_error(anthropic.RateLimitError, 429, headers={"retry-after": "12"}), RateLimited),
    (api_error(anthropic.BadRequestError, 400, "Your credit balance is too low"), QuotaUsedUp),
    (api_error(anthropic.BadRequestError, 400, "tools.0: invalid"), SetupProblem),
    (api_error(anthropic.InternalServerError, 500), AIUnavailable),
    (api_error(anthropic.OverloadedError, 529), AIUnavailable),
    (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")), AIUnavailable),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), AIUnavailable),
])
async def test_claude_error_mapping(error, expected):
    with pytest.raises(expected) as caught:
        await ClaudeAI(client=FakeAnthropic(error)).decide("s", "m", ALL)
    if expected is RateLimited:
        assert caught.value.retry_after == 12.0
