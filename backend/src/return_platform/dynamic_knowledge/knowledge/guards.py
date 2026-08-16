"""Deterministic capability, schema, query, anchor, and hallucination guards."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from return_platform.dynamic_knowledge.knowledge.evidence import (
    EvidenceValidationFailure,
    EvidenceValidationResult,
    QueryEvidence,
    StatementType,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import LogicalQueryPlan, QueryOperation
from return_platform.dynamic_knowledge.schema import ActiveSchema, AgentPolicy, EntitySourceAccess


class GuardRejected(ValueError):
    """Typed deterministic policy rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PrincipalContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str
    tenant_id: str
    roles: frozenset[str]
    branch_ids: frozenset[str] = frozenset()


class QuerySafetyPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_traversal_depth: int = Field(default=4, ge=0, le=16)
    max_rows: int = Field(default=100, ge=1, le=10_000)
    max_distinct_values: int = Field(default=20, ge=1, le=1000)
    max_filters: int = Field(default=20, ge=1, le=100)


class AnchorValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str
    operator: str
    value: Any
    value_origin: str


class StrongAnchorRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    strong_anchor_id: str
    anchors: tuple[AnchorValue, ...]


@dataclass(frozen=True, slots=True)
class GuardContext:
    schema: ActiveSchema
    agent_policy: AgentPolicy
    principal: PrincipalContext


class CapabilityGuard:
    def validate(self, context: GuardContext, capability: str) -> None:
        if not roles_allowed(context.principal.roles, context.agent_policy.allowed_roles):
            raise GuardRejected(
                "ORDER_AGENT_OUT_OF_SCOPE",
                "The authenticated role cannot use this agent.",
            )
        if capability not in context.agent_policy.allowed_business_capabilities:
            raise GuardRejected(
                "ORDER_AGENT_INVALID_CAPABILITY",
                f"Capability {capability!r} is not one of the exact allowed values "
                f"(allowed: {sorted(context.agent_policy.allowed_business_capabilities)}).",
            )


class SchemaQueryGuard:
    def validate(self, context: GuardContext, plan: LogicalQueryPlan) -> None:
        schema = context.schema
        if plan.start_entity_id not in context.agent_policy.allowed_entity_ids:
            raise GuardRejected(
                "ORDER_AGENT_OUT_OF_SCOPE", "The requested entity is outside the agent policy."
            )
        involved_entities = {plan.start_entity_id}
        involved_entities.update(step.target_entity_id for step in plan.traversal)
        if not involved_entities.issubset(context.agent_policy.allowed_entity_ids):
            raise GuardRejected(
                "ORDER_AGENT_OUT_OF_SCOPE",
                "Traversal references an entity outside the agent policy.",
            )
        for condition in plan.filters:
            entity = schema.entities.get(condition.entity_id)
            if entity is None or condition.field_id not in entity.fields:
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE", "Query references an unknown field."
                )
            field = entity.fields[condition.field_id]
            if not field.capabilities.filterable and not field.capabilities.searchable:
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE", "Field is not query-enabled."
                )
            if condition.operator not in field.capabilities.operators:
                raise GuardRejected(
                    "REJECT_UNSUPPORTED_OPERATOR", "Operator is not enabled for the field."
                )
            if not roles_allowed(context.principal.roles, field.permissions.searchable_by):
                raise GuardRejected(
                    "REJECT_UNAUTHORIZED_FIELD", "The field is not available to this role."
                )
        if plan.operation is QueryOperation.FULLTEXT_SEARCH:
            # The index replaces the WHERE clause, so nothing above validated
            # the field being matched on. Without this a full-text plan could
            # rank on a field the principal is not allowed to search -- the
            # search itself is the disclosure, whatever the result columns say.
            start_entity = schema.entities.get(plan.start_entity_id)
            indexed_field = (
                None
                if start_entity is None or plan.fulltext_field_id is None
                else start_entity.fields.get(plan.fulltext_field_id)
            )
            if indexed_field is None:
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE",
                    "Full-text search references an unknown field.",
                )
            if not indexed_field.capabilities.searchable:
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE", "Field is not search-enabled."
                )
            if not roles_allowed(context.principal.roles, indexed_field.permissions.searchable_by):
                raise GuardRejected(
                    "REJECT_UNAUTHORIZED_FIELD", "The field is not available to this role."
                )
        target_entity_id = (
            plan.traversal[-1].target_entity_id if plan.traversal else plan.start_entity_id
        )
        target_entity = schema.entities[target_entity_id]
        selected_fields = set(plan.fields)
        if plan.aggregation_field_id:
            selected_fields.add(plan.aggregation_field_id)
        selected_fields.update(plan.group_by_field_ids)
        for field_id in selected_fields:
            selected_field = target_entity.fields.get(field_id)
            if selected_field is None:
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE", "Query references an unknown result field."
                )
            if (
                plan.operation in {QueryOperation.DISTINCT_VALUES, QueryOperation.TOP_VALUES}
                and not selected_field.capabilities.distinct
            ):
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE",
                    "Distinct values are not enabled for the field.",
                )
            if (
                plan.operation
                in {
                    QueryOperation.COUNT_DISTINCT,
                    QueryOperation.MIN,
                    QueryOperation.MAX,
                    QueryOperation.SUM,
                    QueryOperation.AVERAGE,
                }
                and not selected_field.capabilities.aggregatable
            ):
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE", "Aggregation is not enabled for the field."
                )
            if not roles_allowed(
                context.principal.roles, selected_field.permissions.displayable_by
            ):
                raise GuardRejected(
                    "REJECT_UNAUTHORIZED_FIELD", "The field cannot be displayed to this role."
                )
        current_entity = plan.start_entity_id
        for step in plan.traversal:
            relationship = schema.graph.relationships.get(step.relationship_id)
            if relationship is None:
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE", "Unknown graph relationship."
                )
            if step.direction == "OUTBOUND":
                valid = (
                    relationship.source_entity_id == current_entity
                    and relationship.target_entity_id == step.target_entity_id
                )
            else:
                valid = (
                    relationship.target_entity_id == current_entity
                    and relationship.source_entity_id == step.target_entity_id
                )
            if not valid:
                raise GuardRejected(
                    "REJECT_INVALID_SCHEMA_REFERENCE", "Relationship path is invalid."
                )
            current_entity = step.target_entity_id


