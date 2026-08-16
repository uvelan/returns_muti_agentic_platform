"""AI Studio's sandbox CDM document, read by the released schema.

The same defect as the seed generator's, in the second place it survived.
`ai_studio._value` built `party[].custAccts[].additionalCustomerInfo[]` -- a
path no real `customerOutboundCDM` document has at any level. It existed
because the active schema once declared `customer_account` against it, taken
from the field specification rather than from data, so the sandbox manufactured
exactly the structure the wrong declaration asserted and a validation ERROR was
cleared by data the platform had invented for itself.

`operational_generation/generator.py::_cdm_parties` was corrected first, to
`party[].partyMainCusts[].mainCusts` -- what `MASTER:900781` carries. This file
holds the sandbox path to the same statement, and proves it the same way: not by
inspecting the document (both shapes look plausible) but by running the
platform's own `GenericSourceRecordExtractor` with the released schema over it
and requiring the rows the real document yields.

One thing is asserted here that the seed generator's file cannot assert. A
sandbox scenario mints a customer reference and writes it into its own sales
order, so `customer_id` -- the half of `mainCusts` after the `*`, and the join
to `sales_order.customer_id` -- has a known correct value. A bridge that carried
any other number would leave every sandbox party an orphan of its own orders.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.data_platform.ai_studio import (
    ScenarioContext,
    _scenario_context,
    generate_asset_record,
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

BACKEND_ROOT = Path(__file__).parents[2]
CDM_ASSET_ID = "source.mongodb.customer_outbound_cdm"
#: The active schema's own id for the same collection.
CDM_SOURCE_ASSET_ID = "source_customers"


@pytest.fixture(scope="module")
def registry() -> SchemaRegistry:
    return load_schema_registry(BACKEND_ROOT / "config" / "schema_registry.yaml")


@pytest.fixture(scope="module")
def active_schema() -> ActiveSchema:
    return load_active_schema(
        BACKEND_ROOT / "config" / "dynamic_knowledge" / "active-schema.return-order.yaml"
    )


@pytest.fixture(scope="module")
def scenario() -> ScenarioContext:
    return _scenario_context(0, random.Random(20260815), seed=20260815)


@pytest.fixture(scope="module")
def document(registry: SchemaRegistry, scenario: ScenarioContext) -> dict[str, Any]:
    return generate_asset_record(registry.asset(CDM_ASSET_ID), scenario, random.Random(20260815))


def _accounts(schema: ActiveSchema, document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every `customer_account` row the released schema extracts from one document."""
    page = RawSourcePage(
        documents=(
            RawSourceDocument(
                operation="UPSERT",
                document=document,
                source_identity=str(document.get("_id", "sandbox")),
            ),
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


def test_the_sandbox_document_yields_customer_account_rows(
    active_schema: ActiveSchema, document: dict[str, Any]
) -> None:
    """A zero here is the failure, and it is the one that hid for so long.

    A sandbox that contributes no `customer_account` rows looks exactly like a
    sandbox nobody has seeded, so nothing about the platform reports it.
    """
    rows = _accounts(active_schema, document)

    assert rows, (
        "the sandbox CDM document contributes no customer_account rows: it is "
        "building a shape the released schema does not read"
    )


def test_the_bridge_carries_the_scenario_own_customer_reference(
    active_schema: ActiveSchema, document: dict[str, Any], scenario: ScenarioContext
) -> None:
    """`BRANCH*CUSTID`, split into the halves the graph keys and filters on.

    `customer_id` must be the scenario's own customer reference -- the value its
    sales order carries in `custId` -- or the documented join produces nothing
    and every sandbox party is an orphan. `customer_branch_id` is the other
    half, and the `*` is the delimiter `SPLIT_PART` cuts on, so a branch code
    carrying one would publish two wrong halves rather than fail.
    """
    (row,) = _accounts(active_schema, document)

    branch, customer_id = str(row["customer_account"]).split("*", 1)
    assert row["customer_branch_id"] == branch
    assert row["customer_id"] == customer_id
    assert customer_id == scenario.customer_reference
    assert branch == scenario.branch_reference.upper()
    assert "*" not in branch
    assert branch == branch.upper()


def test_the_fabricated_bridge_is_gone_from_the_sandbox(document: dict[str, Any]) -> None:
    """No `custAccts` anywhere in the sandbox document either.

    Not tidiness. It is the structure that was invented to satisfy a declaration
    the source never supported, and leaving one copy of it standing leaves the
    next reader a second, plausible-looking bridge to repoint the schema onto.
    """
    assert "custAccts" not in json.dumps(document, default=str)
    for party in document["party"]:
        assert "partyMainCusts" in party
