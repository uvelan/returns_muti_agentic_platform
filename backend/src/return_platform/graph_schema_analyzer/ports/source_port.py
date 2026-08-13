"""Read-only discovery and inspection over configured sources.

Note what these ports cannot express: there is no write, no DDL, and no free-form
query. Source systems are read-only to the analyzer as a structural fact of the
contract, not as a rule someone has to remember (design doc C3.3). Sampling is
bounded by the caller-supplied limit *and* by the source's own sampling policy on
the implementing side, so a large `limit` cannot escalate access.

`SourceDiscoveryPort` answers one question -- "describe this whole source" -- and
is what the snapshot pipeline consumes. `SourceInspectionPort` is the operator- and
model-facing surface: eight bounded questions that can be asked one at a time,
about one object at a time, so an analysis can look at a hundred-table warehouse
without reading all of it. They are separate ports because a caller granted the
second is not thereby granted the first: `discover` reads every object of a source
in one call, which is exactly the escalation the object-at-a-time surface exists to
avoid.

The result models are declared here rather than in the connector world because
this is the consumer's contract. The four backend adapters in `bootstrap/adapters/`
import these and speak them directly; the analyzer imports nothing outward, which
is what `tests/graph_schema_analyzer/test_independence.py` enforces.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DiscoveredDataset",
    "FieldDescription",
    "FieldProfile",
    "IndexDescription",
    "ObjectDescription",
    "ObjectKind",
    "ObjectProfile",
    "RelationshipKind",
    "RelationshipObservation",
    "SourceDiscoveryPort",
    "SourceInspectionPort",
    "SourceObjectRef",
    "SourceValidation",
]


class DiscoveredDataset(BaseModel):
    """What discovery found for one dataset, before the analyzer classifies it.

    `sample_rows` is transient by contract: it may be handed to the AI call and
    must pass the platform redactor (or an encrypted structure) before any
    durable write. `SourceSchemaSnapshot` is what records which of those happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    dataset_name: str
    fields: tuple[Mapping[str, Any], ...]
    approximate_row_count: int | None = None
    sample_rows: tuple[Mapping[str, Any], ...] = ()


class ObjectKind(StrEnum):
    """What a source calls the thing being described.

    Carried explicitly because the distinction changes what the analyzer may
    conclude: a VIEW has no storage statistics and usually no index, and a
    NODE_LABEL already *is* graph shape rather than something to map onto one.
    """

    COLLECTION = "COLLECTION"
    TABLE = "TABLE"
    VIEW = "VIEW"
    NODE_LABEL = "NODE_LABEL"


class RelationshipKind(StrEnum):
    """How a reported relationship is known, so a consumer can weigh it.

    FOREIGN_KEY and GRAPH_RELATIONSHIP are declared by the source and are facts.
    Nothing here is inferred: a source that declares no relationships reports
    none, because a guessed edge presented in the same shape as a declared one
    would be indistinguishable to the validation checks that treat this as fact.
    """

    FOREIGN_KEY = "FOREIGN_KEY"
    GRAPH_RELATIONSHIP = "GRAPH_RELATIONSHIP"


class SourceValidation(BaseModel):
    """The answer to "can we reach this source, and what is it".

    `reachable=False` is a *returned value*, not a raised exception, because the
    caller asking is an operator testing a connection: an unreachable source is
    the expected outcome half the time and needs a message they can act on, not a
    stack trace.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    reachable: bool
    server_version: str | None = None
    detail: str | None = None


class SourceObjectRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    object_name: str
    object_kind: ObjectKind


class FieldDescription(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str
    declared_type: str
    nullable: bool


class ObjectDescription(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    object_name: str
    object_kind: ObjectKind
    fields: tuple[FieldDescription, ...]
    approximate_row_count: int | None = None


class FieldProfile(BaseModel):
    """Per-field statistics, and never a value.

    W4.8 ranks elicitation questions by selectivity from exactly these numbers.
    That ranking is only worth having if the numbers state their own basis, which
    is why `ObjectProfile.sampled_rows` travels with them: `approximate_distinct`
    computed over 50 sampled rows is a different claim from the same number
    computed over a table.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str
    declared_type: str
    null_rate: float = Field(ge=0.0, le=1.0)
    approximate_distinct: int | None = None
    # "Could be this object's key": non-null and unique across every row seen.
    # A claim about the sample, which is why it is named a *candidate*.
    identifier_candidate: bool = False
    # "Could drive an incremental cursor": a datetime present in every row seen.
    # SQL Server sources have no fallback ordering column, so a source whose
    # profile surfaces none cannot be configured for incremental sync at all --
    # which is a thing worth learning during analysis rather than at first sync.
    change_tracking_candidate: bool = False


class ObjectProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    object_name: str
    approximate_row_count: int | None = None
    sampled_rows: int = Field(ge=0)
    fields: tuple[FieldProfile, ...] = ()


class IndexDescription(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index_name: str
    fields: tuple[str, ...]
    unique: bool = False
    primary: bool = False


class RelationshipObservation(BaseModel):
    """One relationship the source itself declares.

    Endpoints are field tuples rather than single fields because composite keys
    are real and reporting only the first column of one would produce a graph
    schema that joins on the wrong thing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    relationship_kind: RelationshipKind
    from_object: str
    from_fields: tuple[str, ...]
    to_object: str
    to_fields: tuple[str, ...]
    constraint_name: str | None = None


@runtime_checkable
class SourceDiscoveryPort(Protocol):
    """Resolved from the capability registry; implemented in bootstrap/adapters/."""

    async def list_source_ids(self) -> Sequence[str]:
        """Every source the caller is permitted to analyze."""

    async def discover(self, *, source_id: str, sample_limit: int) -> Sequence[DiscoveredDataset]:
        """Metadata for every dataset in one source, plus at most `sample_limit`
        rows per dataset when the source's policy permits sampling at all."""


@runtime_checkable
class SourceInspectionPort(Protocol):
    """The eight read-only questions, uniform across MongoDB, SQL Server,
    PostgreSQL and Neo4j.

    Adding a ninth that takes a caller-supplied query string would defeat every
    other guarantee in this file at once, so the shape of this Protocol is itself
    the control: there is nowhere to put one.

    Implementations answer for the sources they were constructed over and raise
    for anything else. They do **not** enforce the analysis's grant --
    `application/source_inspection.py` does, in one place, before any of this is
    reached.
    """

    async def validate(self, *, source_id: str) -> SourceValidation:
        """Reachability and server identity. Must not raise for an unreachable
        source; that outcome is the returned value."""

    async def list_sources(self) -> Sequence[str]:
        """Every source this implementation can reach."""

    async def list_objects(self, *, source_id: str) -> Sequence[SourceObjectRef]:
        """Collections / tables / views / node labels, excluding the source's own
        system objects."""

    async def describe_object(self, *, source_id: str, object_name: str) -> ObjectDescription:
        """Field names, declared types and nullability for one object."""

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """At most `limit` rows. `fields` narrows the projection at the source, so
        an unlisted column is never read rather than being read and discarded."""

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> ObjectProfile:
        """Statistics only -- counts, rates, candidacy flags, never a value.

        `sample_size` bounds the rows read to compute them; the rows themselves do
        not leave the implementation, which is why profiling a source is a weaker
        grant than sampling one."""

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        """Declared indexes. What is cheap to look up by is a different question
        from what is unique, so both flags travel."""

    async def list_relationships(
        self, *, source_id: str, object_name: str | None = None
    ) -> Sequence[RelationshipObservation]:
        """Relationships the source declares, optionally narrowed to one object's
        outgoing edges. A source that declares none reports none."""
