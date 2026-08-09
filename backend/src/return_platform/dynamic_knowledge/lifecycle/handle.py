"""The one place a request learns which graph generation serves it.

Phase 12's rule is "no code below handle acquisition resolves 'current
generation' independently". Two things follow from that, and this module exists
to make both true at once rather than leaving them to each caller's discipline:

* **Resolution happens exactly once per request.** A request that re-resolved
  mid-flight could read half its data from one generation and half from its
  successor -- the blue/green cutover would become visible as inconsistency
  rather than as an atomic swap.
* **Resolution and lease acquisition are the same step.** Slice 1 built the
  drain that retirement waits on, but a drain only means something if the
  readers it is protecting are actually counted. Resolving without leasing
  gives a request that believes it is pinned to a generation the retirement
  path is free to remove.

Binding them into one async context manager is what makes "resolved but not
leased" unrepresentable: there is no API that returns a generation id without
also having recorded the claim, and no way to hold the claim past the `async
with` that took it.

**The lease is best-effort, deliberately.** `acquire` yields a handle even when
no lease store is configured, and even when the store refuses. A refusal means
the generation began draining between resolution and acquisition, which is
precisely the case the plan answers with `REBIND_ON_RESUME` -- the caller should
re-resolve, not fail -- and until the rebind path exists, degrading to the
unleased behaviour that shipped before this module is strictly better than
failing a request that would have worked. `leased` says which happened, so
nothing has to infer it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from return_platform.dynamic_knowledge.lifecycle.lease_store import GenerationLeaseStore
from return_platform.dynamic_knowledge.schema import ActiveSchema

_LOGGER = logging.getLogger(__name__)

# Long enough to outlive a full reasoning turn including model latency, short
# enough that a crashed request stops blocking a retirement within minutes
# rather than hours. Retirement's own drain wait is bounded independently, so
# this is not the only backstop.
DEFAULT_READ_LEASE_TTL_SECONDS = 900


class GenerationResolver(Protocol):
    """The existing `MongoGraphStateProvider.active_generation` shape."""

    async def active_generation(self, schema: ActiveSchema) -> str: ...


@dataclass(frozen=True, slots=True)
class GenerationHandle:
    """One request's pinned view of which generation serves it.

    `leased` is not decoration. An unleased handle is one the retirement path
    cannot see, so a generation it names may be retired while the request is
    still using it. Callers that need the guarantee -- as opposed to callers
    that merely need an id -- have to be able to tell the difference.
    """

    graph_generation_id: str
    leased: bool
    lease_id: str | None = None


class GenerationHandleProvider:
    """Resolves and leases as one step. The only sanctioned way to learn the
    current generation."""

    def __init__(
        self,
        resolver: GenerationResolver,
        *,
        lease_store: GenerationLeaseStore | None = None,
        owner_instance_id: str = "unknown",
        read_lease_ttl_seconds: int = DEFAULT_READ_LEASE_TTL_SECONDS,
    ) -> None:
        self._resolver = resolver
        self._lease_store = lease_store
        self._owner_instance_id = owner_instance_id
        self._read_lease_ttl_seconds = read_lease_ttl_seconds

    @asynccontextmanager
    async def acquire_read(self, schema: ActiveSchema) -> AsyncIterator[GenerationHandle]:
        """Pin a generation for the duration of the block, and release on exit.

        Release is in a `finally` so a raising body still gives the lease back:
        an exception that leaked a lease would block the next retirement for the
        full TTL, turning a transient request failure into an operational one.
        """
        graph_generation_id = await self._resolver.active_generation(schema)
        lease_id = await self._try_acquire(graph_generation_id)
        try:
            yield GenerationHandle(
                graph_generation_id=graph_generation_id,
                leased=lease_id is not None,
                lease_id=lease_id,
            )
        finally:
            if lease_id is not None and self._lease_store is not None:
                try:
                    await self._lease_store.release(
                        graph_generation_id=graph_generation_id, lease_id=lease_id
                    )
                except Exception:
                    # The TTL is the backstop. Failing the request on a release
                    # error would convert a cleanup problem into a user-visible
                    # one for no gain.
                    _LOGGER.exception(
                        "Could not release read lease %s on generation %s; "
                        "it will expire on its TTL",
                        lease_id,
                        graph_generation_id,
                    )

    async def _try_acquire(self, graph_generation_id: str) -> str | None:
        if self._lease_store is None:
            return None
        try:
            lease = await self._lease_store.acquire_read_lease(
                graph_generation_id=graph_generation_id,
                # No ActiveRuntimeSnapshot is threaded through this path yet --
                # `active_generation` reads the `dynamic_graph_generations`
                # collection, a parallel notion of "current" that predates the
                # snapshot. Recorded as 0 rather than invented, so nothing reads
                # it as a real activation version.
                snapshot_activation_version=0,
                owner_instance_id=self._owner_instance_id,
                ttl_seconds=self._read_lease_ttl_seconds,
            )
        except Exception:
            _LOGGER.exception(
                "Could not acquire a read lease on generation %s; proceeding unleased",
                graph_generation_id,
            )
            return None
        if lease is None:
            _LOGGER.info(
                "Generation %s is draining; proceeding unleased pending REBIND_ON_RESUME",
                graph_generation_id,
            )
            return None
        return lease.lease_id
