"""The rebuild trigger: when a rebuild happens, and when it must not.

`build_and_activate` had no caller in `src/`. The whole blue/green protocol was
unreachable, which is why production still resolves `LEGACY_GENERATION_ID`.

Two properties carry the weight. **Idempotence**: this is designed to be called
on startup and on a schedule, so a trigger that rebuilt whenever it was asked
would be worse than none. And **the lease-held case is not an error**: two
replicas starting together is the expected case, not a fault, so the loser must
stand down quietly while every other activation failure still surfaces.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from return_platform.dynamic_knowledge.graph.generation import ActiveRuntimeSnapshot
from return_platform.dynamic_knowledge.lifecycle.orchestrator import ActivationError
from return_platform.dynamic_knowledge.lifecycle.rebuild_trigger import (
    RebuildReason,
    RebuildTrigger,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class _Schema:
    def __init__(self, checksum: str = "fingerprint-1") -> None:
        self.configuration_checksum = checksum


class _SnapshotStore:
    def __init__(self, snapshot: ActiveRuntimeSnapshot | None = None) -> None:
        self.snapshot = snapshot

    async def read(self, *, snapshot_name: str) -> ActiveRuntimeSnapshot | None:
        return self.snapshot

    async def compare_and_swap(self, **kwargs: object) -> bool:  # pragma: no cover
        raise NotImplementedError


class _Orchestrator:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls = 0
        self._raises = raises

    async def build_and_activate(
        self,
        *,
        schema: object,
        snapshot_name: str,
        configuration_release_id: str,
        search_index_release_id: str = "none",
    ) -> ActiveRuntimeSnapshot:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return _snapshot(
            graph_generation_id=f"gen-{uuid.uuid4().hex[:8]}",
            fingerprint=getattr(schema, "configuration_checksum", "fingerprint-1"),
            release_id=configuration_release_id,
            version=2,
        )


def _snapshot(
    *,
    graph_generation_id: str = "gen-live",
    fingerprint: str = "fingerprint-1",
    release_id: str = "release-1",
    version: int = 1,
) -> ActiveRuntimeSnapshot:
    return ActiveRuntimeSnapshot(
        snapshot_name="default",
        configuration_release_id=release_id,
        schema_fingerprint=fingerprint,
        graph_generation_id=graph_generation_id,
        search_index_release_id="none",
        activation_id=str(uuid.uuid4()),
        activation_version=version,
        activated_at=NOW,
    )


def _trigger(store: _SnapshotStore, orchestrator: _Orchestrator) -> RebuildTrigger:
    return RebuildTrigger(
        snapshot_store=store,  # type: ignore[arg-type]
        orchestrator=orchestrator,  # type: ignore[arg-type]
    )


# --- the decision -----------------------------------------------------------


@pytest.mark.asyncio
async def test_no_snapshot_means_a_first_build() -> None:
    decision = await _trigger(_SnapshotStore(), _Orchestrator()).evaluate(
        schema=_Schema(),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )
    assert decision.required
    assert decision.reason is RebuildReason.NO_ACTIVE_GENERATION


@pytest.mark.asyncio
async def test_an_unchanged_schema_and_release_needs_no_rebuild() -> None:
    """The idempotence property. Called on every startup and every schedule
    tick, this must be the common answer."""
    store = _SnapshotStore(_snapshot())
    decision = await _trigger(store, _Orchestrator()).evaluate(
        schema=_Schema("fingerprint-1"),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )
    assert not decision.required
    assert decision.reason is None


@pytest.mark.asyncio
async def test_a_changed_schema_fingerprint_triggers_a_rebuild() -> None:
    store = _SnapshotStore(_snapshot(fingerprint="fingerprint-old"))
    decision = await _trigger(store, _Orchestrator()).evaluate(
        schema=_Schema("fingerprint-new"),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )
    assert decision.required
    assert decision.reason is RebuildReason.SCHEMA_FINGERPRINT_CHANGED
    # The detail names both sides, so the log answers "why" on its own.
    assert "fingerprint-old" in decision.detail and "fingerprint-new" in decision.detail


@pytest.mark.asyncio
async def test_a_new_release_with_identical_schema_bytes_still_rebuilds() -> None:
    """Two releases can carry byte-identical schemas -- the fingerprint alone
    would call that unchanged -- but the snapshot pins the release id for audit,
    so it still has to cut over."""
    store = _SnapshotStore(_snapshot(release_id="release-1"))
    decision = await _trigger(store, _Orchestrator()).evaluate(
        schema=_Schema("fingerprint-1"),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-2",
    )
    assert decision.required
    assert decision.reason is RebuildReason.CONFIGURATION_RELEASE_CHANGED


@pytest.mark.asyncio
async def test_force_reports_what_it_is_replacing() -> None:
    """Checked after reading the snapshot rather than short-circuiting, so a
    forced rebuild still says which generation it displaces."""
    store = _SnapshotStore(_snapshot(graph_generation_id="gen-live"))
    decision = await _trigger(store, _Orchestrator()).evaluate(
        schema=_Schema(),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
        force=True,
    )
    assert decision.required
    assert decision.reason is RebuildReason.FORCED
    assert "gen-live" in decision.detail


# --- acting on it -----------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_current_does_nothing_when_nothing_changed() -> None:
    orchestrator = _Orchestrator()
    result = await _trigger(_SnapshotStore(_snapshot()), orchestrator).ensure_current(
        schema=_Schema("fingerprint-1"),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )
    assert result is None
    assert orchestrator.calls == 0


@pytest.mark.asyncio
async def test_ensure_current_rebuilds_when_the_schema_changed() -> None:
    orchestrator = _Orchestrator()
    result = await _trigger(
        _SnapshotStore(_snapshot(fingerprint="fingerprint-old")), orchestrator
    ).ensure_current(
        schema=_Schema("fingerprint-new"),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )
    assert result is not None
    assert result.schema_fingerprint == "fingerprint-new"
    assert orchestrator.calls == 1


@pytest.mark.asyncio
async def test_a_rebuild_already_running_elsewhere_is_not_an_error() -> None:
    """Two replicas calling this on startup is the expected case. Raising would
    make a healthy deployment look like a failing one."""
    orchestrator = _Orchestrator(raises=ActivationError("busy", stage="ACQUIRE_REBUILD_LEASE"))
    result = await _trigger(_SnapshotStore(), orchestrator).ensure_current(
        schema=_Schema(),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )
    assert result is None
    assert orchestrator.calls == 1


@pytest.mark.asyncio
async def test_every_other_activation_failure_still_surfaces() -> None:
    """The counterpart to the test above: swallowing a validation failure would
    make a broken rebuild indistinguishable from a busy one, and nobody would
    ever learn the graph could not be rebuilt."""
    orchestrator = _Orchestrator(raises=ActivationError("bad graph", stage="VALIDATE"))
    with pytest.raises(ActivationError) as caught:
        await _trigger(_SnapshotStore(), orchestrator).ensure_current(
            schema=_Schema(),  # type: ignore[arg-type]
            snapshot_name="default",
            configuration_release_id="release-1",
        )
    assert caught.value.stage == "VALIDATE"
