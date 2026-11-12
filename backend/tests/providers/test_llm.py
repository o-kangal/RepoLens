import json
from collections.abc import Callable

import anthropic
import httpx2
import openai
import pytest
from pydantic import SecretStr

from repolens.config import Settings
from repolens.providers.llm import (
    ANTHROPIC_FALLBACK_BETA,
    DEFAULT_ANTHROPIC_MODEL,
    LLM,
    AnthropicLLM,
    ChatMessage,
    Completion,
    LLMError,
    OpenAILLM,
    ScriptedLLM,
    StopReason,
    TextDelta,
    TokenUsage,
    configured_models,
    create_llms,
)

pytestmark = pytest.mark.anyio

SYSTEM = "Answer only from the sources."
MESSAGES = (ChatMessage("user", "Where is auth?"),)
type Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Recorder:
    def __init__(self, response: httpx2.Response | Exception) -> None:
        self._response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def body(self) -> dict[str, object]:
        [request] = self.requests
        loaded: dict[str, object] = json.loads(request.content)
        return loaded


def http(recorder: Recorder) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))


def sse(*events: dict[str, object], named: bool = True, done: bool = False) -> httpx2.Response:
    lines = []
    for event in events:
        if named:
            lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}\n")
    if done:
        lines.append("data: [DONE]\n")
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text="\n".join(lines) + "\n"
    )


async def run(llm: LLM) -> tuple[list[str], Completion]:
    texts: list[str] = []
    completions: list[Completion] = []
    async for event in llm.stream(system=SYSTEM, messages=MESSAGES, max_tokens=1000):
        if isinstance(event, TextDelta):
            assert not completions, "text must not follow the completion"
            texts.append(event.text)
        else:
            completions.append(event)
    [completion] = completions
    return texts, completion


# Anthropic


def anthropic_events(
    *texts: str, stop_reason: str = "end_turn", usage: dict[str, object] | None = None
) -> httpx2.Response:
    events: list[dict[str, object]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 1},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    ]
    events += [
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}
        for text in texts
    ]
    events += [
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": usage or {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    return sse(*events)


def claude(recorder: Recorder, *, refusal_fallback: bool = True) -> AnthropicLLM:
    client = anthropic.AsyncAnthropic(api_key="test-key", http_client=http(recorder), max_retries=0)
    return AnthropicLLM(client=client, model="claude-opus-5-5", refusal_fallback=refusal_fallback)


async def test_anthropic_streams_text_then_usage() -> None:
    recorder = Recorder(anthropic_events("Auth is ", "in auth.py [S1]."))

    texts, completion = await run(claude(recorder))

    assert texts == ["Auth is ", "in auth.py [S1]."]
    assert completion == Completion(
        stop_reason=StopReason.END,
        usage=(TokenUsage(model="claude-opus-5-5", input_tokens=12, output_tokens=5),),
    )
    body = recorder.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 1000
    assert body["system"] == SYSTEM
    assert body["messages"] == [{"role": "user", "content": "Where is auth?"}]
    assert body["stream"] is True


async def test_anthropic_asks_for_a_refusal_fallback_by_default() -> None:
    recorder = Recorder(anthropic_events("ok"))

    await run(claude(recorder))

    assert recorder.body()["fallbacks"] == "default"
    assert ANTHROPIC_FALLBACK_BETA in recorder.requests[0].headers["anthropic-beta"]


async def test_anthropic_fallback_can_be_turned_off() -> None:
    recorder = Recorder(anthropic_events("ok"))

    await run(claude(recorder, refusal_fallback=False))

    assert "fallbacks" not in recorder.body()
    assert "anthropic-beta" not in recorder.requests[0].headers


async def test_anthropic_reports_usage_of_every_fallback_attempt() -> None:
    usage = {
        "output_tokens": 7,
        "iterations": [
            {"type": "message", "model": "claude-opus-5-5", "input_tokens": 12, "output_tokens": 3},
            {
                "type": "fallback_message",
                "model": "claude-opus-4-8",
                "input_tokens": 15,
                "output_tokens": 7,
            },
        ],
    }
    recorder = Recorder(anthropic_events("partial ", "continued", usage=usage))

    texts, completion = await run(claude(recorder))

    assert texts == ["partial ", "continued"]
    assert completion.usage == (
        TokenUsage(model="claude-opus-5-5", input_tokens=12, output_tokens=3),
        TokenUsage(model="claude-opus-4-8", input_tokens=15, output_tokens=7),
    )


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("end_turn", StopReason.END),
        ("stop_sequence", StopReason.END),
        ("max_tokens", StopReason.MAX_TOKENS),
        ("refusal", StopReason.REFUSAL),
        ("pause_turn", StopReason.OTHER),
    ],
)
async def test_anthropic_stop_reasons(stop_reason: str, expected: StopReason) -> None:
    _, completion = await run(claude(Recorder(anthropic_events(stop_reason=stop_reason))))

    assert completion.stop_reason is expected


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
async def test_anthropic_http_errors_become_llm_errors(status: int) -> None:
    error = {"type": "error", "error": {"type": "api_error", "message": "failure"}}
    recorder = Recorder(httpx2.Response(status, json=error))

    with pytest.raises(LLMError) as info:
        await run(claude(recorder))

    assert info.value.status == status
    assert "test-key" not in str(info.value)


