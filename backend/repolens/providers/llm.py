"""LLM providers. This module is the only code that calls an LLM API.

Every adapter streams text deltas and ends with one ``Completion`` that carries the stop reason
and the token usage of each model that ran.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

import anthropic
import httpx2
import openai
from anthropic.types.beta import BetaMessage, BetaMessageParam
from openai.types.chat import ChatCompletionMessageParam

from repolens.config import Settings

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
# Server-side refusal fallback: the API re-runs a declined request on a model it picks per
# refusal category, on the same stream.
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


class StopReason(StrEnum):
    END = "end"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class Completion:
    stop_reason: StopReason
    usage: tuple[TokenUsage, ...]
    """One entry per model attempt; several when a refusal fell back to another model."""


type LLMEvent = TextDelta | Completion


class LLMError(Exception):
    """The provider failed or could not be reached."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LLM(Protocol):
    @property
    def model(self) -> str: ...

    def stream(
        self, *, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[LLMEvent]:
        """Yield text deltas, then exactly one ``Completion``; raise ``LLMError`` on failure."""
        ...


class AnthropicLLM:
    def __init__(
        self, *, client: anthropic.AsyncAnthropic, model: str, refusal_fallback: bool = True
    ) -> None:
        self._client = client
        self._model = model
        self._refusal_fallback = refusal_fallback

    @property
    def model(self) -> str:
        return self._model

    async def stream(
        self, *, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[LLMEvent]:
        try:
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[_anthropic_message(message) for message in messages],
                betas=[ANTHROPIC_FALLBACK_BETA] if self._refusal_fallback else anthropic.omit,
                fallbacks="default" if self._refusal_fallback else anthropic.omit,
            ) as stream:
                async for text in stream.text_stream:
                    yield TextDelta(text)
                message = await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise _status_error("Anthropic", exc.status_code) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic request failed: {type(exc).__name__}") from exc
        yield Completion(stop_reason=_anthropic_stop(message), usage=_anthropic_usage(message))


def _anthropic_message(message: ChatMessage) -> BetaMessageParam:
    if message.role == "user":
        return {"role": "user", "content": message.content}
    return {"role": "assistant", "content": message.content}


def _anthropic_stop(message: BetaMessage) -> StopReason:
    match message.stop_reason:
        case "end_turn" | "stop_sequence":
            return StopReason.END
        case "max_tokens":
            return StopReason.MAX_TOKENS
        case "refusal":
            return StopReason.REFUSAL
        case _:
            return StopReason.OTHER


def _anthropic_usage(message: BetaMessage) -> tuple[TokenUsage, ...]:
    # With fallbacks, top-level usage covers only the attempt that produced the message;
    # iterations list every attempt, each billed at its own model's rates.
    if message.usage.iterations:
        return tuple(
            TokenUsage(
                model=getattr(iteration, "model", None) or message.model,
                input_tokens=iteration.input_tokens,
                output_tokens=iteration.output_tokens,
            )
            for iteration in message.usage.iterations
        )
    return (
        TokenUsage(
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        ),
    )


class OpenAILLM:
    """OpenAI Chat Completions. An ``AsyncAzureOpenAI`` client works too, with a deployment name."""

    def __init__(self, *, client: openai.AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def stream(
        self, *, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[LLMEvent]:
        params: list[ChatCompletionMessageParam] = [{"role": "system", "content": system}]
        params.extend(_openai_message(message) for message in messages)
        stop = StopReason.OTHER
        usage: tuple[TokenUsage, ...] = ()
        try:
            chunks = await self._client.chat.completions.create(
                model=self._model,
                messages=params,
                max_completion_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in chunks:
                for choice in chunk.choices:
                    if choice.delta.content:
                        yield TextDelta(choice.delta.content)
                    if choice.finish_reason is not None:
                        stop = _openai_stop(choice.finish_reason)
                if chunk.usage is not None:
                    usage = (
                        TokenUsage(
                            model=chunk.model,
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                        ),
                    )
        except openai.APIStatusError as exc:
            raise _status_error("OpenAI", exc.status_code) from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"OpenAI request failed: {type(exc).__name__}") from exc
        yield Completion(stop_reason=stop, usage=usage)


def _openai_message(message: ChatMessage) -> ChatCompletionMessageParam:
    if message.role == "user":
        return {"role": "user", "content": message.content}
    return {"role": "assistant", "content": message.content}


def _openai_stop(finish_reason: str) -> StopReason:
    match finish_reason:
        case "stop":
            return StopReason.END
        case "length":
            return StopReason.MAX_TOKENS
        case "content_filter":
            return StopReason.REFUSAL
        case _:
            return StopReason.OTHER


@dataclass
class ScriptedLLM:
    """Offline LLM for tests: streams ``chunks``, then completes or raises ``error``."""

    chunks: Sequence[str] = ("Scripted answer.",)
    stop_reason: StopReason = StopReason.END
    error: LLMError | None = None
    model: str = "scripted"
    calls: list[tuple[str, tuple[ChatMessage, ...], int]] = field(default_factory=list)

    async def stream(
        self, *, system: str, messages: Sequence[ChatMessage], max_tokens: int
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append((system, tuple(messages), max_tokens))
        for chunk in self.chunks:
            yield TextDelta(chunk)
        if self.error is not None:
            raise self.error
        output_tokens = sum(len(chunk.split()) for chunk in self.chunks)
        yield Completion(
            stop_reason=self.stop_reason,
            usage=(TokenUsage(model=self.model, input_tokens=0, output_tokens=output_tokens),),
        )


def _status_error(provider: str, status: int) -> LLMError:
    # An error event inside a stream arrives on a response that opened with HTTP 200.
    if status < 400:
        return LLMError(f"{provider} stream failed mid-response")
    return LLMError(f"{provider} returned HTTP {status}", status=status)


def create_llm(settings: Settings, http: httpx2.AsyncClient) -> LLM | None:
    """Build the configured LLM, or None when its credentials are not set."""
    match settings.llm_provider:
        case "anthropic":
            if settings.anthropic_api_key is None:
                return None
            return AnthropicLLM(
                client=anthropic.AsyncAnthropic(
                    api_key=settings.anthropic_api_key.get_secret_value(), http_client=http
                ),
                model=settings.llm_model or DEFAULT_ANTHROPIC_MODEL,
                refusal_fallback=settings.llm_refusal_fallback,
            )
        case "openai":
            if settings.openai_api_key is None:
                return None
            return OpenAILLM(
                client=openai.AsyncOpenAI(
                    api_key=settings.openai_api_key.get_secret_value(), http_client=http
                ),
                model=_required_model(settings),
            )
        case "azure_openai":
            if settings.azure_openai_api_key is None or settings.azure_openai_endpoint is None:
                return None
            return OpenAILLM(
                client=openai.AsyncAzureOpenAI(
                    azure_endpoint=settings.azure_openai_endpoint,
                    api_key=settings.azure_openai_api_key.get_secret_value(),
                    api_version=settings.azure_openai_api_version,
                    http_client=http,
                ),
                model=_required_model(settings),
            )


def _required_model(settings: Settings) -> str:
    if settings.llm_model is None:
        raise ValueError(f"REPOLENS_LLM_MODEL is required for the {settings.llm_provider} provider")
    return settings.llm_model
