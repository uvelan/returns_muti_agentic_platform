"""W2.7: bay placement reads an observed warehouse instead of scanning SQL.

Against the production descriptor
(`config/dynamic_knowledge/active-schema.return-order.yaml`), the real
`CypherCompiler` and a real `OnDemandSyncCoordinator`. The connector, projector,
writer, store and graph read are local stand-ins for the same reason W2.6's
module gives: what is under test is the plan the adapter builds, the query it
compiles and how it reads an answer, and a test that needed Neo4j to assert those
would not be run often enough to catch a regression in them.

The real source contract is asserted separately, against SQL Server, in
`test_warehouse_bay_source_contract_real_infra.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.integration.bay_observations import (
    BAY_ENTITY_ID,
    WAREHOUSE_ENTITY_ID,
    WAREHOUSE_FIELD_ID,
    GraphWarehouseBayObservations,
)
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphMutationBatch,
    RawSourceDocument,
    RawSourcePage,
    SyncReceipt,
    SyncReservation,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
)
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    EntitySourceAccess,
    RelationshipSourceAccess,
    SourceContractStatus,
)
from return_platform.operations.warehouse.observations import BayEvidence
from return_platform.source_connectors.contracts import LogicalTargetedReadPlan

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
WAREHOUSE = "WH-CHENNAI-01"
GENERATION = "gen-1"


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


class _Resolver:
    async def active_generation(self, schema: ActiveSchema) -> str:
        del schema
        return GENERATION


def _row(bay_id: str, **overrides: Any) -> dict[str, Any]:
    """One `platform.bay_configuration` row, in the shape pymssql returns.

    `active`/`hazardous_allowed`/`oversized_allowed` are `bit` columns and arrive
    as booleans; the JSON-array columns arrive as the text the source stores.
    """
    return {
        "bay_id": bay_id,
        "bay_name": f"Bay {bay_id}",
        "warehouse_id": WAREHOUSE,
        "branch_id": "BR-CHENNAI",
        "bay_type": "PPL",
        "active": True,
        "priority": 10,
        "supported_shipping_paths": '["PPL"]',
        "supported_product_types": '["STANDARD","BULKY"]',
        "max_package_count": 50,
        "overflow_bay_id": None,
        "hazardous_allowed": False,
        "oversized_allowed": False,
        "max_handling_unit_count": 50,
        "max_pallet_count": None,
        "capacity_unit": "HANDLING_UNIT",
        "row_version_v2": 1,
        "updated_at": datetime(2026, 8, 13, tzinfo=UTC),
        **overrides,
    }


class _Connector:
    """Answers a targeted read with real-shaped bay rows."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.plans: list[LogicalTargetedReadPlan] = []
        self._rows = [_row("BAY-PPL-01"), _row("BAY-BOL-01")] if rows is None else rows

    async def targeted_read(
        self, *, schema: ActiveSchema, plan: LogicalTargetedReadPlan
    ) -> RawSourcePage:
        del schema
        self.plans.append(plan)
        return RawSourcePage(
            documents=tuple(
                RawSourceDocument(
                    operation="UPSERT", document=row, source_identity=str(row["bay_id"])
                )
                for row in self._rows
            ),
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )


class _Registry:
    def __init__(self, connector: _Connector) -> None:
        self._connector = connector

    def resolve(self, source_asset_id: str) -> _Connector:
        return self._connector


class _Writer:
    def __init__(self) -> None:
        self.batches: list[GraphMutationBatch] = []

    async def write(
        self, *, schema: ActiveSchema, graph_generation_id: str, batch: GraphMutationBatch
    ) -> tuple[int, int]:
        del schema, graph_generation_id
        self.batches.append(batch)
        return len(batch.node_mutations), len(batch.relationship_mutations)


class _Store:
    async def reserve(
        self,
        *,
        request_digest: str,
        proposed_request_id: str,
        schema_version: str,
        graph_generation_id: str,
    ) -> SyncReservation:
        del request_digest, schema_version, graph_generation_id
        return SyncReservation(acquired=True, sync_request_id=proposed_request_id)

    async def complete(self, receipt: SyncReceipt) -> None:
        del receipt