async def test_anthropic_error_event_mid_stream_becomes_an_llm_error() -> None:
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 1},
            },
        },
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ]

    with pytest.raises(LLMError, match="mid-response") as info:
        await run(claude(Recorder(sse(*events))))

    assert info.value.status is None


async def test_anthropic_connection_errors_become_llm_errors() -> None:
    recorder = Recorder(httpx2.ConnectError("refused"))

    with pytest.raises(LLMError, match="APIConnectionError") as info:
        await run(claude(recorder))

    assert info.value.status is None


# OpenAI and Azure OpenAI


def openai_chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, object]:
    choices = (
        []
        if usage is not None
        else [{"index": 0, "delta": {"content": content}, "finish_reason": finish_reason}]
    )
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-test",
        "choices": choices,
        "usage": usage,
    }


def openai_events(*texts: str, finish_reason: str = "stop") -> httpx2.Response:
    chunks = [openai_chunk(text) for text in texts]
    chunks.append(openai_chunk(finish_reason=finish_reason))
    chunks.append(
        openai_chunk(usage={"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24})
    )
    return sse(*chunks, named=False, done=True)


def gpt(recorder: Recorder) -> OpenAILLM:
    client = openai.AsyncOpenAI(api_key="test-key", http_client=http(recorder), max_retries=0)
    return OpenAILLM(client=client, model="gpt-test")


async def test_openai_streams_text_then_usage() -> None:
    recorder = Recorder(openai_events("Auth ", "is here."))

    texts, completion = await run(gpt(recorder))

    assert texts == ["Auth ", "is here."]
    assert completion == Completion(
        stop_reason=StopReason.END,
        usage=(TokenUsage(model="gpt-test", input_tokens=20, output_tokens=4),),
    )
    body = recorder.body()
    assert body["messages"] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Where is auth?"},
    ]
    assert body["max_completion_tokens"] == 1000
    assert body["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [
        ("stop", StopReason.END),
        ("length", StopReason.MAX_TOKENS),
        ("content_filter", StopReason.REFUSAL),
        ("tool_calls", StopReason.OTHER),
    ],
)
async def test_openai_stop_reasons(finish_reason: str, expected: StopReason) -> None:
    _, completion = await run(gpt(Recorder(openai_events("x", finish_reason=finish_reason))))

    assert completion.stop_reason is expected


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_openai_http_errors_become_llm_errors(status: int) -> None:
    recorder = Recorder(httpx2.Response(status, json={"error": {"message": "failure"}}))

    with pytest.raises(LLMError) as info:
        await run(gpt(recorder))

    assert info.value.status == status
    assert "test-key" not in str(info.value)


async def test_openai_connection_errors_become_llm_errors() -> None:
    with pytest.raises(LLMError, match="APIConnectionError"):
        await run(gpt(Recorder(httpx2.ConnectError("refused"))))


