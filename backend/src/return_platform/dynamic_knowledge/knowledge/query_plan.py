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
        if self.operation is QueryOperation.TRAVERSE and not self.traversal:
            raise ValueError("TRAVERSE requires at least one relationship step")
        if (
            self.operation is QueryOperation.SEMANTIC_SEARCH
            and not (self.semantic_query or "").strip()
        ):
            raise ValueError("SEMANTIC_SEARCH requires semantic_query")
        return self
