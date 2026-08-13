"""Stable provider contracts shared by all AI adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """Normalized provider failure that is safe to persist and expose."""

    def __init__(self, code: str, message: str = "AI provider request failed") -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    system_prompt: str
    user_payload: dict[str, Any]
    max_output_tokens: int | None = None
    temperature: float = 0.0
    response_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider: str
    model: str
    text: str
    # `input_tokens` is the prompt the provider actually processed -- the
    # *uncached* part -- and `cached_input_tokens` is what it served from a
    # prompt cache. They are separate because they are billed at different
    # rates, and adapters normalise to this split rather than passing through
    # whichever convention their vendor uses: Anthropic reports cache reads as a
    # sibling of `input_tokens`, OpenAI reports them as a subset of the prompt
    # count, and adding a subset to its own superset doubles the bill.
    #
    # `None` means the provider said nothing about caching, which is not the
    # same as "nothing was cached" -- it is why this is not defaulted to 0.
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class AIProvider(Protocol):
    name: str
    model: str

    @property
    def configured(self) -> bool: ...

    async def generate(self, request: ProviderRequest) -> ProviderResponse: ...
