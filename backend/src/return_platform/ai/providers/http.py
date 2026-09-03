"""Bounded HTTP primitives for cloud and local AI providers."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from return_platform.ai.providers.contracts import ProviderError

logger = logging.getLogger("return_platform.ai.providers.http")


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
    # 410 is how NVIDIA's integrate API retires a model: the body says
    # `{"status":410,"title":"Gone","detail":"The model 'openai/gpt-oss-120b' has
    # reached its end of life on 2026-09-03T08:00:00Z and is no longer
    # available."}`. Without this branch it fell through to `raise_for_status()`
    # and was reported as RESPONSE_INVALID, which reads as "the model sent us
    # garbage" and -- worse -- is not a route-level unavailability, so the router
    # never opened the model circuit and kept spending the retry budget on a
    # model that no longer exists. Four models went end-of-life in a single day
    # on 2026-09-03 (meta/llama-3.1-8b-instruct, meta/llama-3.1-70b-instruct,
    # nvidia/llama-3.3-nemotron-super-49b-v1, openai/gpt-oss-120b), so this is
    # the ordinary retirement path, not an edge case.
    if response.status_code == 410:
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
        logger.warning(
            "ai_provider_http_error",
            extra={"status_code": response.status_code, "body": response.text[:2_000]},
        )
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
