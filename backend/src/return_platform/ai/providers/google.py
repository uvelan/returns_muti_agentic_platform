"""Google Gemini adapter."""

from __future__ import annotations

import json
import logging

from return_platform.ai.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai.providers.http import HTTPProvider, secret_value
from return_platform.ai.providers.schema_cleaner import clean_gemini_schema
from return_platform.configuration.settings import Settings

logger = logging.getLogger("return_platform.ai.providers.google")


class GeminiProvider(HTTPProvider):
    name = "GOOGLE"

    def __init__(self, settings: Settings) -> None:
        super().__init__(timeout_seconds=settings.ai_timeout_seconds)
        self._api_key = secret_value(settings.google_api_key)
        self._base_url = settings.google_base_url
        self.model = settings.google_model or ""

    @property
    def configured(self) -> bool:
        return self._api_key is not None and bool(self.model)

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if self._api_key is None:
            raise ProviderError("AUTH_FAILED")
        # Some deployments configure the model id already prefixed with "models/".
        url = f"{self._base_url}/models/{self.model}:generateContent"
        if "/" in self.model:
            url = f"{self._base_url}/{self.model}:generateContent"
        logger.debug("gemini_request", extra={"model": self.model, "url": url})

        data = await self._post(
            url,
            headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
            payload={
                "systemInstruction": {"parts": [{"text": request.system_prompt}]},
                "contents": [
                    {"role": "user", "parts": [{"text": json.dumps(request.user_payload)}]}
                ],
                "generationConfig": {
                    "temperature": request.temperature,
                    "responseMimeType": "application/json",
                    **(
                        {"responseSchema": clean_gemini_schema(request.response_schema)}
                        if request.response_schema is not None
                        else {}
                    ),
                    **(
                        {"maxOutputTokens": request.max_output_tokens}
                        if request.max_output_tokens is not None
                        else {}
                    ),
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
