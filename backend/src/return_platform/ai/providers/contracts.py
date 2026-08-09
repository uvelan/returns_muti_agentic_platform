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
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class AIProvider(Protocol):
    name: str
    model: str

    @property
    def configured(self) -> bool: ...

    async def generate(self, request: ProviderRequest) -> ProviderResponse: ...
