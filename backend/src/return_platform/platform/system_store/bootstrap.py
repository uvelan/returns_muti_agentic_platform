"""Startup algorithm (implementation plan Phase 3):

    acquire FENCED lease (token T) + start heartbeat
    for each configured structure:
        inspect
        if missing: create; create required indexes        <- guarded on token T
        if present: reuse
        apply pending forward migration                    <- guarded on token T
        record schema version                               <- guarded on token T
    release lease (always, via finally)

Never drops a valid existing structure. `auto_bootstrap_missing` is the manifest's
`auto_bootstrap_missing_structures` flag threaded through by the caller: when false, a
missing structure is a hard failure rather than something this bootstrapper is allowed
to create on its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from return_platform.platform.system_store.contracts import (
    CompatibilityStatus,
    StructureDefinition,
    SystemStoreAdapter,
    SystemStoreBootstrapReport,
)
from return_platform.platform.system_store.locking import FencedLeaseManager, LeaseStore
from return_platform.platform.system_store.migrations import Migration, MigrationRunner


class MissingSystemStoreStructure(RuntimeError):
    """A declared structure does not exist and auto_bootstrap_missing_structures is off."""


class SystemStoreBootstrapper:
    def __init__(
        self,
        *,
        lease_store: LeaseStore,
        adapter: SystemStoreAdapter,
        migration_runner: MigrationRunner,
        owner_instance_id: str,
        lock_name: str = "system_store_bootstrap",
        lease_ttl_seconds: float = 30.0,
    ) -> None:
        self._lease_store = lease_store
        self._adapter = adapter
        self._migration_runner = migration_runner
        self._owner_instance_id = owner_instance_id
        self._lock_name = lock_name
        self._lease_ttl_seconds = lease_ttl_seconds

    async def bootstrap(
        self,
        structures: Sequence[StructureDefinition],
        *,
        auto_bootstrap_missing: bool = True,
        migrations_by_structure: Mapping[str, Sequence[Migration]] | None = None,
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
            for definition in structures:
                lease_manager.ensure_alive()
                inspection = await self._adapter.inspect_structure(definition)

                if inspection.status is CompatibilityStatus.MISSING:
                    if not auto_bootstrap_missing:
                        raise MissingSystemStoreStructure(
                            f"Structure '{definition.logical_name}' "
                            f"(physical '{definition.physical_name}') is missing and "
                            f"auto_bootstrap_missing_structures is disabled"
                        )
                    lease_manager.ensure_alive()
                    await self._adapter.create_structure(definition)
                    created.append(definition.logical_name)
                else:
                    existing.append(definition.logical_name)

                lease_manager.ensure_alive()
                created_indexes.extend(await self._adapter.ensure_indexes(definition))

                pending_migrations = migrations_by_structure.get(definition.logical_name, ())
                if pending_migrations:
                    applied = await self._migration_runner.apply_pending(
                        definition.logical_name,
                        definition.schema_version,
                        pending_migrations,
                        lease_manager,
                    )
                    if applied:
                        migrated.append(definition.logical_name)

        return SystemStoreBootstrapReport(
            created_structures=tuple(created),
            existing_structures=tuple(existing),
            created_indexes=tuple(created_indexes),
            migrated_structures=tuple(migrated),
        )
