"""Forward-only schema migration for system-store structures (design doc §13.7).

Migrations only ever move a structure's recorded schema version forward. Each
`Migration.apply()` must be either transaction-wrapped or independently idempotent: the
runner does not distinguish "never ran" from "ran but crashed before its version was
recorded" -- both look like "current version is behind target," so a migration must be
safe to re-run from the same starting state. `record_version` is a fenced write, so a
migration that finishes after its lease was superseded (a paused-then-resumed stale
holder) fails to record -- the next holder sees the version still behind and reapplies,
which is only safe because `apply()` is required to be idempotent in the first place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from return_platform.platform.system_store.locking import FencedLease, FencedLeaseManager


class Migration(Protocol):
    target_version: int

    async def apply(self, lease: FencedLease) -> None: ...


class VersionLedger(Protocol):
    async def current_version(self, logical_name: str) -> int: ...

    async def record_version(self, logical_name: str, version: int, lease: FencedLease) -> None: ...


class MigrationRunner:
    def __init__(self, ledger: VersionLedger) -> None:
        self._ledger = ledger

    async def apply_pending(
        self,
        logical_name: str,
        target_version: int,
        migrations: Sequence[Migration],
        lease_manager: FencedLeaseManager,
    ) -> tuple[int, ...]:
        """Apply every migration whose target_version exceeds the recorded version, in
        ascending order, checking the lease is still alive before each one. Returns the
        versions actually applied (empty if already at or past target_version)."""
        current = await self._ledger.current_version(logical_name)
        if current >= target_version:
            return ()

        pending = sorted(
            (
                migration
                for migration in migrations
                if current < migration.target_version <= target_version
            ),
            key=lambda migration: migration.target_version,
        )
        applied: list[int] = []
        for migration in pending:
            lease_manager.ensure_alive()
            await migration.apply(lease_manager.current_lease)
            await self._ledger.record_version(
                logical_name, migration.target_version, lease_manager.current_lease
            )
            applied.append(migration.target_version)
        return tuple(applied)
