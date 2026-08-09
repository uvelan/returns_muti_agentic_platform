from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.dynamic_knowledge.knowledge.guards import (
    AnchorValue,
    GuardContext,
    GuardRejected,
    PrincipalContext,
    StrongAnchorGuard,
    StrongAnchorRequest,
)
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntitySourceAccess,
    RelationshipSourceAccess,
    SourceContractStatus,
    maximum_relationship_access,
)


def context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies["agent_a"],
        principal=PrincipalContext(
            principal_id="p1", tenant_id="t1", roles=frozenset({"associate"})
        ),
    )


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        (
            EntitySourceAccess.CONNECTED_SYNC,
            EntitySourceAccess.CONNECTED_SYNC,
            RelationshipSourceAccess.CONNECTED_SYNC,
        ),
        (
            EntitySourceAccess.CONNECTED_SYNC,
            EntitySourceAccess.CONNECTED_READ,
            RelationshipSourceAccess.SEED_ONLY,
        ),
        (
            EntitySourceAccess.CONNECTED_READ,
            EntitySourceAccess.CONNECTED_READ,
            RelationshipSourceAccess.SEED_ONLY,
        ),
        (
            EntitySourceAccess.CONNECTED_SYNC,
            EntitySourceAccess.SEED_ONLY,
            RelationshipSourceAccess.SEED_ONLY,
        ),
        (
            EntitySourceAccess.SEED_ONLY,
            EntitySourceAccess.SEED_ONLY,
            RelationshipSourceAccess.SEED_ONLY,
        ),
        (
            EntitySourceAccess.CONNECTED_SYNC,
            EntitySourceAccess.DISABLED,
            RelationshipSourceAccess.DISABLED,
        ),
        (
            EntitySourceAccess.SEED_ONLY,
            EntitySourceAccess.DISABLED,
            RelationshipSourceAccess.DISABLED,
        ),
    ],
)
def test_maximum_relationship_access_matrix(
    source: EntitySourceAccess, target: EntitySourceAccess, expected: RelationshipSourceAccess
) -> None:
    assert maximum_relationship_access(source, target) == expected
    assert maximum_relationship_access(target, source) == expected


def test_unverified_entity_cannot_declare_connected_sync(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"]["source_contract_status"] = "UNVERIFIED"
    raw["entities"]["entity_a"]["source_access"] = "CONNECTED_SYNC"
    with pytest.raises(ValidationError, match="UNVERIFIED"):
        ActiveSchema.model_validate(raw)


def test_unverified_entity_may_be_seed_only(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"]["source_contract_status"] = "UNVERIFIED"
    raw["entities"]["entity_a"]["source_access"] = "SEED_ONLY"
    # a_to_b's access must not exceed entity_a's new SEED_ONLY ceiling.
    raw["graph"]["relationships"]["a_to_b"]["access"] = "SEED_ONLY"
    schema = ActiveSchema.model_validate(raw)
    assert schema.entities["entity_a"].source_contract_status is SourceContractStatus.UNVERIFIED


def test_relationship_cannot_exceed_seed_only_endpoint(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_b"]["source_access"] = "SEED_ONLY"
    # relationship a_to_b defaults to CONNECTED_SYNC, which now exceeds entity_b's ceiling.
    with pytest.raises(ValidationError, match="exceeds the maximum"):
        ActiveSchema.model_validate(raw)


def test_relationship_seed_only_is_allowed_against_seed_only_endpoint(
    active_schema: ActiveSchema,
) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_b"]["source_access"] = "SEED_ONLY"
    raw["graph"]["relationships"]["a_to_b"]["access"] = "SEED_ONLY"
    schema = ActiveSchema.model_validate(raw)
    assert schema.graph.relationships["a_to_b"].access is RelationshipSourceAccess.SEED_ONLY


def test_strong_anchor_guard_rejects_seed_only_entity(active_schema: ActiveSchema) -> None:
    raw = active_schema.model_dump(mode="json")
    raw["entities"]["entity_a"]["source_access"] = "SEED_ONLY"
    raw["graph"]["relationships"]["a_to_b"]["access"] = "SEED_ONLY"
    schema = ActiveSchema.model_validate(raw)
    with pytest.raises(GuardRejected) as error:
        StrongAnchorGuard().validate(
            context(schema),
            StrongAnchorRequest(
                entity_id="entity_a",
                strong_anchor_id="exact_id",
                anchors=(
                    AnchorValue(
                        field_id="id", operator="EXACT", value="A-100", value_origin="USER_MESSAGE"
                    ),
                ),
            ),
        )
    assert error.value.code == "ON_DEMAND_SYNC_ENTITY_NOT_CONNECTED"


def test_strong_anchor_guard_allows_connected_sync_entity(active_schema: ActiveSchema) -> None:
    normalized = StrongAnchorGuard().validate(
        context(active_schema),
        StrongAnchorRequest(
            entity_id="entity_a",
            strong_anchor_id="exact_id",
            anchors=(
                AnchorValue(
                    field_id="id", operator="EXACT", value="A-100", value_origin="USER_MESSAGE"
                ),
            ),
        ),
    )
    assert normalized == {"id": "A-100"}