class QuerySafetyGuard:
    def __init__(self, policy: QuerySafetyPolicy) -> None:
        self._policy = policy

    def validate(self, plan: LogicalQueryPlan) -> None:
        if len(plan.traversal) > self._policy.max_traversal_depth:
            raise GuardRejected(
                "ORDER_AGENT_QUERY_BUDGET_EXCEEDED", "Traversal depth exceeds policy."
            )
        if plan.limit > self._policy.max_rows:
            raise GuardRejected(
                "ORDER_AGENT_QUERY_BUDGET_EXCEEDED", "Requested row limit exceeds policy."
            )
        if len(plan.filters) > self._policy.max_filters:
            raise GuardRejected("ORDER_AGENT_QUERY_BUDGET_EXCEEDED", "Filter count exceeds policy.")
        if (
            plan.operation is QueryOperation.DISTINCT_VALUES
            and plan.limit > self._policy.max_distinct_values
        ):
            raise GuardRejected(
                "ORDER_AGENT_QUERY_BUDGET_EXCEEDED", "Distinct-value limit exceeds policy."
            )


class StrongAnchorGuard:
    def validate(self, context: GuardContext, request: StrongAnchorRequest) -> dict[str, Any]:
        entity = context.schema.entities.get(request.entity_id)
        if entity is None or request.entity_id not in context.agent_policy.allowed_entity_ids:
            raise GuardRejected(
                "REJECT_INVALID_SCHEMA_REFERENCE", "Unknown or unavailable anchor entity."
            )
        if entity.source_access is not EntitySourceAccess.CONNECTED_SYNC:
            raise GuardRejected(
                "ON_DEMAND_SYNC_ENTITY_NOT_CONNECTED",
                f"Entity {request.entity_id!r} has source_access "
                f"{entity.source_access!r} and cannot be synchronized on demand.",
            )
        definition = entity.strong_anchors.get(request.strong_anchor_id)
        if definition is None or not definition.on_demand_sync_allowed:
            raise GuardRejected("REJECT_WEAK_ANCHOR", "The requested strong anchor is not enabled.")
        supplied = {anchor.field_id: anchor for anchor in request.anchors}
        if len(supplied) != len(request.anchors):
            raise GuardRejected("REJECT_WEAK_ANCHOR", "Duplicate anchor fields are not allowed.")
        configured = {item.field_id: item for item in definition.fields}
        if not set(supplied).issubset(configured):
            raise GuardRejected(
                "REJECT_INVALID_SCHEMA_REFERENCE", "Anchor contains an unconfigured field."
            )
        required = {item.field_id for item in definition.fields if item.required}
        if not required.issubset(supplied) or len(supplied) < definition.minimum_fields_present:
            raise GuardRejected(
                "ON_DEMAND_SYNC_STRONG_ANCHOR_REQUIRED",
                "Additional configured identifying information is required.",
            )
        normalized: dict[str, Any] = {}
        for field_id, anchor in supplied.items():
            field_definition = entity.fields[field_id]
            anchor_definition = configured[field_id]
            if not field_definition.capabilities.on_demand_sync_anchor:
                raise GuardRejected(
                    "REJECT_WEAK_ANCHOR", "Field is not enabled for on-demand synchronization."
                )
            if anchor.operator not in anchor_definition.allowed_operators:
                raise GuardRejected(
                    "REJECT_UNSUPPORTED_OPERATOR", "Anchor operator is not enabled."
                )
            permitted_roles = field_definition.permissions.on_demand_sync_by
            if not roles_allowed(context.principal.roles, permitted_roles):
                raise GuardRejected(
                    "REJECT_UNAUTHORIZED_FIELD", "Anchor field is not available to this role."
                )
            if anchor.value_origin not in {"USER_MESSAGE", "GRAPH_EVIDENCE", "CONFIRMED_FACT"}:
                raise GuardRejected("REJECT_WEAK_ANCHOR", "Anchor value has an untrusted origin.")
            normalized[field_id] = _normalize_anchor_value(anchor.operator, anchor.value)
        return normalized


