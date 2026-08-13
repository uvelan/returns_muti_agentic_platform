"""Typed logical query plans produced by reasoning models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QueryOperation(StrEnum):
    SEARCH = "SEARCH"
    FILTER = "FILTER"
    TRAVERSE = "TRAVERSE"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    GROUP_BY = "GROUP_BY"
    MIN = "MIN"
    MAX = "MAX"
    SUM = "SUM"
    AVERAGE = "AVERAGE"
    DATE_RANGE = "DATE_RANGE"
    TOP_VALUES = "TOP_VALUES"
    DISTINCT_VALUES = "DISTINCT_VALUES"
    EXISTS = "EXISTS"
    MISSING_VALUE_COUNT = "MISSING_VALUE_COUNT"
    SEMANTIC_SEARCH = "SEMANTIC_SEARCH"
    #: Server-side approximate match over a full-text index. Distinct from
    #: SEMANTIC_SEARCH, which is a vector-index operation this platform does not
    #: have: a full-text index is an ordinary Neo4j index (see migration
    #: `0013_order_discovery_fulltext_v2.cypher`), needs no APOC, and ranks the
    #: *whole* labelled set by relevance rather than returning an arbitrary
    #: window of it.
    FULLTEXT_SEARCH = "FULLTEXT_SEARCH"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class QueryCondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    field_id: str
    operator: str
    value: Any = None


class TraversalStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str
    direction: str = Field(pattern=r"^(OUTBOUND|INBOUND)$")
    target_entity_id: str


class QuerySort(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    field_id: str
    direction: SortDirection = SortDirection.ASC


class LogicalQueryPlan(BaseModel):
    """Read-only graph request. It contains logical IDs, never raw Cypher."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: QueryOperation
    start_entity_id: str
    fields: tuple[str, ...] = ()
    filters: tuple[QueryCondition, ...] = ()
    traversal: tuple[TraversalStep, ...] = ()
    aggregation_field_id: str | None = None
    group_by_field_ids: tuple[str, ...] = ()
    sort: tuple[QuerySort, ...] = ()
    candidate_set_id: str | None = None
    semantic_query: str | None = None
    #: FULLTEXT_SEARCH only. The index is named rather than derived because the
    #: name is operator-owned runtime configuration
    #: (`discovery.progressive.customer_fulltext_index`), not a property of the
    #: schema; `fulltext_field_id` is the field it covers, carried so the schema
    #: guard can check that this principal may search that field at all.
    fulltext_index: str | None = None
    fulltext_field_id: str | None = None
    fulltext_query: str | None = None
    limit: int = Field(default=20, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> LogicalQueryPlan:
        aggregate_ops = {
            QueryOperation.COUNT_DISTINCT,
            QueryOperation.MIN,
            QueryOperation.MAX,
            QueryOperation.SUM,
            QueryOperation.AVERAGE,
            QueryOperation.DISTINCT_VALUES,
            QueryOperation.TOP_VALUES,
        }
        if self.operation in aggregate_ops and self.aggregation_field_id is None:
            raise ValueError(f"{self.operation.value} requires aggregation_field_id")
        if self.operation is QueryOperation.GROUP_BY and not self.group_by_field_ids:
            raise ValueError("GROUP_BY requires at least one group_by_field_id")
        if self.operation is QueryOperation.TRAVERSE and not self.traversal:
            raise ValueError("TRAVERSE requires at least one relationship step")
        if (
            self.operation is QueryOperation.SEMANTIC_SEARCH
            and not (self.semantic_query or "").strip()
        ):
            raise ValueError("SEMANTIC_SEARCH requires semantic_query")
        fulltext_parts = (self.fulltext_index, self.fulltext_field_id, self.fulltext_query)
        if self.operation is QueryOperation.FULLTEXT_SEARCH:
            if any(not (part or "").strip() for part in fulltext_parts):
                raise ValueError(
                    "FULLTEXT_SEARCH requires fulltext_index, fulltext_field_id and fulltext_query"
                )
            if self.traversal:
                raise ValueError("FULLTEXT_SEARCH starts at the indexed entity and cannot traverse")
            if self.filters:
                # The index is the predicate. A WHERE alongside it would read as
                # a narrowing of the ranked set while actually being applied
                # after the index's own limit -- which is how a bounded window
                # reappears under a different name.
                raise ValueError("FULLTEXT_SEARCH takes its predicate from the index, not filters")
        elif any(part is not None for part in fulltext_parts):
            raise ValueError("full-text fields are only valid on a FULLTEXT_SEARCH plan")
        return self
