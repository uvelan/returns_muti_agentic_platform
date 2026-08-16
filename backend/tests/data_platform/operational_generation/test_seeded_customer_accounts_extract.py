"""The seed's CDM document and the real one, put through the same extractor.

The defect this closes is not a wrong path -- it is a wrong *proof*. The active
schema's `customer_account` entity once declared
`party[].custAccts[].additionalCustomerInfo[]`, a path taken from the field
specification rather than from data, and `_cdm_parties` hand-built exactly that
structure into every seeded document. So the entity extracted rows in any
environment that had been seeded, extracted none from the one real document in
`return_source.customerOutboundCDM`, and the disagreement was invisible because
300 of the 301 documents there were the generator's own.

The entity has since been repointed to `party[].partyMainCusts[].mainCusts` --
what `MASTER:900781` carries. That left the generator emitting a shape nothing
reads and seeded customers contributing zero `customer_account` rows.

Inspecting the generated document cannot catch a recurrence of either failure,
because both were shapes that looked right. What catches it is running the
platform's own `GenericSourceRecordExtractor`, with the released schema, over a
generated document and over the real one, and requiring the same rows out of
both. A generator that drifts back to an invented shape fails here even if the
schema drifts with it, because the real document is not the generator's to
change.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.data_platform.operational_generation import (
    CollisionPolicy,
    GenerationMode,
    GenerationRequest,
    HallucinationGuard,
    OperationalGenerator,
    ScenarioType,
)
from return_platform.data_platform.schema_registry import SchemaRegistry, load_schema_registry
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    ProjectionReadScope,
    RawSourceDocument,
    RawSourcePage,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

BACKEND_ROOT = Path(__file__).parents[3]
CDM_ASSET_ID = "source.mongodb.customer_outbound_cdm"
#: The schema's own id for the same collection. The registry and the active
#: schema name their assets independently; this is the join between them.
CDM_SOURCE_ASSET_ID = "source_customers"

#: The properties `customer_account_node` projects, minus the timestamp, which
#: the real sample carries as an unresolved `{"$date": ...}` extended-JSON
#: wrapper rather than a value.
ACCOUNT_FIELDS = ("party_id", "customer_account", "customer_id", "customer_branch_id")


@pytest.fixture(scope="module")
def registry() -> SchemaRegistry:
    return load_schema_registry(BACKEND_ROOT / "config" / "schema_registry.yaml")


@pytest.fixture(scope="module")
def active_schema() -> ActiveSchema:
    return load_active_schema(
        BACKEND_ROOT / "config" / "dynamic_knowledge" / "active-schema.return-order.yaml"
    )


@pytest.fixture(scope="module")
def real_document() -> dict[str, Any]:
    """`MASTER:900781` -- the one non-synthetic document in the extract."""
    path = (
        BACKEND_ROOT
        / "tests"
        / "fixtures"
        / "ferguson_source_samples"
        / "customer_outbound_cdm.json"
    )
    with path.open(encoding="utf-8") as stream:
        document: dict[str, Any] = json.load(stream)
    return document


async def _generated_documents(registry: SchemaRegistry) -> list[dict[str, Any]]:
    generator = OperationalGenerator(registry, HallucinationGuard(registry))
    proposal = await generator.generate_proposal(
        GenerationRequest(
            asset_ids=(CDM_ASSET_ID,),
            record_count=3,
            deterministic_seed=4831,
            tenant_id="test_tenant",
            date_from=datetime(2023, 1, 1, tzinfo=UTC),
            date_to=datetime(2023, 1, 31, tzinfo=UTC),
            generation_mode=GenerationMode.DETERMINISTIC,
            collision_policy=CollisionPolicy.REJECT,
            scenario_distribution={ScenarioType.POSITIVE: 1},
        )
    )
    return [dict(record.values) for record in proposal.records if record.asset_id == CDM_ASSET_ID]


def _accounts(schema: ActiveSchema, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every `customer_account` row the released schema extracts from these documents."""
    page = RawSourcePage(
        documents=tuple(
            RawSourceDocument(
                operation="UPSERT",
                document=document,
                source_identity=str(document.get("_id", index)),
            )
            for index, document in enumerate(documents)
        ),
        observed_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    mutations = GenericSourceRecordExtractor().extract(
        schema=schema,
        source_asset_id=CDM_SOURCE_ASSET_ID,
        page=page,
        read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
    )
    return [
        dict(mutation.record.values)
        for mutation in mutations
        if mutation.entity_id == "customer_account" and mutation.record is not None
    ]


@pytest.mark.asyncio
async def test_the_real_document_and_the_seed_yield_the_same_row_shape(
    registry: SchemaRegistry,
    active_schema: ActiveSchema,
    real_document: dict[str, Any],
) -> None:
    """Both sides produce `customer_account` rows, and the same fields on each.

    Field-for-field rather than count-for-count: what broke was that one side
    resolved every path and the other resolved none, which a count assertion on
    the seed alone would have reported as a healthy zero.
    """
    real = _accounts(active_schema, [real_document])
    seeded = _accounts(active_schema, await _generated_documents(registry))

    assert real, "the real CDM document must still yield customer_account rows"
    assert seeded, (
        "seeded customers contribute no customer_account rows: the generator is "
        "building a shape the released schema does not read"
    )
    assert {frozenset(row) for row in real} == {frozenset(row) for row in seeded}
    for row in (*real, *seeded):
        assert set(ACCOUNT_FIELDS) <= set(row)


@pytest.mark.asyncio
async def test_both_sides_carry_the_branch_and_customer_split(
    registry: SchemaRegistry,
    active_schema: ActiveSchema,
    real_document: dict[str, Any],
) -> None:
    """`BRANCH*CUSTID`, split into the two halves the graph keys and filters on.

    `customer_id` is half of `customer_account`'s natural key and the value
    salesInv copies into `custId`; `customer_branch_id` is the other half of the
    composite. A seed whose account reference carried no `*` would extract a
    `customer_id` of `None`, lose the natural key, and drop the row -- silently,
    since a dropped row and an unseeded collection look identical.
    """
    for source, rows in (
        ("real", _accounts(active_schema, [real_document])),
        ("seeded", _accounts(active_schema, await _generated_documents(registry))),
    ):
        for row in rows:
            reference = row["customer_account"]
            assert "*" in reference, f"{source} account reference is not BRANCH*CUSTID: {reference}"
            branch, customer_id = reference.split("*", 1)
            assert row["customer_branch_id"] == branch
            assert row["customer_id"] == customer_id
            assert branch and customer_id


@pytest.mark.asyncio
async def test_the_seed_exercises_the_distinct_declaration(
    registry: SchemaRegistry,
    active_schema: ActiveSchema,
) -> None:
    """A seeded document repeats an account, as `MASTER:900781` does.

    `customer_account` is declared `distinct` because a party lists the same
    `mainCusts` value more than once. A seed that never repeated one would leave
    that declaration exercised by the real fixture alone, and a regression that
    dropped it would show up in production rather than here.
    """
    documents = await _generated_documents(registry)
    entries = [
        entry
        for document in documents
        for party in document["party"]
        for entry in party["partyMainCusts"]
    ]
    rows = _accounts(active_schema, documents)

    assert len(entries) > len(rows)
    assert len({(row["party_id"], row["customer_id"]) for row in rows}) == len(rows)


@pytest.mark.asyncio
async def test_the_fabricated_bridge_is_gone_from_the_seed(
    registry: SchemaRegistry,
) -> None:
    """No `custAccts` anywhere in a generated CDM document.

    Not tidiness. `custAccts` is the structure the generator invented to satisfy
    a declaration the source never supported, and leaving it in place would
    leave the next reader a second, plausible-looking bridge to repoint the
    schema back onto.
    """
    documents = await _generated_documents(registry)

    assert documents
    for document in documents:
        assert "custAccts" not in json.dumps(document)
        for party in document["party"]:
            assert "partyMainCusts" in party
