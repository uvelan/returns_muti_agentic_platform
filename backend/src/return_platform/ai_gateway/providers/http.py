"""Bounded HTTP primitives for cloud and local AI providers."""

from __future__ import annotations

from typing import Any

import httpx

from return_platform.ai_gateway.providers.contracts import ProviderError


def secret_value(value: object) -> str | None:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    if not callable(getter):
        return None
    raw = str(getter()).strip()
    return raw or None


def raise_for_provider_status(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise ProviderError("AUTH_FAILED")
    if response.status_code == 429:
        raise ProviderError("RATE_LIMITED")
    if response.status_code == 404:
        raise ProviderError("MODEL_UNAVAILABLE")
    if response.status_code in {408, 504}:
        raise ProviderError("TIMEOUT")
    if response.status_code in {413, 422}:
        raise ProviderError("CONTEXT_LIMIT_EXCEEDED")
    if response.status_code >= 500:
        raise ProviderError("PROVIDER_UNAVAILABLE")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise ProviderError("RESPONSE_INVALID") from error


class HTTPProvider:
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
        raise_for_provider_status(response)
        try:
            data = response.json()
        except ValueError as error:
            raise ProviderError("RESPONSE_INVALID") from error
        if not isinstance(data, dict):
            raise ProviderError("RESPONSE_INVALID")
        return data