class _Reader:
    """Captures every compiled read and answers per start entity."""

    def __init__(self, by_entity: dict[str, list[dict[str, Any]]]) -> None:
        self.by_entity = by_entity
        self.cyphers: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any:
        del schema, graph_generation_id
        self.cyphers.append(compiled_cypher)
        self.parameters.append(parameters)
        rows = self.by_entity.get(plan.start_entity_id, [])
        return {"rows": rows, "count": len(rows)}


def _observations(
    schema: ActiveSchema,
    reader: _Reader,
    connector: _Connector | None = None,
    writer: _Writer | None = None,
) -> GraphWarehouseBayObservations:
    coordinator = OnDemandSyncCoordinator(
        connectors=_Registry(connector or _Connector()),
        extractor=GenericSourceRecordExtractor(),
        projector=GenericGraphProjector(),
        writer=writer or _Writer(),
        store=_Store(),
    )
    return GraphWarehouseBayObservations(
        schema=schema,
        on_demand_sync=coordinator,
        generation_handles=GenerationHandleProvider(_Resolver()),
        knowledge_gateway=reader,
    )


def _graph_rows(*bay_ids: str) -> dict[str, list[dict[str, Any]]]:
    """What the graph would hold, keyed by the entity a plan starts at."""
    return {
        WAREHOUSE_ENTITY_ID: [{WAREHOUSE_FIELD_ID: WAREHOUSE, "branch_id": "BR-CHENNAI"}],
        BAY_ENTITY_ID: [_row(bay_id) for bay_id in bay_ids],
    }


def _demoted(schema: ActiveSchema) -> ActiveSchema:
    """The descriptor with `warehouse` pushed back to `SEED_ONLY`/`UNVERIFIED`.

    The shipped descriptor declares it `CONNECTED_SYNC` on a `VERIFIED` contract,
    its paths being columns SQL Server's own catalogue declares -- so the sync
    half is exercised on the descriptor as shipped and only the *refusal* needs a
    constructed schema. Demotion, never promotion: a test that raised an entity's
    access to make its own assertion pass would prove the test can pass.
    """
    document = schema.model_dump(mode="json")
    document["entities"][WAREHOUSE_ENTITY_ID]["source_access"] = EntitySourceAccess.SEED_ONLY.value
    document["entities"][WAREHOUSE_ENTITY_ID]["source_contract_status"] = (
        SourceContractStatus.UNVERIFIED.value
    )
    # Relationship access is capped by its endpoints, so demoting the entity
    # without this makes the whole descriptor fail validation.
    document["graph"]["relationships"]["warehouse_HAS_BAY_bay"]["access"] = (
        RelationshipSourceAccess.SEED_ONLY.value
    )
    return ActiveSchema.model_validate(document)


# ---------------------------------------------------------------------------
# The defect: a missing reference used to widen the search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_warehouse_reference_yields_no_candidates_and_says_so(
    descriptor: ActiveSchema,
) -> None:
    """The bypass answered this with every bay in the estate.

    `WHERE (%s IS NULL OR configuration.warehouse_id = %s)` collapses to true on
    a NULL, so a return that had never been given a processing warehouse got a
    *longer* candidate list than one that had, and the agent staged a parcel into
    a bay in some other building. Absence of a reference is absence of evidence.
    """
    reader = _Reader(_graph_rows("BAY-PPL-01"))
    connector = _Connector()

    observation = await _observations(descriptor, reader, connector).observe(None)

    assert observation.evidence is BayEvidence.ABSENT
    assert observation.absent_reason == "NO_WAREHOUSE_REFERENCE"
    assert observation.candidates == ()
    # Nothing was read and nothing was synced: there was nothing to look up.
    assert reader.cyphers == []
    assert connector.plans == []


