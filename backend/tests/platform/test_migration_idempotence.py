"""MigrationRunner: forward-only, ascending order, and safe to re-run a migration whose
apply() already ran but whose version was never recorded -- the runner cannot tell "never
ran" apart from "ran but crashed before recording," so it always re-applies from the
recorded version, and correctness rests on each Migration.apply() being idempotent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from return_platform.platform.system_store.locking import FencedLease
from return_platform.platform.system_store.migrations import MigrationRunner


def _lease(fencing_token: int = 1) -> FencedLease:
    now = datetime.now(UTC)
    return FencedLease(
        lock_name="lock",
        lease_id="lease-1",
        owner_instance_id="owner",
        fencing_token=fencing_token,
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now,
    )


class _FakeLeaseManager:
    """Stands in for FencedLeaseManager without needing a real acquired lease."""

    def __init__(self, lease: FencedLease) -> None:
        self._lease = lease
        self.ensure_alive_calls = 0

    def ensure_alive(self) -> None:
        self.ensure_alive_calls += 1

    @property
    def current_lease(self) -> FencedLease:
        return self._lease


class _FakeVersionLedger:
    def __init__(self, initial: int = 0) -> None:
        self.version = initial
        self.record_calls = 0
        self.fail_next_record = False

    async def current_version(self, logical_name: str) -> int:
        return self.version

    async def record_version(self, logical_name: str, version: int, lease: FencedLease) -> None:
        self.record_calls += 1
        if self.fail_next_record:
            self.fail_next_record = False
            raise RuntimeError("simulated crash before recording")
        self.version = version


@dataclass
class _IdempotentAppendMigration:
    """Idempotent by construction: applying it twice leaves the target list with the
    tag present exactly once, exactly what a real migration must guarantee."""

    target_version: int
    tag: str
    applied_to: set[str]
    applied_order: list[str] | None = None

    async def apply(self, lease: FencedLease) -> None:
        if self.applied_order is not None:
            self.applied_order.append(self.tag)
        self.applied_to.add(self.tag)


@pytest.mark.asyncio
async def test_migrations_apply_in_ascending_order_and_stop_at_target() -> None:
    ledger = _FakeVersionLedger(initial=0)
    runner = MigrationRunner(ledger)
    lease_manager = _FakeLeaseManager(_lease())
    applied_order: list[str] = []
    tags: set[str] = set()

    migrations = [
        _IdempotentAppendMigration(
            target_version=2, tag="v2", applied_to=tags, applied_order=applied_order
        ),
        _IdempotentAppendMigration(
            target_version=1, tag="v1", applied_to=tags, applied_order=applied_order
        ),
        _IdempotentAppendMigration(
            target_version=3, tag="v3", applied_to=tags, applied_order=applied_order
        ),
    ]

    applied = await runner.apply_pending(
        "structure_a", target_version=2, migrations=migrations, lease_manager=lease_manager
    )

    assert applied == (1, 2)
    assert applied_order == ["v1", "v2"]
    assert ledger.version == 2
    assert lease_manager.ensure_alive_calls == 2


@pytest.mark.asyncio
async def test_no_migration_runs_when_already_at_or_past_target_version() -> None:
    ledger = _FakeVersionLedger(initial=5)
    runner = MigrationRunner(ledger)
    lease_manager = _FakeLeaseManager(_lease())
    tags: set[str] = set()
    migrations = [_IdempotentAppendMigration(target_version=3, tag="v3", applied_to=tags)]

    applied = await runner.apply_pending(
        "structure_a", target_version=3, migrations=migrations, lease_manager=lease_manager
    )

    assert applied == ()
    assert "v3" not in tags
    assert lease_manager.ensure_alive_calls == 0


@pytest.mark.asyncio
async def test_migration_is_reapplied_after_a_crash_before_its_version_was_recorded() -> None:
    """The runner cannot distinguish 'apply() never ran' from 'apply() ran but the
    process died before record_version()' -- both look like 'current version is behind
    target.' A re-run must call apply() again; only the migration's own idempotence
    keeps that safe, which is exactly what this test proves by using an
    idempotent-by-construction migration and checking the visible effect is not
    duplicated."""
    ledger = _FakeVersionLedger(initial=0)
    ledger.fail_next_record = True
    runner = MigrationRunner(ledger)
    lease_manager = _FakeLeaseManager(_lease())
    tags: set[str] = set()
    migration = _IdempotentAppendMigration(target_version=1, tag="v1", applied_to=tags)

    with pytest.raises(RuntimeError, match="simulated crash"):
        await runner.apply_pending(
            "structure_a", target_version=1, migrations=[migration], lease_manager=lease_manager
        )

    # Version was never recorded -- the next bootstrap attempt sees version 0 again.
    assert ledger.version == 0

    # Re-run: apply() runs a second time, but the effect is not duplicated because the
    # migration itself is idempotent (a set, not an append-only log).
    applied = await runner.apply_pending(
        "structure_a", target_version=1, migrations=[migration], lease_manager=lease_manager
    )

    assert applied == (1,)
    assert ledger.version == 1
    assert tags == {"v1"}