class ResponseSafetyGuard:
    """Block raw query text, secrets, and policy disclosure from model responses."""

    _forbidden_patterns = tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"```(?:sql|cypher|mongodb)",
            r"\b(?:DETACH\s+DELETE|DROP\s+(?:TABLE|DATABASE|INDEX)|LOAD\s+CSV)\b",
            r"\b(?:system prompt|hidden instructions|developer message)\b",
            r"\b(?:AIza[0-9A-Za-z_-]{20,}|nvapi-[0-9A-Za-z_-]+|sk-[0-9A-Za-z_-]{20,})\b",
        )
    )

    def validate(
        self, response: StructuredAgentResponse, *, allowed_capabilities: frozenset[str]
    ) -> None:
        if response.business_capability not in allowed_capabilities:
            raise GuardRejected("ORDER_AGENT_OUT_OF_SCOPE", "Response capability is not enabled.")
        for statement in response.statements:
            if len(statement.text) > 2_000:
                raise GuardRejected(
                    "ORDER_AGENT_RESPONSE_VALIDATION_FAILED", "Response statement is too long."
                )
            if any(pattern.search(statement.text) for pattern in self._forbidden_patterns):
                raise GuardRejected(
                    "ORDER_AGENT_RESPONSE_VALIDATION_FAILED",
                    "Response contains prohibited query, secret, or policy-disclosure content.",
                )


class HallucinationGuard:
    def validate(
        self,
        *,
        response: StructuredAgentResponse,
        evidence: Sequence[QueryEvidence],
        graph_generation_id: str,
    ) -> EvidenceValidationResult:
        evidence_by_id = {item.query_execution_id: item for item in evidence}
        failures: list[EvidenceValidationFailure] = []
        for statement in response.statements:
            if statement.statement_type is not StatementType.GRAPH_FACT:
                continue
            for reference in statement.evidence_refs:
                item = evidence_by_id.get(reference.query_execution_id)
                if item is None:
                    failures.append(
                        EvidenceValidationFailure(
                            statement_id=statement.statement_id,
                            reason="missing query evidence",
                        )
                    )
                    continue
                if item.graph_generation_id != graph_generation_id:
                    failures.append(
                        EvidenceValidationFailure(
                            statement_id=statement.statement_id,
                            reason="evidence belongs to a stale graph generation",
                        )
                    )
                    continue
                try:
                    actual = _resolve_result_path(item.result, reference.result_path)
                except (KeyError, IndexError, TypeError, ValueError):
                    failures.append(
                        EvidenceValidationFailure(
                            statement_id=statement.statement_id,
                            reason=(
                                f"evidence path {list(reference.result_path)!r} does not exist "
                                f"in query {reference.query_execution_id}"
                            ),
                        )
                    )
                    continue
                if reference.expected_value is not None and actual != reference.expected_value:
                    failures.append(
                        EvidenceValidationFailure(
                            statement_id=statement.statement_id,
                            reason=(
                                f"claimed value {reference.expected_value!r} for path "
                                f"{list(reference.result_path)!r} does not match the actual "
                                f"evidence value {actual!r}"
                            ),
                        )
                    )
        return EvidenceValidationResult(valid=not failures, failures=tuple(failures))


def _resolve_result_path(value: Any, path: Sequence[str]) -> Any:
    current = value
    for segment in path:
        if isinstance(current, Mapping):
            current = current[segment]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            if not segment.isdecimal():
                raise ValueError("sequence path segment must be non-negative integer")
            current = current[int(segment)]
        else:
            raise TypeError("cannot traverse evidence path")
    return current


def _normalize_anchor_value(operator: str, value: Any) -> Any:
    if operator == "NORMALIZED_EQUALS":
        if not isinstance(value, str):
            raise GuardRejected("REJECT_WEAK_ANCHOR", "NORMALIZED_EQUALS requires a string value.")
        return " ".join(value.split()).casefold()
    return value


def roles_allowed(principal_roles: frozenset[str], permitted_roles: frozenset[str]) -> bool:
    """Whether this principal holds a role the field or policy admits.

    Public, and imported by `integration/neo4j_gateway.py`, so that the compact
    schema put in front of the reasoning model can advertise exactly what these
    guards will accept rather than a superset of it. A second copy of the
    predicate there would be a second copy of a security decision, and the two
    would drift the first time `*` stopped meaning what it means today.

    An empty `permitted_roles` denies everyone. That is not an oversight in the
    callers -- it is what an unset `permissions.searchable_by` means, and 55
    fields in the active schema rely on it.
    """
    return "*" in permitted_roles or bool(principal_roles.intersection(permitted_roles))
