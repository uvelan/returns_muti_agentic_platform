"""Answer from a recording where one exists, and call the real provider where it does not.

The one genuinely useful thing the predecessor platform had and this one lost.
`infrastructure/simulator/` could re-run a conversation against stored responses;
without that, every evaluation run costs tokens, takes provider latency, and
gives a slightly different answer than the run before it -- which makes "did my
change improve anything" unanswerable, because the baseline moved too.

**A miss is a real call, not a failure.** That is what makes the recording build
itself: run the suite once against live providers and every request is captured;
run it again and nothing leaves the process. A new prompt, a changed schema or an
extra turn simply costs one call the first time it appears. `STRICT` turns a miss
into an error instead, for the case where the whole point is proving that
*nothing* reached a provider.

**Recordings are keyed by what was actually asked**, not by conversation or turn
position: a digest over the system prompt, the user payload, the schema and the
decoding parameters. Two turns that ask the identical question share one
recording, and changing the prompt by a character correctly misses rather than
replaying an answer to a different question.

**A recording is a model's answer and stays labelled as one.** The stored
provider and model travel back with the text, so a trace replayed from disk is
still attributable to the provider that produced it. This deliberately does not
share storage with the interception records: those hold text a *human* typed,
and `DurableInterceptionProvider` goes to some trouble to keep human output
labelled human. Merging the two would put a person's words into an evaluation
set as though a model had written them.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Protocol

from return_platform.ai.providers.contracts import (
    AIProvider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)

__all__ = ["ReplayProvider", "ReplayStore", "request_digest"]

logger = logging.getLogger("return_platform.ai.providers.replay")


class ReplayStore(Protocol):
    """Persistence for recorded provider answers.

    Deliberately narrow: a digest in, a recording out, and a way to write one.
    Anything wider would invite callers to browse recordings as though they were
    an audit surface, which the interception records already are.
    """

    async def read(self, digest: str) -> dict[str, Any] | None: ...

    async def write(self, digest: str, record: dict[str, Any]) -> None: ...


def request_digest(request: ProviderRequest, *, task_id: str | None = None) -> str:
    """A stable key for "this exact question".

    Canonical JSON with sorted keys, so a payload whose fields serialise in a
    different order still finds its recording -- dictionary order is an
    implementation detail of whoever built the request, and letting it change
    the key would produce a store that misses almost everything.

    `temperature` and the schema are part of the key on purpose: the same prompt
    decoded differently, or asked for a different shape, is a different question
    and must not replay the old answer.
    """
    payload = {
        "task": task_id,
        "system": request.system_prompt,
        "user": request.user_payload,
        "schema": request.response_schema,
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReplayProvider:
    """Wraps a real provider with a recording layer.

    A wrapper rather than a peer provider because replay is a *mode* applied to
    a provider, not an alternative to one: on a miss it has to make the call the
    wrapped provider would have made, with that provider's credentials, model
    and identity. A sibling entry in the route table would have no way to do
    that, and would need its own copy of the routing decision to guess.
    """

    def __init__(
        self,
        inner: AIProvider,
        store: ReplayStore,
        *,
        task_id: str | None = None,
        strict: bool = False,
    ) -> None:
        self._inner = inner
        self._store = store
        self._task_id = task_id
        self._strict = strict
        # The wrapped provider's identity, not "REPLAY". A replayed answer is
        # still that provider's answer, and a trace that said otherwise would
        # make recorded runs incomparable with live ones -- which is the one
        # thing replay exists to enable. Plain attributes because `AIProvider`
        # declares them as settable, and a read-only property does not satisfy
        # the protocol.
        self.name = inner.name
        self.model = inner.model

    @property
    def configured(self) -> bool:
        # In strict mode nothing is ever called, so a missing credential is not
        # a reason to declare the route unusable -- that is the whole point of
        # replaying a suite on a machine with no keys.
        return True if self._strict else self._inner.configured

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        digest = request_digest(request, task_id=self._task_id)
        recorded = await self._read(digest)
        if recorded is not None:
            return ProviderResponse(
                provider=str(recorded.get("provider", self._inner.name)),
                model=str(recorded.get("model", self._inner.model)),
                text=str(recorded.get("text", "")),
                input_tokens=recorded.get("inputTokens"),
                output_tokens=recorded.get("outputTokens"),
                total_tokens=recorded.get("totalTokens"),
            )

        if self._strict:
            raise ProviderError(
                "REPLAY_MISS",
                f"no recording for request {digest[:12]} and strict replay forbids a live call",
            )

        response = await self._inner.generate(request)
        await self._record(digest, response)
        return response

    async def _read(self, digest: str) -> dict[str, Any] | None:
        try:
            return await self._store.read(digest)
        except Exception:  # noqa: BLE001 - a broken recording must not break the call
            # Degrading to a live call is the safe direction: the caller gets a
            # real answer and pays for it, rather than an error for a cache.
            logger.warning("replay_store_unreadable", extra={"digest": digest}, exc_info=True)
            return None

    async def _record(self, digest: str, response: ProviderResponse) -> None:
        try:
            await self._store.write(
                digest,
                {
                    "provider": response.provider,
                    "model": response.model,
                    "text": response.text,
                    "inputTokens": response.input_tokens,
                    "outputTokens": response.output_tokens,
                    "totalTokens": response.total_tokens,
                },
            )
        except Exception:  # noqa: BLE001 - the answer is already good
            # The call succeeded; failing it because the recording could not be
            # written would turn a cache problem into a user-visible outage.
            logger.warning("replay_store_unwritable", extra={"digest": digest}, exc_info=True)
