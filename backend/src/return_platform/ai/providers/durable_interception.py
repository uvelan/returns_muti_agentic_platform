"""A MANUAL provider backed by the durable interception store.

Drop-in for `ManualFileProvider`: same `AIProvider` shape, same `MANUAL` /
`manual-human-v1` identity, so it plugs into the existing route pool with no
caller change. What differs is where the held request lives.

`ManualFileProvider` writes JSON under `.manual_llm/` relative to the process
CWD and polls the directory. That has no durability story: a restart loses every
in-flight request, two replicas cannot see each other's files, an operator must
have filesystem access to the container, and nothing records who answered. This
persists the same handoff to the platform store instead, sealed at rest, with
the resume command written in the same document.

**Human output stays human output.** `name`/`model` report `MANUAL` and
`manual-human-v1`, never the provider whose place the human took. An evaluation
set built from traces must not silently include text a person wrote.

**The response is not trusted more than a model's.** It is returned as a plain
`ProviderResponse` and travels the identical path afterwards -- the invoker's
schema parse, `inspect_output`, the response contract. A human typing free text
into an operator console is at least as likely to produce something malformed as
a model is, and giving it a shortcut past validation would put the one unchecked
payload in the system on the most privileged path.

**Still poll-based, and that is a deliberate half-step.** The plan also asks for
an at-least-once resume *worker* so the request does not occupy a coroutine
while a human thinks. That requires the caller to release its turn and be woken
later, which the Order Agent can already express (`interrupt()` + Temporal) but
the analyzer cannot yet. Moving the handoff off the filesystem is worth landing
on its own; the non-blocking resume is the next slice, and the record this
writes is already shaped for it -- `ResumeCommand` exists precisely so a worker
can complete the work without the original coroutine.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from return_platform.ai.interception.records import InterceptionStatus, ResumeCommand
from return_platform.ai.interception.store import InterceptionStore
from return_platform.ai.providers.contracts import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.configuration.settings import Settings


class DurableInterceptionProvider:
    name = "MANUAL"
    model = "manual-human-v1"

    def __init__(
        self,
        settings: Settings,
        store: InterceptionStore,
        *,
        task_id: str = "UNKNOWN",
        resume: ResumeCommand | None = None,
        poll_seconds: float = 1.0,
        timeout_seconds: float = 600.0,
    ) -> None:
        # Same gate ManualFileProvider uses: a human-in-the-loop provider must be
        # structurally impossible to deploy, not merely discouraged.
        self._enabled = settings.environment in {"development", "test"}
        self._store = store
        self._task_id = task_id
        self._resume = resume or ResumeCommand(run_id="unknown", thread_id="unknown")
        self._poll_seconds = poll_seconds
        self._timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return self._enabled

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self._enabled:
            raise ProviderError("POLICY_BLOCKED")
        interception_id = uuid.uuid4().hex
        expires_at = datetime.now(UTC) + timedelta(seconds=self._timeout_seconds)
        await self._store.open(
            interception_id=interception_id,
            task_id=self._task_id,
            request_payload={
                "systemPrompt": request.system_prompt,
                "userPayload": request.user_payload,
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
            resume=self._resume,
            expires_at=expires_at,
        )

        deadline = asyncio.get_running_loop().time() + self._timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            record = await self._store.get(interception_id)
            if record is None:
                raise ProviderError("INTERCEPTION_LOST")
            if record.status is InterceptionStatus.ANSWERED:
                payload = await self._store.request_payload(interception_id)
                text = (payload or {}).get("responseText")
                if not isinstance(text, str) or not text:
                    raise ProviderError("INTERCEPTION_EMPTY")
                return ProviderResponse(self.name, self.model, text)
            if record.status in {InterceptionStatus.CANCELLED, InterceptionStatus.EXPIRED}:
                raise ProviderError("INTERCEPTION_CLOSED")
            await asyncio.sleep(self._poll_seconds)

        # Close the record on the way out. Leaving it PENDING would show an
        # operator a request whose caller has already given up -- they would
        # answer into nothing, which is worse than seeing it expired.
        await self._store.cancel(interception_id=interception_id, status=InterceptionStatus.EXPIRED)
        raise ProviderError("TIMEOUT")


def render_request_for_operator(payload: dict[str, object]) -> str:
    """The held request, formatted for a human to read and answer.

    Exists so an operator console never has to re-derive the shape, and so the
    rendering is one place if the payload shape changes.
    """
    return json.dumps(payload, indent=2, sort_keys=True)
