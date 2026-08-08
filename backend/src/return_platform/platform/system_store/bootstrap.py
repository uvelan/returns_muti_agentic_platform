"""Winner/waiter/takeover startup algorithm (Slice 3R.2, 3R.8):

    compute manifest_fingerprint
    try:
        acquire FENCED lease (token T) + start heartbeat
        mark bootstrap-state RUNNING (fenced)
        for each configured structure:
            persistent fence check                              <- guarded on token T
            inspect
            if missing: create                                  <- idempotent
            in-memory heartbeat-health check
            create required indexes                              <- idempotent
            persistent fence check again                        <- guarded on token T
            re-inspect actual structure/index -> physical_identity
            index drift: FAIL or WARN per configured policy, never auto-repair
            apply pending forward migration                      <- guarded on token T
            record schema version                                <- guarded on token T
        mark bootstrap-state COMPLETE (fenced)
        release lease (always, via finally)
    except LeaseUnavailable:
        wait: bounded backoff + jitter, inspect durable bootstrap-state for the exact
        manifest_fingerprint; COMPLETE -> re-validate structures and return without
        migrating; owner lease expired -> retry (may become the new owner); deadline
        elapsed -> SystemStoreBootstrapTimeout

`COMPLETE` means the entire manifest finished, never inferred from partial
schema-version progress -- a waiter that only checked schema versions could wrongly
treat an owner's in-progress bootstrap as done partway through.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping, Sequence
from typing import Protocol

from return_platform.platform.system_store.contracts import (
    BootstrapState,
    BootstrapStatus,
    CompatibilityStatus,
    StructureDefinition,
    StructureIdentity,
    SystemStoreAdapter,
    SystemStoreBootstrapReport,
    compute_manifest_fingerprint,
    compute_structure_fingerprint,
)
from return_platform.platform.system_store.locking import (
    FencedLease,
    FencedLeaseManager,
    LeaseStore,
    LeaseUnavailable,
)
from return_platform.platform.system_store.migrations import Migration, MigrationRunner


class MissingSystemStoreStructure(RuntimeError):
    """A declared structure does not exist and auto_bootstrap_missing_structures is off."""


class IndexDriftDetected(RuntimeError):
    """A same-named index exists with a different canonical definition than declared,
    and `fail_closed_on_drift` is True. Never auto-repaired -- see the target design's
    system-store drift policy."""


class StructureVanishedDuringBootstrap(RuntimeError):
    """Defensive: a structure just created/verified is no longer PRESENT on
    re-inspection. Should be unreachable under correct fencing; raised rather than
    silently continuing if it ever is."""


class SystemStoreBootstrapTimeout(RuntimeError):
    """A bootstrap waiter's deadline elapsed before the manifest reached COMPLETE and no
    takeover became possible (the owner's lease never expired)."""


class FenceVerifier(Protocol):
    async def verify_fence(self, lease: FencedLease) -> None: ...


class BootstrapStateStore(Protocol):
    async def read(self, manifest_fingerprint: str) -> BootstrapState | None: ...

    async def mark_running(self, manifest_fingerprint: str, lease: FencedLease) -> None: ...

    async def mark_complete(self, manifest_fingerprint: str, lease: FencedLease) -> None: ...

    async def mark_failed(
        self, manifest_fingerprint: str, lease: FencedLease, failure_code: str
    ) -> None: ...


class SystemStoreBootstrapper:
    def __init__(
        self,
        *,
        lease_store: LeaseStore,
        adapter: SystemStoreAdapter,
        migration_runner: MigrationRunner,
        bootstrap_state: BootstrapStateStore,
        guard: FenceVerifier,
        owner_instance_id: str,
        lock_name: str = "system_store_bootstrap",
        lease_ttl_seconds: float = 30.0,
        fail_closed_on_drift: bool = True,
        waiter_deadline_seconds: float = 60.0,
        waiter_base_delay_seconds: float = 0.2,
        waiter_max_delay_seconds: float = 5.0,
    ) -> None:
        self._lease_store = lease_store
        self._adapter = adapter
        self._migration_runner = migration_runner
        self._bootstrap_state = bootstrap_state
        self._guard = guard
        self._owner_instance_id = owner_instance_id
        self._lock_name = lock_name
        self._lease_ttl_seconds = lease_ttl_seconds
        self._fail_closed_on_drift = fail_closed_on_drift
        self._waiter_deadline_seconds = waiter_deadline_seconds
        self._waiter_base_delay_seconds = waiter_base_delay_seconds
        self._waiter_max_delay_seconds = waiter_max_delay_seconds

    async def bootstrap(
        self,
        structures: Sequence[StructureDefinition],
        *,
        auto_bootstrap_missing: bool = True,
        migrations_by_structure: Mapping[str, Sequence[Migration]] | None = None,
    ) -> SystemStoreBootstrapReport:
        manifest_fingerprint = compute_manifest_fingerprint(structures)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._waiter_deadline_seconds
        attempt = 0

        while True:
            attempt += 1
            try:
                return await self._bootstrap_as_owner(
                    manifest_fingerprint,
                    structures,
                    auto_bootstrap_missing=auto_bootstrap_missing,
                    migrations_by_structure=migrations_by_structure,
                )
            except LeaseUnavailable:
                pass

            state = await self._bootstrap_state.read(manifest_fingerprint)
            if state is not None and state.status is BootstrapStatus.COMPLETE:
                await self._validate_resulting_structures(structures)
                return SystemStoreBootstrapReport(
                    created_structures=(),
                    existing_structures=tuple(d.logical_name for d in structures),
                    created_indexes=(),
                    migrated_structures=(),
                )

            if loop.time() >= deadline:
                raise SystemStoreBootstrapTimeout(
                    f"Timed out waiting for bootstrap of manifest '{manifest_fingerprint}' "
                    f"to complete"
                )

            delay = min(
                self._waiter_max_delay_seconds,
                self._waiter_base_delay_seconds * (2 ** (attempt - 1)),
            )
            await asyncio.sleep(random.uniform(0, delay))

    async def _bootstrap_as_owner(
        self,
        manifest_fingerprint: str,
        structures: Sequence[StructureDefinition],
        *,
        auto_bootstrap_missing: bool,
        migrations_by_structure: Mapping[str, Sequence[Migration]] | None,
    ) -> SystemStoreBootstrapReport:
        migrations_by_structure = migrations_by_structure or {}
        created: list[str] = []
        existing: list[str] = []
        created_indexes: list[str] = []
        migrated: list[str] = []

        async with FencedLeaseManager(
            self._lease_store,
            self._lock_name,
            owner_instance_id=self._owner_instance_id,
            ttl_seconds=self._lease_ttl_seconds,
        ) as lease_manager:
            await self._bootstrap_state.mark_running(
                manifest_fingerprint, lease_manager.current_lease
            )
            try:
                for definition in structures:
                    identity, was_created, index_created = await self._bootstrap_one_structure(
                        definition,
                        lease_manager,
                        auto_bootstrap_missing=auto_bootstrap_missing,
                    )
                    (created if was_created else existing).append(definition.logical_name)
                    created_indexes.extend(index_created)

                    pending_migrations = migrations_by_structure.get(definition.logical_name, ())
                    if pending_migrations:
                        applied = await self._migration_runner.apply_pending(
                            identity, definition.schema_version, pending_migrations, lease_manager
                        )
                        if applied:
                            migrated.append(definition.logical_name)
            except Exception as exc:
                try:
                    await self._bootstrap_state.mark_failed(
                        manifest_fingerprint, lease_manager.current_lease, type(exc).__name__
                    )
                except Exception:
                    pass  # lease already lost -- nothing authoritative left to record
                raise

            await self._bootstrap_state.mark_complete(
                manifest_fingerprint, lease_manager.current_lease
            )

        return SystemStoreBootstrapReport(
            created_structures=tuple(created),
            existing_structures=tuple(existing),
            created_indexes=tuple(created_indexes),
            migrated_structures=tuple(migrated),
        )

    async def _bootstrap_one_structure(
        self,
        definition: StructureDefinition,
        lease_manager: FencedLeaseManager,
        *,
        auto_bootstrap_missing: bool,
    ) -> tuple[StructureIdentity, bool, tuple[str, ...]]:
        """Returns (identity, was_created, created_index_names)."""
        lease_manager.ensure_alive()
        await self._guard.verify_fence(lease_manager.current_lease)

        inspection = await self._adapter.inspect_structure(definition)
        was_created = False
        if inspection.status is CompatibilityStatus.MISSING:
            if not auto_bootstrap_missing:
                raise MissingSystemStoreStructure(
                    f"Structure '{definition.logical_name}' (physical "
                    f"'{definition.physical_name}') is missing and "
                    f"auto_bootstrap_missing_structures is disabled"
                )
            await self._adapter.create_structure(definition)
            was_created = True

        lease_manager.ensure_alive()
        index_result = await self._adapter.ensure_indexes(definition)

        await self._guard.verify_fence(lease_manager.current_lease)
        reinspection = await self._adapter.inspect_structure(definition)
        if (
            reinspection.status is not CompatibilityStatus.PRESENT
            or reinspection.physical_identity is None
        ):
            raise StructureVanishedDuringBootstrap(
                f"Structure '{definition.logical_name}' was created/verified but is not "
                f"PRESENT on re-inspection"
            )

        if index_result.drifted:
            if self._fail_closed_on_drift:
                names = ", ".join(report.index_name for report in index_result.drifted)
                raise IndexDriftDetected(
                    f"Structure '{definition.logical_name}' has drifted index "
                    f"definition(s) for: {names}"
                )

        identity = StructureIdentity(
            logical_name=definition.logical_name,
            physical_name=definition.physical_name,
            physical_identity=reinspection.physical_identity,
            structure_fingerprint=compute_structure_fingerprint(definition),
        )
        return identity, was_created, index_result.created

    async def _validate_resulting_structures(
        self, structures: Sequence[StructureDefinition]
    ) -> None:
        for definition in structures:
            inspection = await self._adapter.inspect_structure(definition)
            if inspection.status is not CompatibilityStatus.PRESENT:
                raise SystemStoreBootstrapTimeout(
                    f"Bootstrap state reports COMPLETE but structure "
                    f"'{definition.logical_name}' is not present"
                )
