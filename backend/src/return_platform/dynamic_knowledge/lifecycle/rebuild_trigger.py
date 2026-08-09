"""Decides when a rebuild is needed, and runs one.

`GenerationLifecycleOrchestrator.build_and_activate` had no caller anywhere in
`src/`. Every part of the blue/green protocol existed -- build, catch-up,
validation, activation, drain, retirement -- and nothing could reach any of it,
which is why `LEGACY_GENERATION_ID` is still what production resolves. This is
the entry point that makes the lifecycle reachable.

**The trigger is derived, not configured.** `ActiveRuntimeSnapshot` already
records the `schema_fingerprint` and `configuration_release_id` the live
generation was built from, and `ActiveSchema` carries its own
`configuration_checksum`. A rebuild is needed exactly when those disagree.
Adding a separate "rebuild needed" flag somewhere would be a second source of
truth that could disagree with the first.

**Idempotence is the whole contract.** `ensure_current` is written to be called
repeatedly -- on startup, on a schedule, from an operator endpoint -- and to do
nothing when nothing has changed. A trigger that rebuilt on every call would be
worse than no trigger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from return_platform.dynamic_knowledge.graph.generation import ActiveRuntimeSnapshot
from return_platform.dynamic_knowledge.lifecycle.mongo_store import ActiveRuntimeSnapshotStore
from return_platform.dynamic_knowledge.lifecycle.orchestrator import ActivationError
from return_platform.dynamic_knowledge.schema import ActiveSchema

_LOGGER = logging.getLogger(__name__)

_LEASE_HELD_STAGE = "ACQUIRE_REBUILD_LEASE"


class RebuildReason(StrEnum):
    NO_ACTIVE_GENERATION = "NO_ACTIVE_GENERATION"
    SCHEMA_FINGERPRINT_CHANGED = "SCHEMA_FINGERPRINT_CHANGED"
    CONFIGURATION_RELEASE_CHANGED = "CONFIGURATION_RELEASE_CHANGED"
    FORCED = "FORCED"


@dataclass(frozen=True, slots=True)
class RebuildDecision:
    """Why a rebuild is or is not needed. Separated from acting on it so the
    reasoning is inspectable -- an operator asking "why did it rebuild?" should
    not have to infer it from logs."""

    required: bool
    reason: RebuildReason | None
    detail: str


class RebuildOrchestrator(Protocol):
    """The subset of GenerationLifecycleOrchestrator this drives."""

    async def build_and_activate(
        self,
        *,
        schema: ActiveSchema,
        snapshot_name: str,
        configuration_release_id: str,
        search_index_release_id: str = "none",
    ) -> ActiveRuntimeSnapshot: ...


class RebuildTrigger:
    def __init__(
        self,
        *,
        snapshot_store: ActiveRuntimeSnapshotStore,
        orchestrator: RebuildOrchestrator,
    ) -> None:
        self._snapshot_store = snapshot_store
        self._orchestrator = orchestrator

    async def evaluate(
        self,
        *,
        schema: ActiveSchema,
        snapshot_name: str,
        configuration_release_id: str,
        force: bool = False,
    ) -> RebuildDecision:
        """Pure decision -- reads the snapshot, changes nothing.

        `force` is checked *after* reading the snapshot rather than short-
        circuiting, so a forced rebuild still reports what it is replacing.
        """
        current = await self._snapshot_store.read(snapshot_name=snapshot_name)
        if current is None:
            return RebuildDecision(
                required=True,
                reason=RebuildReason.NO_ACTIVE_GENERATION,
                detail=f"no ActiveRuntimeSnapshot exists for {snapshot_name!r}",
            )
        if force:
            return RebuildDecision(
                required=True,
                reason=RebuildReason.FORCED,
                detail=f"forced rebuild over generation {current.graph_generation_id}",
            )
        if current.schema_fingerprint != schema.configuration_checksum:
            return RebuildDecision(
                required=True,
                reason=RebuildReason.SCHEMA_FINGERPRINT_CHANGED,
                detail=(
                    f"active generation was built from schema fingerprint "
                    f"{current.schema_fingerprint!r}, loaded schema is "
                    f"{schema.configuration_checksum!r}"
                ),
            )
        if current.configuration_release_id != configuration_release_id:
            # The schema bytes can be identical across two releases -- the
            # fingerprint alone would call that "unchanged" -- but the release
            # id is what the snapshot pins for audit, so it must still cut over.
            return RebuildDecision(
                required=True,
                reason=RebuildReason.CONFIGURATION_RELEASE_CHANGED,
                detail=(
                    f"active generation pins release {current.configuration_release_id!r}, "
                    f"requested {configuration_release_id!r}"
                ),
            )
        return RebuildDecision(
            required=False,
            reason=None,
            detail=f"generation {current.graph_generation_id} is current",
        )

    async def ensure_current(
        self,
        *,
        schema: ActiveSchema,
        snapshot_name: str,
        configuration_release_id: str,
        force: bool = False,
        search_index_release_id: str = "none",
    ) -> ActiveRuntimeSnapshot | None:
        """Rebuild if needed. Returns the new snapshot, or None if none was.

        **A rebuild already in progress elsewhere is not an error.** Two
        replicas calling this on startup is the expected case, not a fault: the
        loser sees `ActivationError(stage="ACQUIRE_REBUILD_LEASE")` and returns
        None, exactly as if no rebuild were needed. Every other
        `ActivationError` propagates, because a build that failed validation or
        lost its compare-and-swap is something an operator must see.
        """
        decision = await self.evaluate(
            schema=schema,
            snapshot_name=snapshot_name,
            configuration_release_id=configuration_release_id,
            force=force,
        )
        if not decision.required:
            _LOGGER.debug("No rebuild needed for %s: %s", snapshot_name, decision.detail)
            return None

        _LOGGER.info("Rebuilding %s (%s): %s", snapshot_name, decision.reason, decision.detail)
        try:
            return await self._orchestrator.build_and_activate(
                schema=schema,
                snapshot_name=snapshot_name,
                configuration_release_id=configuration_release_id,
                search_index_release_id=search_index_release_id,
            )
        except ActivationError as error:
            if error.stage == _LEASE_HELD_STAGE:
                _LOGGER.info(
                    "Another instance is already rebuilding %s; standing down", snapshot_name
                )
                return None
            raise
