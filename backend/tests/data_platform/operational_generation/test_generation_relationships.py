from datetime import UTC, datetime
from pathlib import Path

import pytest
from record_paths import at, leaf_paths

from return_platform.data_platform.operational_generation import (
    CollisionPolicy,
    GenerationMode,
    GenerationRequest,
    HallucinationGuard,
    OperationalGenerator,
    ScenarioType,
)
from return_platform.data_platform.schema_registry import SchemaRegistry, load_schema_registry


@pytest.fixture
def registry() -> SchemaRegistry:
    project_root = Path(__file__).parent.parent.parent.parent.parent
    return load_schema_registry(project_root / "backend" / "config" / "schema_registry.yaml")


@pytest.fixture
def guard(registry: SchemaRegistry) -> HallucinationGuard:
    return HallucinationGuard(registry)


@pytest.fixture
def generator(registry: SchemaRegistry, guard: HallucinationGuard) -> OperationalGenerator:
    return OperationalGenerator(registry, guard)


@pytest.mark.asyncio
async def test_generation_relationships_valid(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    # Test customer -> order -> line -> shipment dependencies (or a subset)
    # Actually we just pass valid generated_data_policy="ENABLED" assets
    assets = [
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED" and a.write_policy != "DENIED"
    ]
    asset_ids = tuple(a.asset_id for a in assets)

    if not asset_ids:
        pytest.skip("No available assets for generation")

    req = GenerationRequest(
        asset_ids=asset_ids,
        record_count=1,
        deterministic_seed=123,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    proposal = await generator.generate_proposal(req)

    records = {record.asset_id: record for record in proposal.records}
    customer = records["source.mongodb.customer_outbound_cdm"].values
    order = records["source.mongodb.sales_inv"].values
    shipment = records["source.mongodb.shipment_info"].values
    product = records["source.mongodb.product_search"].values
    # Read by registry path, not by top-level key: these records are nested,
    # which is the shape the active schema's `physical_path` mappings read.
    #
    # The customer join is the documented CDM bridge --
    # party[].custAccts[].additionalCustomerInfo[].customerId is what salesInv
    # copies into custId. There is no top-level `customerId` on the CDM
    # document, and asserting one only ever passed against the old flat seed.
    cdm_customer_id = at(customer, "party")[0]["custAccts"][0]["additionalCustomerInfo"][0][
        "customerId"
    ]
    assert at(order, "salesHdr.salesHdrData.custId") == cdm_customer_id
    assert at(shipment, "shipmentInfoEventData.trilOrdNum") == at(
        order, "salesHdrEventData.orderId"
    )
    # The join the graph actually makes: `line_references_product` matches
    # order_line.master_product_id (lineData.masterProductId) against
    # product.product_id, whose physical path is `_id`.
    assert at(order, "salesLines")[0]["lineData"]["masterProductId"] == at(product, "_id")

    for rec in proposal.records:
        for k, v in leaf_paths(rec.values):
            if (
                ("date" in k.lower() or "time" in k.lower())
                and isinstance(v, str)
                and v.endswith("Z")
            ):
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                assert req.date_from <= dt <= req.date_to