@pytest.mark.asyncio
async def test_a_reference_the_graph_does_not_know_is_distinguishable_from_no_reference(
    descriptor: ActiveSchema,
) -> None:
    """Two different problems for whoever is holding the parcel.

    One is a return that was never routed; the other is a warehouse id that
    resolves to nothing, which is a configuration or sync fault. Collapsing them
    is how the SQL predicate above stayed unnoticed.
    """
    observation = await _observations(descriptor, _Reader({})).observe(WAREHOUSE)

    assert observation.evidence is BayEvidence.ABSENT
    assert observation.absent_reason == "WAREHOUSE_NOT_IN_GRAPH"
    assert observation.graph_generation_id == GENERATION


@pytest.mark.asyncio
async def test_an_observed_warehouse_with_no_bay_is_observed_and_not_absent(
    descriptor: ActiveSchema,
) -> None:
    """A warehouse whose every bay is switched off is a real state.

    Reporting it as ABSENT would say the warehouse does not exist, and whoever
    read that would go looking for a sync fault instead of for a bay.
    """
    reader = _Reader(
        {WAREHOUSE_ENTITY_ID: [{WAREHOUSE_FIELD_ID: WAREHOUSE, "branch_id": "BR-CHENNAI"}]}
    )

    observation = await _observations(descriptor, reader).observe(WAREHOUSE)

    assert observation.evidence is BayEvidence.OBSERVED
    assert observation.candidates == ()


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_observed_warehouse_carries_the_bays_the_graph_holds(
    descriptor: ActiveSchema,
) -> None:
    observation = await _observations(
        descriptor, _Reader(_graph_rows("BAY-PPL-01", "BAY-BOL-01"))
    ).observe(WAREHOUSE)

    assert observation.evidence is BayEvidence.OBSERVED
    assert [candidate["bay_id"] for candidate in observation.candidates] == [
        "BAY-PPL-01",
        "BAY-BOL-01",
    ]
    assert observation.graph_generation_id == GENERATION


@pytest.mark.asyncio
async def test_both_reads_are_compiled_from_the_schema_and_anchored_on_the_warehouse(
    descriptor: ActiveSchema,
) -> None:
    """Not hand-written Cypher, and two questions rather than a traversal.

    A traversal would answer "no such warehouse" and "no bay in it" with the same
    empty result. Compiling through `CypherCompiler` is what keeps both reads
    subject to the same field allowlist and identifier validation as an agent's
    query.
    """
    reader = _Reader(_graph_rows("BAY-PPL-01"))

    await _observations(descriptor, reader).observe(WAREHOUSE)

    assert len(reader.cyphers) == 2
    assert "MATCH (n0:`Warehouse`)" in reader.cyphers[0]
    assert "MATCH (n0:`Bay`)" in reader.cyphers[1]
    for cypher, parameters in zip(reader.cyphers, reader.parameters, strict=True):
        assert "n0.`warehouse_id` = $p0" in cypher
        assert parameters["p0"] == WAREHOUSE
        assert cypher.strip().startswith("MATCH")


@pytest.mark.asyncio
async def test_a_graph_outage_is_raised_rather_than_reported_as_no_warehouse(
    descriptor: ActiveSchema,
) -> None:
    """Reporting ABSENT on an outage would mark every return's bay omitted.

    Across the whole deployment, and it would look like configuration. The caller
    owns the `best_effort` policy and turns this into `UNAVAILABLE`.
    """

    class _Broken(_Reader):
        async def execute(self, **kwargs: Any) -> Any:
            raise RuntimeError("bolt connection refused")

    with pytest.raises(RuntimeError):
        await _observations(descriptor, _Broken({})).observe(WAREHOUSE)


