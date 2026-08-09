"""File-handoff AI provider for live testing without any external API call.

Only usable when ``environment`` is ``development`` or ``test`` - the same gate
:class:`SimulatorProvider` uses, and for the same reason: this must never be
reachable in production. When selected, ``generate`` writes the exact request
a real model would receive to disk and waits for a human-authored response
file to appear, returning its contents as the model's raw text output. Pair
with ``scripts/manual_llm_responder.py``, which watches the same directory and
prompts a human for that response.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from return_platform.ai.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.configuration.settings import Settings

DEFAULT_MANUAL_LLM_DIR = Path(".manual_llm")


class ManualFileProvider:
    name = "MANUAL"
    model = "manual-human-v1"

    def __init__(
        self,
        settings: Settings,
        *,
        base_dir: Path | None = None,
        poll_seconds: float = 1.0,
        timeout_seconds: float = 600.0,
    ) -> None:
        self._enabled = settings.environment in {"development", "test"}
        self._base_dir = base_dir or DEFAULT_MANUAL_LLM_DIR
        self._poll_seconds = poll_seconds
        self._timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return self._enabled

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self._enabled:
            raise ProviderError("POLICY_BLOCKED")
        request_id = uuid.uuid4().hex
        requests_dir = self._base_dir / "requests"
        responses_dir = self._base_dir / "responses"
        requests_dir.mkdir(parents=True, exist_ok=True)
        responses_dir.mkdir(parents=True, exist_ok=True)
        request_path = requests_dir / f"{request_id}.json"
        response_path = responses_dir / f"{request_id}.json"
        request_path.write_text(
            json.dumps(
                {
                    "requestId": request_id,
                    "systemPrompt": request.system_prompt,
                    "userPayload": request.user_payload,
                    "temperature": request.temperature,
                    "maxOutputTokens": request.max_output_tokens,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        try:
            deadline = time.monotonic() + self._timeout_seconds
            while time.monotonic() < deadline:
                if response_path.is_file():
                    text = response_path.read_text(encoding="utf-8")
                    response_path.unlink(missing_ok=True)
                    return ProviderResponse(self.name, self.model, text)
                await asyncio.sleep(self._poll_seconds)
        finally:
            request_path.unlink(missing_ok=True)
        raise ProviderError("TIMEOUT")
