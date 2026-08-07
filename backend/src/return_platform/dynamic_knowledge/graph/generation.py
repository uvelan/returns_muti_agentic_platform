"""Blue/green graph-generation lifecycle and cross-store activation records.

Platform MongoDB is authoritative for ActiveRuntimeSnapshot, ConfigurationRelease,
SyncRun/SyncCheckpoint, and the read/write drain leases below -- Neo4j owns only
the GraphGeneration marker, GraphWriteReceipt, and ProjectionOwnership. See the
source-to-graph alignment plan for the full authority table and activation
protocol; this module holds only the shared lifecycle/record types both sides
of that boundary need.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class GraphGenerationStatus(StrEnum):
    """PREPARING -> BUILDING -> CATCHING_UP -> VALIDATING -> READY_FOR_ACTIVATION -> ACTIVE -> RETIRED.

    A generation's Neo4j-side GraphGeneration marker briefly carries ACTIVE
    status *before* the MongoDB ActiveRuntimeSnapshot compare-and-swap during
    cutover -- that overlap is expected, not a bug. ACTIVE status alone never
    makes a generation eligible to serve traffic; only being referenced by
    ActiveRuntimeSnapshot does.
    """

    PREPARING = "PREPARING"
    BUILDING = "BUILDING"
    CATCHING_UP = "CATCHING_UP"
    VALIDATING = "VALIDATING"
    READY_FOR_ACTIVATION = "READY_FOR_ACTIVATION"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


class ActiveRuntimeSnapshot(BaseModel):
    """The one atomic pointer every request resolves to determine what serves it.

    Every request resolves this exactly once and pins schema_fingerprint /
    graph_generation_id / search_index_release_id for its own lifetime --
    never re-resolved mid-request.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_name: str
    configuration_release_id: str
    schema_fingerprint: str
    graph_generation_id: str
    search_index_release_id: str
    activation_id: str
    activation_version: int = Field(ge=1)
    activated_at: datetime


class GenerationReadLease(BaseModel):
    """A short-TTL lease a request holds while pinned to a graph generation.

    Cleanup of a RETIRED generation waits for every read lease (and every
    GenerationWriteReservation) referencing it to drain or expire -- a
    crashed request process must not block retirement forever, hence the TTL.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lease_id: str
    graph_generation_id: str
    snapshot_activation_version: int
    owner_instance_id: str
    acquired_at: datetime
    expires_at: datetime


class ConfigurationRelease(BaseModel):
    """One approved, immutable configuration/schema release. A generation is
    always built from exactly one release; a release may back at most one
    live generation build at a time (see RebuildLease)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    configuration_release_id: str
    schema_fingerprint: str
    approved_by: str
    approved_at: datetime
    release_status: str = "ACTIVE"


class RebuildLease(BaseModel):
    """Prevents two concurrent rebuilds from racing to build/activate a
    generation for the same snapshot_name. Short-TTL, renewed while the
    build is in progress; a crashed rebuild process's lease simply expires
    rather than blocking the next rebuild attempt forever."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lease_id: str
    snapshot_name: str
    graph_generation_id: str
    owner_instance_id: str
    acquired_at: datetime
    expires_at: datetime


class GenerationWriteReservation(BaseModel):
    """Same drain requirement as GenerationReadLease, for in-flight on-demand writes.

    On-demand sync *writes* to the graph, so a read-drain alone is not
    sufficient before retiring a generation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reservation_id: str
    graph_generation_id: str
    snapshot_activation_version: int
    owner_instance_id: str
    acquired_at: datetime
    expires_at: datetime


class GraphWriteReceipt(BaseModel):
    """Idempotency receipt for one (sync_run_id, chunk_id) graph write.

    Same run+chunk+checksum replays this receipt without re-executing. Same
    run+chunk with a different checksum must be rejected by the writer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sync_run_id: str
    chunk_id: str
    payload_checksum: str
    graph_generation_id: str
    committed_at: datetime
    nodes_written: int = 0
    relationships_written: int = 0


class ProjectionOwnership(BaseModel):
    """Neo4j-resident record: one parent source document's claim on one
    projected business node, for entities configured with an OwnershipPolicy
    (see schema.py). Conceptually (ProjectionOwnership)-[:OWNS]->(business node).

    entity_key_hash is a canonical hash of the owned node's key_values --
    not a security hash, just a normalized single comparable value so
    ownership records work uniformly regardless of a key's field shape
    (single field or composite).

    A business node with zero remaining ProjectionOwnership records (from
    any source document) is the trigger for applying its EntityDeletionPolicy
    -- see Neo4jDynamicGraphWriter.reconcile_child_ownership.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_generation_id: str
    projection_id: str
    source_asset_id: str
    source_identity: str
    entity_id: str
    entity_key_hash: str
    source_version: str | None = None
    identity_strategy: str | None = None
    identity_key_version: int | None = None