# ---------------------------------------------------------------------------
# The sync half
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_anchored_read_brings_in_the_warehouse_and_all_of_its_bays(
    descriptor: ActiveSchema,
) -> None:
    """The step's Validation, in the unit that can assert it.

    Both entities are bound to `platform.bay_configuration`, and extraction runs
    every entity bound to a source over each document a targeted read returns --
    so anchoring on the warehouse is what syncs its bays, with no second sync and
    no anchor on `bay.warehouse_id`, which could not honestly declare a
    `maximum_expected_matches`.
    """
    connector = _Connector()
    writer = _Writer()

    observation = await _observations(
        descriptor, _Reader(_graph_rows("BAY-PPL-01")), connector, writer
    ).observe(WAREHOUSE)

    assert observation.sync_skipped_reason is None
    assert observation.sync_request_id is not None
    assert len(connector.plans) == 1
    plan = connector.plans[0]
    assert plan.entity_id == WAREHOUSE_ENTITY_ID
    assert [(c.field_id, c.operator, c.value) for c in plan.conditions] == [
        (WAREHOUSE_FIELD_ID, "EXACT", WAREHOUSE)
    ]

    written = {
        mutation.projection_id for batch in writer.batches for mutation in batch.node_mutations
    }
    assert written == {WAREHOUSE_ENTITY_ID, BAY_ENTITY_ID}
    assert any(batch.relationship_mutations for batch in writer.batches)


@pytest.mark.asyncio
async def test_the_sync_is_skipped_and_named_when_the_descriptor_forbids_it(
    descriptor: ActiveSchema,
) -> None:
    """A demoted entity is not something to route around.

    Skipping while still reading the graph is the honest behaviour: a scheduled
    sync may have brought the warehouse in, and the reason is recorded so the
    skip is visible. Would catch the access check being dropped as redundant now
    that the shipped descriptor permits the sync.
    """
    connector = _Connector()
    reader = _Reader(_graph_rows("BAY-PPL-01"))
    demoted = _demoted(descriptor)

    observation = await _observations(demoted, reader, connector).observe(WAREHOUSE)

    assert demoted.entities[WAREHOUSE_ENTITY_ID].source_access is EntitySourceAccess.SEED_ONLY
    assert observation.sync_skipped_reason == "SOURCE_ACCESS_SEED_ONLY"
    assert connector.plans == []
    # The read still happened, and found what a scheduled sync had left there.
    assert observation.evidence is BayEvidence.OBSERVED


@pytest.mark.asyncio
async def test_a_failed_sync_does_not_stop_the_read(descriptor: ActiveSchema) -> None:
    """SQL Server being down is not a reason to ignore what the graph holds.

    Refusing to look would turn a source outage into "this warehouse has no
    bays", which is wrong in the direction of claiming less than is known.
    """

    class _BrokenConnector(_Connector):
        async def targeted_read(self, **kwargs: Any) -> RawSourcePage:
            raise RuntimeError("source unreachable")

    observation = await _observations(
        descriptor, _Reader(_graph_rows("BAY-PPL-01")), _BrokenConnector()
    ).observe(WAREHOUSE)

    assert observation.evidence is BayEvidence.OBSERVED
    assert observation.candidates
    assert observation.sync_skipped_reason is not None
    assert observation.sync_skipped_reason.startswith("SYNC_FAILED_")


def test_the_evidence_reference_distinguishes_all_three_readings() -> None:
    """What lands in the audit trail. Codes, never prose -- these are validated
    as identifiers wherever they are recorded."""
    from return_platform.operations.warehouse.observations import WarehouseObservation

    absent = WarehouseObservation(
        warehouse_reference=None,
        evidence=BayEvidence.ABSENT,
        absent_reason="NO_WAREHOUSE_REFERENCE",
    )
    unavailable = WarehouseObservation(
        warehouse_reference=WAREHOUSE,
        evidence=BayEvidence.UNAVAILABLE,
        unavailable_reason="RUNTIMEERROR",
    )
    observed = WarehouseObservation(
        warehouse_reference=WAREHOUSE,
        evidence=BayEvidence.OBSERVED,
        graph_generation_id=GENERATION,
        candidates=({"bay_id": "BAY-PPL-01"},),
    )

    assert absent.evidence_reference == "WAREHOUSE_ABSENT:NO_WAREHOUSE_REFERENCE"
    assert unavailable.evidence_reference == "WAREHOUSE_UNAVAILABLE:RUNTIMEERROR"
    assert observed.evidence_reference == f"WAREHOUSE_OBSERVED:{GENERATION}:1"
    for reference in (absent, unavailable, observed):
        assert " " not in reference.evidence_reference