async def test_azure_openai_calls_the_deployment() -> None:
    recorder = Recorder(openai_events("hi"))
    client = openai.AsyncAzureOpenAI(
        azure_endpoint="https://example.openai.azure.com",
        api_key="azure-key",
        api_version="2024-10-21",
        http_client=http(recorder),
        max_retries=0,
    )

    texts, _ = await run(OpenAILLM(client=client, model="answers-deployment"))

    [request] = recorder.requests
    assert texts == ["hi"]
    assert request.url.host == "example.openai.azure.com"
    assert "answers-deployment" in request.url.path
    assert request.headers["api-key"] == "azure-key"


# Scripted fake


async def test_scripted_llm_streams_its_script_and_records_calls() -> None:
    llm = ScriptedLLM(chunks=("one ", "two"), stop_reason=StopReason.MAX_TOKENS)

    texts, completion = await run(llm)

    assert texts == ["one ", "two"]
    assert completion.stop_reason is StopReason.MAX_TOKENS
    assert completion.usage == (TokenUsage(model="scripted", input_tokens=0, output_tokens=2),)
    assert llm.calls == [(SYSTEM, MESSAGES, 1000)]


async def test_scripted_llm_can_fail_after_partial_output() -> None:
    llm = ScriptedLLM(chunks=("partial",), error=LLMError("boom", status=503))
    texts: list[str] = []

    with pytest.raises(LLMError, match="boom"):
        async for event in llm.stream(system=SYSTEM, messages=MESSAGES, max_tokens=10):
            assert isinstance(event, TextDelta)
            texts.append(event.text)

    assert texts == ["partial"]


# Configuration


async def test_anthropic_is_the_default_provider_and_model() -> None:
    async with httpx2.AsyncClient() as client:
        llms = create_llms(Settings(anthropic_api_key=SecretStr("k"), llm_model=None), client)

    assert list(llms) == [DEFAULT_ANTHROPIC_MODEL]
    assert isinstance(llms[DEFAULT_ANTHROPIC_MODEL], AnthropicLLM)


async def test_models_come_from_configuration_with_the_default_first() -> None:
    settings = Settings(
        anthropic_api_key=SecretStr("k"),
        llm_model="claude-sonnet-5-5",
        llm_models=["claude-haiku-5-5", "claude-sonnet-5-5"],
    )

    async with httpx2.AsyncClient() as client:
        llms = create_llms(settings, client)

    assert list(llms) == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    assert [llm.model for llm in llms.values()] == ["claude-sonnet-5-5", "claude-haiku-5-5"]


def test_further_models_are_read_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOLENS_LLM_MODELS", " claude-haiku-5-5, ,claude-sonnet-5-5 ")

    assert Settings().llm_models == ["claude-haiku-5-5", "claude-sonnet-5-5"]


async def test_openai_and_azure_use_the_configured_model_or_deployment() -> None:
    async with httpx2.AsyncClient() as client:
        openai_llms = create_llms(
            Settings(llm_provider="openai", openai_api_key=SecretStr("k"), llm_model="gpt-x"),
            client,
        )
        azure_llms = create_llms(
            Settings(
                llm_provider="azure_openai",
                azure_openai_endpoint="https://example.openai.azure.com",
                azure_openai_api_key=SecretStr("k"),
                azure_openai_api_version="2024-10-21",
                llm_model="answers-deployment",
            ),
            client,
        )

    assert list(openai_llms) == ["gpt-x"]
    assert isinstance(openai_llms["gpt-x"], OpenAILLM)
    assert list(azure_llms) == ["answers-deployment"]
    assert isinstance(azure_llms["answers-deployment"], OpenAILLM)


@pytest.mark.parametrize(
    "settings",
    [
        Settings(anthropic_api_key=None),
        Settings(llm_provider="openai", openai_api_key=None, llm_model="gpt-x"),
        Settings(
            llm_provider="azure_openai",
            azure_openai_api_key=SecretStr("k"),
            azure_openai_endpoint=None,
            llm_model="d",
        ),
    ],
)
async def test_missing_credentials_mean_no_llms(settings: Settings) -> None:
    async with httpx2.AsyncClient() as client:
        assert create_llms(settings, client) == {}


def test_openai_needs_a_configured_model() -> None:
    settings = Settings(llm_provider="openai", openai_api_key=SecretStr("k"), llm_model=None)

    with pytest.raises(ValueError, match="REPOLENS_LLM_MODEL"):
        configured_models(settings)
