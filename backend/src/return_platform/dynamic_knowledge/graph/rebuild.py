"""Exclusive graph-generation rebuild state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.fingerprint import graph_schema_fingerprint
from return_platform.dynamic_knowledge.schema import ActiveSchema


class GraphStatus(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    ACTIVE = "ACTIVE"
    SCHEMA_CHANGE_DETECTED = "SCHEMA_CHANGE_DETECTED"
    REBUILD_PENDING = "REBUILD_PENDING"
    REBUILDING = "REBUILDING"
    VALIDATING = "VALIDATING"
    FAILED = "FAILED"


class GraphProjectionState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: GraphStatus
    schema_version: str | None = None
    schema_fingerprint: str | None = None
    graph_generation_id: str | None = None
    configuration_release_id: str | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class FencedLease:
    lease_name: str
    owner_id: str
    fencing_token: int


class GraphLifecycleStore(Protocol):
    async def read_state(self) -> GraphProjectionState: ...
    async def write_state(self, state: GraphProjectionState, *, fencing_token: int | None = None) -> None: ...
    async def acquire_rebuild_lease(self, *, owner_id: str, ttl_seconds: int) -> FencedLease: ...
    async def release_rebuild_lease(self, lease: FencedLease) -> None: ...


class BusinessGraphAdmin(Protocol):
    async def drop_business_graph(self, *, database: str, fencing_token: int) -> None: ...
    async def apply_schema(self, *, schema: ActiveSchema, fencing_token: int) -> None: ...


class FullSyncRunner(Protocol):
    async def run_full_sync(self, *, schema: ActiveSchema, fencing_token: int) -> None: ...


class GraphValidator(Protocol):
    async def validate(self, *, schema: ActiveSchema, fencing_token: int) -> None: ...


class GraphRebuildCoordinator:
    """Activate a new graph generation only after destructive rebuild and validation."""

    def __init__(
        self,
        *,
        store: GraphLifecycleStore,
        graph_admin: BusinessGraphAdmin,
        full_sync: FullSyncRunner,
        validator: GraphValidator,
        lease_ttl_seconds: int = 900,
    ) -> None:
        self._store = store
        self._graph_admin = graph_admin
        self._full_sync = full_sync
        self._validator = validator
        self._lease_ttl_seconds = lease_ttl_seconds

    async def ensure_active(self, schema: ActiveSchema) -> GraphProjectionState:
        target_fingerprint = graph_schema_fingerprint(schema)
        current = await self._store.read_state()
        if (
            current.status is GraphStatus.ACTIVE
            and current.schema_fingerprint == target_fingerprint
            and current.configuration_release_id == schema.configuration_release_id
        ):
            return current

        detected = GraphProjectionState(
            status=GraphStatus.SCHEMA_CHANGE_DETECTED,
            schema_version=schema.schema_version,
            schema_fingerprint=target_fingerprint,
            configuration_release_id=schema.configuration_release_id,
        )
        await self._store.write_state(detected)
        owner_id = str(uuid4())
        lease = await self._store.acquire_rebuild_lease(
            owner_id=owner_id,
            ttl_seconds=self._lease_ttl_seconds,
        )
        generation_id = str(uuid4())
        try:
            await self._store.write_state(
                GraphProjectionState(
                    status=GraphStatus.REBUILDING,
                    schema_version=schema.schema_version,
                    schema_fingerprint=target_fingerprint,
                    graph_generation_id=generation_id,
                    configuration_release_id=schema.configuration_release_id,
                ),
                fencing_token=lease.fencing_token,
            )
            await self._graph_admin.drop_business_graph(
                database=schema.graph.database,
                fencing_token=lease.fencing_token,
            )
            await self._graph_admin.apply_schema(schema=schema, fencing_token=lease.fencing_token)
            await self._full_sync.run_full_sync(schema=schema, fencing_token=lease.fencing_token)
            await self._store.write_state(
                GraphProjectionState(
                    status=GraphStatus.VALIDATING,
                    schema_version=schema.schema_version,
                    schema_fingerprint=target_fingerprint,
                    graph_generation_id=generation_id,
                    configuration_release_id=schema.configuration_release_id,
                ),
                fencing_token=lease.fencing_token,
            )
            await self._validator.validate(schema=schema, fencing_token=lease.fencing_token)
            active = GraphProjectionState(
                status=GraphStatus.ACTIVE,
                schema_version=schema.schema_version,
                schema_fingerprint=target_fingerprint,
                graph_generation_id=generation_id,
                configuration_release_id=schema.configuration_release_id,
            )
            await self._store.write_state(active, fencing_token=lease.fencing_token)
            return active
        except Exception as exc:
            await self._store.write_state(
                GraphProjectionState(
                    status=GraphStatus.FAILED,
                    schema_version=schema.schema_version,
                    schema_fingerprint=target_fingerprint,
                    graph_generation_id=generation_id,
                    configuration_release_id=schema.configuration_release_id,
                    failure_code=type(exc).__name__,
                ),
                fencing_token=lease.fencing_token,
            )
            raise
        finally:
            await self._store.release_rebuild_lease(lease)


def require_active_graph(state: GraphProjectionState) -> str:
    """Return the generation ID or reject all agent reads during rebuild/failure."""

    if state.status is not GraphStatus.ACTIVE or state.graph_generation_id is None:
        raise RuntimeError("KNOWLEDGE_GRAPH_NOT_ACTIVE")
    return state.graph_generation_id
