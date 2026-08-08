"""Forward-only schema migration for system-store structures (design doc §13.7).

Migrations only ever move a structure's recorded schema version forward. Each
`Migration.apply()` must be either transaction-wrapped or independently idempotent: the
runner does not distinguish "never ran" from "ran but crashed before its version was
recorded" -- both look like "current version is behind target," so a migration must be
safe to re-run from the same starting state. `record_version` is a fenced write, so a
migration that finishes after its lease was superseded (a paused-then-resumed stale
holder) fails to record -- the next holder sees the version still behind and reapplies,
which is only safe because `apply()` is required to be idempotent in the first place.

`MigrationPathValidator` is the single owner of every forward-only invariant --
downgrade rejection, no-op on equality, exact contiguous forward path, duplicate/gap
detection -- so `MigrationRunner` never has to (and never did correctly: an earlier cut
of `apply_pending` applied every migration with `target_version > current`, which
silently accepted a path with a gap, e.g. current=1/target=4/available=[2,4] applying
just [2, 4] and reporting success despite skipping 3).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from return_platform.platform.system_store.contracts import StructureIdentity
from return_platform.platform.system_store.locking import FencedLease, FencedLeaseManager


class Migration(Protocol):
    target_version: int

    async def apply(self, lease: FencedLease) -> None: ...


class VersionLedger(Protocol):
    async def current_version(self, identity: StructureIdentity) -> int: ...

    async def record_version(
        self, identity: StructureIdentity, version: int, lease: FencedLease
    ) -> None: ...


class MigrationDowngradeUnsupported(RuntimeError):
    """The recorded version is ahead of the requested target. Downgrade is never
    performed automatically -- this always indicates a configuration or deployment
    error (an older manifest applied after a newer one already ran)."""


class MigrationPathInvalid(RuntimeError):
    """The available migrations cannot form an exact, contiguous forward path from the
    recorded version to the target: a duplicate target_version, a missing intermediate
    version, or (defensively) a final applied version that does not equal the target."""


class MigrationPathValidator:
    """Pure. Computes the exact ordered migration path from `current` to `target`, or
    raises. Never interprets "some migrations in range" as a valid path."""

    def resolve_path(
        self,
        *,
        current: int,
        target: int,
        migrations: Sequence[Migration],
    ) -> tuple[Migration, ...]:
        if current > target:
            raise MigrationDowngradeUnsupported(
                f"recorded version {current} is ahead of target {target}; "
                f"downgrade is not supported"
            )
        if current == target:
            return ()

        relevant = [m for m in migrations if current < m.target_version <= target]

        seen_versions: dict[int, int] = {}
        for migration in relevant:
            seen_versions[migration.target_version] = (
                seen_versions.get(migration.target_version, 0) + 1
            )
        duplicates = sorted(v for v, count in seen_versions.items() if count > 1)
        if duplicates:
            raise MigrationPathInvalid(
                f"duplicate migration(s) declared for version(s) {duplicates}"
            )

        expected_versions = set(range(current + 1, target + 1))
        available_versions = set(seen_versions.keys())
        missing = sorted(expected_versions - available_versions)
        if missing:
            raise MigrationPathInvalid(
                f"missing intermediate migration(s) for version(s) {missing}; "
                f"cannot reach target {target} from {current}"
            )

        return tuple(sorted(relevant, key=lambda migration: migration.target_version))


class MigrationRunner:
    def __init__(
        self, ledger: VersionLedger, validator: MigrationPathValidator | None = None
    ) -> None:
        self._ledger = ledger
        self._validator = validator or MigrationPathValidator()

    async def apply_pending(
        self,
        identity: StructureIdentity,
        target_version: int,
        migrations: Sequence[Migration],
        lease_manager: FencedLeaseManager,
    ) -> tuple[int, ...]:
        """Apply the exact forward migration path from the recorded version to
        `target_version`, checking the lease is still alive before each step. Raises
        `MigrationDowngradeUnsupported`/`MigrationPathInvalid` before applying anything
        if the path cannot be resolved. After applying, verifies the ledger's recorded
        version actually equals `target_version` -- a defensive check against a runner
        or ledger defect silently under-applying.

        `identity` binds the recorded version to the structure's current physical
        identity (Slice 3R.4): if the physical collection was replaced, the ledger
        reports version 0 regardless of what was previously recorded, so a rename or
        drop+recreate is treated as a fresh structure, never as "already migrated."
        """
        current = await self._ledger.current_version(identity)
        path = self._validator.resolve_path(
            current=current, target=target_version, migrations=migrations
        )
        if not path:
            return ()

        applied: list[int] = []
        for migration in path:
            lease_manager.ensure_alive()
            await migration.apply(lease_manager.current_lease)
            await self._ledger.record_version(
                identity, migration.target_version, lease_manager.current_lease
            )
            applied.append(migration.target_version)

        final_version = await self._ledger.current_version(identity)
        if final_version != target_version:
            raise MigrationPathInvalid(
                f"applied migrations {applied} but recorded version is {final_version}, "
                f"expected {target_version}"
            )
        return tuple(applied)
