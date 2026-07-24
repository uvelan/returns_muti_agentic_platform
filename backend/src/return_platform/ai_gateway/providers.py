"""Provider adapters for Gemini, NVIDIA NIM, OpenAI, Anthropic, Ollama, and simulator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from return_platform.configuration.settings import Settings


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str = "AI provider request failed") -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    system_prompt: str
    user_payload: dict[str, Any]


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


def _secret(value: object) -> str | None:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    if not callable(getter):
        return None
    raw = str(getter()).strip()
    return raw or None


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise ProviderError("AUTH_FAILED")
    if response.status_code == 429:
        raise ProviderError("RATE_LIMITED")
    if response.status_code >= 500:
        raise ProviderError("PROVIDER_UNAVAILABLE")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise ProviderError("RESPONSE_INVALID") from error


class _HTTPProvider:
    name: str
    model: str

    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)

    async def _post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as error:
            raise ProviderError("TIMEOUT") from error
        except httpx.HTTPError as error:
            raise ProviderError("PROVIDER_UNAVAILABLE") from error
        _raise_for_status(response)
        try:
            data = response.json()
        except ValueError as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(data, dict):
            raise ProviderError("RESPONSE_INVALID")
        return data


class GeminiProvider(_HTTPProvider):
    name = "GOOGLE"

    def __init__(self, settings: Settings) -> None:
        super().__init__(timeout_seconds=settings.ai_timeout_seconds)
        self._api_key = _secret(settings.google_api_key)
        self._base_url = settings.google_base_url
        self.model = settings.google_model

    @property
    def configured(self) -> bool:
        return self._api_key is not None

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if self._api_key is None:
            raise ProviderError("AUTH_FAILED")
        data = await self._post(
            f"{self._base_url}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
            payload={
                "systemInstruction": {"parts": [{"text": request.system_prompt}]},
                "contents": [
                    {"role": "user", "parts": [{"text": json.dumps(request.user_payload)}]}
                ],
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "application/json",
                },
            },
        )
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("RESPONSE_INVALID")
        usage = data.get("usageMetadata", {})
        return ProviderResponse(
            self.name,
            self.model,
            text,
            input_tokens=usage.get("promptTokenCount") if isinstance(usage, dict) else None,
            output_tokens=usage.get("candidatesTokenCount") if isinstance(usage, dict) else None,
            total_tokens=usage.get("totalTokenCount") if isinstance(usage, dict) else None,
        )


class OpenAICompatibleProvider(_HTTPProvider):
    def __init__(
        self,
        *,
        name: str,
        api_key: str | None,
        base_url: str,
        model: str | None,
        timeout_seconds: float,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.name = name
        self._api_key = api_key
        self._base_url = base_url
        self.model = model or ""

    @property
    def configured(self) -> bool:
        return bool(self.model) and (self.name == "OLLAMA" or self._api_key is not None)

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self.configured:
            raise ProviderError("AUTH_FAILED")
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = await self._post(
            f"{self._base_url}/chat/completions",
            headers=headers,
            payload={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": json.dumps(request.user_payload)},
                ],
                "temperature": 0,
                "stream": False,
            },
        )
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("RESPONSE_INVALID")
        usage = data.get("usage", {})
        return ProviderResponse(
            self.name,
            self.model,
            text,
            input_tokens=usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            output_tokens=usage.get("completion_tokens") if isinstance(usage, dict) else None,
            total_tokens=usage.get("total_tokens") if isinstance(usage, dict) else None,
        )


class OpenAIResponsesProvider(_HTTPProvider):
    name = "OPENAI"

    def __init__(self, settings: Settings) -> None:
        super().__init__(timeout_seconds=settings.ai_timeout_seconds)
        self._api_key = _secret(settings.openai_api_key)
        self._base_url = settings.openai_base_url
        self.model = settings.openai_model or ""

    @property
    def configured(self) -> bool:
        return self._api_key is not None and bool(self.model)

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self.configured or self._api_key is None:
            raise ProviderError("AUTH_FAILED")
        data = await self._post(
            f"{self._base_url}/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload={
                "model": self.model,
                "instructions": request.system_prompt,
                "input": json.dumps(request.user_payload),
                "store": False,
            },
        )
        text = data.get("output_text")
        if not isinstance(text, str) or not text.strip():
            try:
                text = data["output"][0]["content"][0]["text"]
            except (KeyError, IndexError, TypeError) as error:
                raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("RESPONSE_INVALID")
        usage = data.get("usage", {})
        return ProviderResponse(
            self.name,
            self.model,
            text,
            input_tokens=usage.get("input_tokens") if isinstance(usage, dict) else None,
            output_tokens=usage.get("output_tokens") if isinstance(usage, dict) else None,
            total_tokens=(
                int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
                if isinstance(usage, dict)
                else None
            ),
        )


class AnthropicProvider(_HTTPProvider):
    name = "ANTHROPIC"

    def __init__(self, settings: Settings) -> None:
        super().__init__(timeout_seconds=settings.ai_timeout_seconds)
        self._api_key = _secret(settings.anthropic_api_key)
        self._base_url = settings.anthropic_base_url
        self._version = settings.anthropic_version
        self.model = settings.anthropic_model or ""

    @property
    def configured(self) -> bool:
        return self._api_key is not None and bool(self.model)

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self.configured or self._api_key is None:
            raise ProviderError("AUTH_FAILED")
        data = await self._post(
            f"{self._base_url}/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self._version,
                "Content-Type": "application/json",
            },
            payload={
                "model": self.model,
                "max_tokens": 512,
                "temperature": 0,
                "system": request.system_prompt,
                "messages": [{"role": "user", "content": json.dumps(request.user_payload)}],
            },
        )
        try:
            text = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("RESPONSE_INVALID")
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        return ProviderResponse(
            self.name,
            self.model,
            text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=(
                int(input_tokens or 0) + int(output_tokens or 0)
                if input_tokens is not None or output_tokens is not None
                else None
            ),
        )


class SimulatorProvider:
    name = "SIMULATOR"
    model = "deterministic-eligibility-simulator-v1"

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.environment in {"development", "test"}

    @property
    def configured(self) -> bool:
        return self._enabled

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self._enabled:
            raise ProviderError("POLICY_BLOCKED")
        requested = request.user_payload.get("requestedDecision")
        if requested in {"APPROVE", "REJECT", "REVIEW_REQUIRED"}:
            decision = requested
        else:
            reason = str(request.user_payload.get("reasonCode", "")).upper()
            order_status = str(request.user_payload.get("orderStatus", "")).upper()
            days = request.user_payload.get("daysSinceDelivery")
            if order_status != "DELIVERED":
                decision = "REJECT"
            elif isinstance(days, int) and days > 45:
                decision = "REJECT"
            elif reason in {"HAZARDOUS", "FRAUD_SUSPECTED", "SERIAL_MISMATCH"}:
                decision = "REVIEW_REQUIRED"
            else:
                decision = "APPROVE"
        text = json.dumps(
            {
                "decision": decision,
                "explanation": "Deterministic simulator policy evaluation.",
                "confidenceMillionths": 900_000 if decision != "REVIEW_REQUIRED" else 500_000,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return ProviderResponse(self.name, self.model, text)


def build_providers(settings: Settings) -> dict[str, AIProvider]:
    return {
        "GOOGLE": GeminiProvider(settings),
        "NVIDIA": OpenAICompatibleProvider(
            name="NVIDIA",
            api_key=_secret(settings.nvidia_api_key),
            base_url=settings.nvidia_base_url,
            model=settings.nvidia_model,
            timeout_seconds=settings.ai_timeout_seconds,
        ),
        "OPENAI": OpenAIResponsesProvider(settings),
        "ANTHROPIC": AnthropicProvider(settings),
        "OLLAMA": OpenAICompatibleProvider(
            name="OLLAMA",
            api_key=None,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ai_timeout_seconds,
        ),
        "SIMULATOR": SimulatorProvider(settings),
    }
