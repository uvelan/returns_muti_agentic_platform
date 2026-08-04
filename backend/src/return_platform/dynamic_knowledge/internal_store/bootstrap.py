"""Create missing internal objects without mutating compatible existing objects."""

from __future__ import annotations

from return_platform.dynamic_knowledge.internal_store.contracts import (
    BootstrapReport,
    CompatibilityStatus,
    InternalSchemaManifest,
    InternalStoreAdapter,
)


class InternalSchemaIncompatible(RuntimeError):
    """Existing internal object does not satisfy the minimum contract."""


class InternalStoreBootstrapper:
    def __init__(self, adapter: InternalStoreAdapter) -> None:
        self._adapter = adapter

    async def bootstrap(self, manifest: InternalSchemaManifest) -> BootstrapReport:
        if manifest.connector_type is not self._adapter.connector_type:
            raise ValueError("manifest connector does not match selected internal-store adapter")
        created: list[str] = []
        existing: list[str] = []
        indexes: list[str] = []
        for definition in manifest.objects:
            inspection = await self._adapter.inspect_object(definition)
            if inspection.status is CompatibilityStatus.INCOMPATIBLE:
                reasons = "; ".join(inspection.reasons) or "unknown incompatibility"
                raise InternalSchemaIncompatible(f"{definition.name}: {reasons}")
            if inspection.status is CompatibilityStatus.MISSING:
                await self._adapter.create_object(definition)
                created.append(definition.name)
            else:
                existing.append(definition.name)
            indexes.extend(await self._adapter.ensure_indexes(definition))
        return BootstrapReport(
            connector_type=manifest.connector_type,
            created_objects=tuple(created),
            existing_objects=tuple(existing),
            created_indexes=tuple(indexes),
        )
