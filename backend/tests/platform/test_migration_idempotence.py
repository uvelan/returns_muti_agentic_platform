"""MigrationRunner: forward-only, ascending order, and safe to re-run a migration whose
apply() already ran but whose version was never recorded -- the runner cannot tell "never
ran" apart from "ran but crashed before recording," so it always re-applies from the
recorded version, and correctness rests on each Migration.apply() being idempotent.

Also: MigrationPathValidator (Slice 3R.3) is the single owner of every forward-only
invariant -- downgrade rejection, no-op on equality, exact contiguous forward path,
duplicate/gap detection. An earlier cut of `apply_pending` applied every migration with
`target_version > current` unconditionally, which silently accepted a path with a gap
(current=1, target=4, available=[2, 4] applying just [2, 4] and reporting success
despite skipping 3) -- these tests exist specifically to catch that class of defect."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from return_platform.platform.system_store.contracts import StructureIdentity
from return_platform.platform.system_store.locking import FencedLease
from return_platform.platform.system_store.migrations import (
    MigrationDowngradeUnsupported,
    MigrationPathInvalid,
    MigrationPathValidator,
    MigrationRunner,
)


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


def _identity(logical_name: str = "structure_a") -> StructureIdentity:
    return StructureIdentity(
        logical_name=logical_name,
        physical_name=f"{logical_name}_physical",
        physical_identity="identity-1",
        structure_fingerprint="fingerprint-1",
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

    async def current_version(self, identity: StructureIdentity) -> int:
        return self.version

    async def record_version(
        self, identity: StructureIdentity, version: int, lease: FencedLease
    ) -> None:
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
        _identity(), target_version=2, migrations=migrations, lease_manager=lease_manager
    )

    assert applied == (1, 2)
    assert applied_order == ["v1", "v2"]
    assert ledger.version == 2
    assert lease_manager.ensure_alive_calls == 2


@pytest.mark.asyncio
async def test_no_migration_runs_when_already_at_target_version() -> None:
    """current == target is a no-op. (current > target is a stricter case -- a
    downgrade -- and is a hard failure, not a silent no-op; see
    test_downgrade_is_rejected.)"""
    ledger = _FakeVersionLedger(initial=3)
    runner = MigrationRunner(ledger)
    lease_manager = _FakeLeaseManager(_lease())
    tags: set[str] = set()
    migrations = [_IdempotentAppendMigration(target_version=3, tag="v3", applied_to=tags)]

    applied = await runner.apply_pending(
        _identity(), target_version=3, migrations=migrations, lease_manager=lease_manager
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
            _identity(), target_version=1, migrations=[migration], lease_manager=lease_manager
        )

    # Version was never recorded -- the next bootstrap attempt sees version 0 again.
    assert ledger.version == 0

    # Re-run: apply() runs a second time, but the effect is not duplicated because the
    # migration itself is idempotent (a set, not an append-only log).
    applied = await runner.apply_pending(
        _identity(), target_version=1, migrations=[migration], lease_manager=lease_manager
    )

    assert applied == (1,)
    assert ledger.version == 1
    assert tags == {"v1"}


# ---------------------------------------------------------------------------
# MigrationPathValidator -- pure invariants (Slice 3R.3)
# ---------------------------------------------------------------------------


def _migration(target_version: int) -> _IdempotentAppendMigration:
    return _IdempotentAppendMigration(
        target_version=target_version, tag=str(target_version), applied_to=set()
    )


def test_downgrade_is_rejected() -> None:
    validator = MigrationPathValidator()
    with pytest.raises(MigrationDowngradeUnsupported):
        validator.resolve_path(current=5, target=3, migrations=[_migration(4)])


def test_equal_current_and_target_is_a_no_op() -> None:
    validator = MigrationPathValidator()
    assert validator.resolve_path(current=3, target=3, migrations=[_migration(4)]) == ()


def test_missing_intermediate_migration_is_rejected() -> None:
    """current=1, target=4, available=[2, 4] -- migration 3 is missing. Must never be
    interpreted as "some migrations in range are enough."""
    validator = MigrationPathValidator()
    with pytest.raises(MigrationPathInvalid, match="missing intermediate"):
        validator.resolve_path(current=1, target=4, migrations=[_migration(2), _migration(4)])


def test_duplicate_migration_for_the_same_version_is_rejected() -> None:
    validator = MigrationPathValidator()
    with pytest.raises(MigrationPathInvalid, match="duplicate"):
        validator.resolve_path(
            current=0, target=2, migrations=[_migration(1), _migration(2), _migration(2)]
        )


def test_exact_forward_path_is_accepted_in_ascending_order() -> None:
    validator = MigrationPathValidator()
    path = validator.resolve_path(
        current=1, target=3, migrations=[_migration(3), _migration(2), _migration(5)]
    )
    assert [m.target_version for m in path] == [2, 3]


@pytest.mark.asyncio
async def test_runner_rejects_downgrade_before_applying_anything() -> None:
    ledger = _FakeVersionLedger(initial=5)
    runner = MigrationRunner(ledger)
    lease_manager = _FakeLeaseManager(_lease())
    tags: set[str] = set()
    migration = _IdempotentAppendMigration(target_version=4, tag="v4", applied_to=tags)

    with pytest.raises(MigrationDowngradeUnsupported):
        await runner.apply_pending(
            _identity(), target_version=3, migrations=[migration], lease_manager=lease_manager
        )

    assert "v4" not in tags
    assert ledger.version == 5
    assert lease_manager.ensure_alive_calls == 0


@pytest.mark.asyncio
async def test_runner_rejects_a_migration_gap_before_applying_anything() -> None:
    ledger = _FakeVersionLedger(initial=1)
    runner = MigrationRunner(ledger)
    lease_manager = _FakeLeaseManager(_lease())
    tags: set[str] = set()
    migrations = [
        _IdempotentAppendMigration(target_version=2, tag="v2", applied_to=tags),
        _IdempotentAppendMigration(target_version=4, tag="v4", applied_to=tags),
    ]

    with pytest.raises(MigrationPathInvalid, match="missing intermediate"):
        await runner.apply_pending(
            _identity(), target_version=4, migrations=migrations, lease_manager=lease_manager
        )

    assert tags == set()
    assert ledger.version == 1
    assert lease_manager.ensure_alive_calls == 0


@pytest.mark.asyncio
async def test_runner_reports_final_version_mismatch_if_ledger_under_applies() -> None:
    """Defensive: if the ledger's recorded version doesn't end up matching the target
    despite every migration in the resolved path having been applied, the runner must
    not silently report success."""

    class _LyingLedger(_FakeVersionLedger):
        async def record_version(
            self, identity: StructureIdentity, version: int, lease: FencedLease
        ) -> None:
            # Pretend to record, but never actually advance the version.
            self.record_calls += 1

    ledger = _LyingLedger(initial=0)
    runner = MigrationRunner(ledger)
    lease_manager = _FakeLeaseManager(_lease())
    tags: set[str] = set()
    migration = _IdempotentAppendMigration(target_version=1, tag="v1", applied_to=tags)

    with pytest.raises(MigrationPathInvalid, match="expected 1"):
        await runner.apply_pending(
            _identity(), target_version=1, migrations=[migration], lease_manager=lease_manager
        )
