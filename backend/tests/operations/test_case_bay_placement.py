"""Bay placement, keyed by case and answering with one coherent result (BAY-01).

What was here before this commit was `request_bay_assignment` writing a single
`bay_assignment_requested` fact -- no graph read, no ranking, no location, no
confidence -- and a workflow waiting `bay_wait_seconds` for a signal nothing
sent. Bay was correctly declared best-effort and there was nothing to be
best-effort about.

These scenarios run the real `CaseBayPlacement`, the real
`observe_eligible_bays` pipeline the session path uses, and the real
`BayAssignmentAgent` loaded from `config/returns/production.yaml`. Only the two
datastore edges are doubled: the case repository and the warehouse observation
port. The confidence assertions in particular depend on the real agent -- a
double would let a constant pass, which is precisely what contract C2 forbids.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.operations.warehouse.case_placement import (
    FACT_HAZARDOUS,
    FACT_PHYSICAL_STATUS,
    FACT_REQUIRED_CAPACITY,
    FACT_WAREHOUSE,
    CaseBayPlacement,
)
from return_platform.operations.warehouse.observations import (
    BayEvidence,
    WarehouseObservation,
)
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import RequestBayAssignmentInput

pytestmark = pytest.mark.asyncio

CASE_ID = "case-bay-1"
ORDER_REFERENCE = "CW273354"
WAREHOUSE = "WHSE-7"
RECEIVED = "WAREHOUSE_RECEIVED"


@pytest.fixture(scope="module")
def production_configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(Path("config/returns/production.yaml")).configuration


@pytest.fixture
def prearrival_allowed(
    production_configuration: ReturnPlatformConfiguration,
) -> ReturnPlatformConfiguration:
    """Production, with the one flag that governs the case path turned on.

    `allow_prearrival_reservation` is the configured answer to "may a case get
    a bay before the goods arrive". Production says no, so most scenarios below
    record a physical status instead; this fixture exists for the scenario that
    asserts the flag itself is read.
    """
    return production_configuration.model_copy(
        update={
            "bay": production_configuration.bay.model_copy(
                update={"allow_prearrival_reservation": True}
            )
        }
    )


def _bay(
    bay_id: str,
    *,
    capacity: int,
    priority: int = 1,
    active: bool = True,
    hazardous: bool = True,
    oversized: bool = True,
) -> dict[str, Any]:
    """One row shaped as `bay_configuration` reaches the graph."""
    return {
        "bay_id": bay_id,
        "bay_name": f"Bay {bay_id}",
        "bay_type": "RETURNS",
        "warehouse_id": WAREHOUSE,
        "branch_id": "CHARLOTTE",
        "active": active,
        "priority": priority,
        "supported_shipping_paths": "[]",
        "supported_product_types": "[]",
        "hazardous_allowed": hazardous,
        "oversized_allowed": oversized,
        "max_handling_unit_count": capacity,
        "capacity_unit": "HANDLING_UNIT",
    }


class FakeCaseRepository:
    def __init__(self, facts: dict[str, Any] | None = None, *, case: dict[str, Any] | None = None):
        self.case = case if case is not None else {
            "caseId": CASE_ID,
            "tenantId": "tenant-a",
            "confirmedOrderReference": ORDER_REFERENCE,
        }
        self._facts = dict(facts or {})
        self.decisions: list[dict[str, Any]] = []

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self.case if case_id == str(self.case["caseId"]) else None

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        del case_id
        return {name: {"value": value} for name, value in self._facts.items()}

    async def persist_agent_decision(self, **fields: Any) -> dict[str, Any]:
        self.decisions.append(fields)
        return fields


class FakeObservations:
    """The warehouse reading, with the three outcomes kept apart."""

    def __init__(self, observation: WarehouseObservation | None = None, *, error: Exception | None = None):
        self._observation = observation
        self._error = error
        self.observed: list[str | None] = []

    async def observe(self, warehouse_reference: str | None) -> WarehouseObservation:
        self.observed.append(warehouse_reference)
        if self._error is not None:
            raise self._error
        if warehouse_reference is None:
            return WarehouseObservation(
                warehouse_reference=None,
                evidence=BayEvidence.ABSENT,
                absent_reason="NO_WAREHOUSE_REFERENCE",
            )
        assert self._observation is not None
        return self._observation


class FakeOrderObservations:
    def __init__(self, warehouse: str | None) -> None:
        self._warehouse = warehouse
        self.asked: list[str] = []

    async def observe_shipping_warehouse(self, order_reference: str) -> str | None:
        self.asked.append(order_reference)
        return self._warehouse


def _observed(*rows: dict[str, Any]) -> WarehouseObservation:
    return WarehouseObservation(
        warehouse_reference=WAREHOUSE,
        evidence=BayEvidence.OBSERVED,
        graph_generation_id="gen-bay-1",
        candidates=rows,
        sync_request_id="sync-1",
    )


def _placement(
    configuration: ReturnPlatformConfiguration,
    repository: FakeCaseRepository,
    observations: Any,
    order_observations: Any = None,
) -> CaseBayPlacement:
    return CaseBayPlacement(
        repository=repository,
        configuration=configuration,
        observations=observations,
        order_observations=order_observations,
    )


async def test_a_received_case_gets_one_coherent_recommendation(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """Contract C2, as a single assertion block.

    Warehouse, bay, return location, confidence, reason, explanation and the
    evidence reference all come back together. A caller that had to join any of
    these from somewhere else would be the partial result C2 forbids.
    """
    repository = FakeCaseRepository(
        {FACT_WAREHOUSE: WAREHOUSE, FACT_PHYSICAL_STATUS: RECEIVED}
    )
    observations = FakeObservations(
        _observed(_bay("B-1", capacity=4, priority=1), _bay("B-2", capacity=9, priority=2))
    )

    result = await _placement(production_configuration, repository, observations).recommend(
        CASE_ID
    )

    assert result.warehouse_reference == WAREHOUSE
    # The tightest fit that still holds the return -- the agent's own ranking.
    assert result.bay_reference == "B-1"
    assert result.return_location == f"{WAREHOUSE}/B-1"
    assert result.reason == "RECOMMENDED"
    assert result.explanation
    assert result.evidence_reference == "WAREHOUSE_OBSERVED:gen-bay-1:2"
    assert result.graph_generation_id == "gen-bay-1"
    assert result.eligible_bay_ids == ("B-1", "B-2")
    assert result.confidence_millionths is not None
    assert 0 < result.confidence_millionths <= 1_000_000
    assert repository.decisions, "the ranking is recorded against the case, not a handling unit"
    assert repository.decisions[0]["aggregate_id"] == CASE_ID


async def test_confidence_is_computed_from_the_field_not_a_constant(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """C2's sharpest clause: no constant confidence.

    A sole candidate is certain because nothing could have been preferred to
    it. Two near-identical bays are close to a coin toss. If both produced the
    same number, `confidence` would be decoration.
    """

    async def confidence_for(*rows: dict[str, Any]) -> int | None:
        repository = FakeCaseRepository(
            {
                FACT_WAREHOUSE: WAREHOUSE,
                FACT_PHYSICAL_STATUS: RECEIVED,
                FACT_REQUIRED_CAPACITY: 4,
            }
        )
        result = await _placement(
            production_configuration, repository, FakeObservations(_observed(*rows))
        ).recommend(CASE_ID)
        return result.confidence_millionths

    sole = await confidence_for(_bay("B-1", capacity=8))
    tie = await confidence_for(_bay("B-1", capacity=8), _bay("B-2", capacity=8, priority=2))
    narrow = await confidence_for(_bay("B-1", capacity=8), _bay("B-2", capacity=10, priority=2))
    clear = await confidence_for(_bay("B-1", capacity=8), _bay("B-2", capacity=99, priority=2))

    assert sole == 1_000_000, "nothing to weigh it against"
    assert tie == 500_000, "two identical bays are a coin toss"
    # Scaled by what the return needs: a two-unit edge on a four-unit return is
    # half the available margin, and a ninety-unit edge saturates.
    assert narrow == 750_000
    assert clear == 1_000_000
    assert len({tie, narrow, clear}) == 3, "the same field cannot produce one constant"


async def test_no_warehouse_reference_is_a_stated_reason_not_every_bay(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """The defect `bay_observations` was written to close, on the case path.

    A case whose warehouse cannot be resolved must produce no candidates and
    say so -- never the whole estate.
    """
    repository = FakeCaseRepository({FACT_PHYSICAL_STATUS: RECEIVED}, case={"caseId": CASE_ID})
    observations = FakeObservations()

    result = await _placement(production_configuration, repository, observations).recommend(
        CASE_ID
    )

    assert observations.observed == [None]
    assert result.bay_reference is None
    assert result.return_location is None
    assert result.confidence_millionths is None, "no recommendation to be confident about"
    assert result.reason == "WAREHOUSE_ABSENT_NO_WAREHOUSE_REFERENCE"


async def test_a_warehouse_the_graph_does_not_hold_is_told_apart_from_a_full_one(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    repository = FakeCaseRepository(
        {FACT_WAREHOUSE: WAREHOUSE, FACT_PHYSICAL_STATUS: RECEIVED}
    )
    observations = FakeObservations(
        WarehouseObservation(
            warehouse_reference=WAREHOUSE,
            evidence=BayEvidence.ABSENT,
            graph_generation_id="gen-bay-1",
            absent_reason="WAREHOUSE_NOT_IN_GRAPH",
        )
    )

    result = await _placement(production_configuration, repository, observations).recommend(
        CASE_ID
    )

    assert result.reason == "WAREHOUSE_ABSENT_WAREHOUSE_NOT_IN_GRAPH"
    assert result.evidence_reference == "WAREHOUSE_ABSENT:WAREHOUSE_NOT_IN_GRAPH"


async def test_an_unreadable_graph_never_reads_as_an_empty_warehouse(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """Best-effort, preserved exactly: a graph outage is a reason, not a raise.

    Reporting `ABSENT` here would mark every return's bay omitted across the
    deployment and look like configuration.
    """
    repository = FakeCaseRepository(
        {FACT_WAREHOUSE: WAREHOUSE, FACT_PHYSICAL_STATUS: RECEIVED}
    )
    observations = FakeObservations(error=ConnectionRefusedError("neo4j is down"))

    result = await _placement(production_configuration, repository, observations).recommend(
        CASE_ID
    )

    assert result.bay_reference is None
    assert result.reason == "WAREHOUSE_UNAVAILABLE_CONNECTIONREFUSEDERROR"
    assert result.evidence_reference == "WAREHOUSE_UNAVAILABLE:CONNECTIONREFUSEDERROR"


async def test_a_bay_that_cannot_take_the_return_is_excluded_with_its_own_reason(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """An estate that is real but ineligible is a third distinct outcome."""
    repository = FakeCaseRepository(
        {
            FACT_WAREHOUSE: WAREHOUSE,
            FACT_PHYSICAL_STATUS: RECEIVED,
            FACT_REQUIRED_CAPACITY: 10,
            FACT_HAZARDOUS: True,
        }
    )
    observations = FakeObservations(
        _observed(_bay("B-1", capacity=2), _bay("B-2", capacity=50, hazardous=False, priority=2))
    )

    result = await _placement(production_configuration, repository, observations).recommend(
        CASE_ID
    )

    assert result.bay_reference is None
    assert result.reason == "NO_ELIGIBLE_BAY"
    assert result.confidence_millionths is None
    # The warehouse WAS observed -- that is what makes this different from the
    # two absences above, and the evidence reference is what says so.
    assert result.evidence_reference == "WAREHOUSE_OBSERVED:gen-bay-1:2"


async def test_the_warehouse_falls_back_to_the_confirmed_order(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """The re-keying, in one assertion.

    A session carries `processingWarehouseReference`; a case does not. The
    order the associate confirmed shipped from somewhere, and that is the
    honest default rather than a guess about an unrelated site.
    """
    repository = FakeCaseRepository({FACT_PHYSICAL_STATUS: RECEIVED})
    observations = FakeObservations(_observed(_bay("B-1", capacity=4)))
    orders = FakeOrderObservations(WAREHOUSE)

    result = await _placement(
        production_configuration, repository, observations, orders
    ).recommend(CASE_ID)

    assert orders.asked == [ORDER_REFERENCE]
    assert observations.observed == [WAREHOUSE]
    assert result.warehouse_reference == WAREHOUSE
    assert result.bay_reference == "B-1"


async def test_a_recorded_warehouse_outranks_the_order(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """Somebody stating where this return is processed beats anything inferred."""
    repository = FakeCaseRepository(
        {FACT_WAREHOUSE: WAREHOUSE, FACT_PHYSICAL_STATUS: RECEIVED}
    )
    observations = FakeObservations(_observed(_bay("B-1", capacity=4)))
    orders = FakeOrderObservations("WHSE-SOMEWHERE-ELSE")

    await _placement(production_configuration, repository, observations, orders).recommend(
        CASE_ID
    )

    assert orders.asked == [], "the order was never consulted"
    assert observations.observed == [WAREHOUSE]


async def test_production_refuses_a_pre_arrival_case_and_says_why(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """Bay runs before anything is received, and configuration governs that.

    `allow_prearrival_reservation` is false in production, so the answer is a
    stated refusal rather than a recommendation -- and it is given before any
    targeted sync, because configuration has already answered.
    """
    repository = FakeCaseRepository()
    observations = FakeObservations(_observed(_bay("B-1", capacity=4)))

    result = await _placement(production_configuration, repository, observations).recommend(
        CASE_ID
    )

    assert result.reason == "PRE_ARRIVAL_NOT_ALLOWED"
    assert result.bay_reference is None
    assert result.explanation
    assert observations.observed == [], "no warehouse sync was paid for"


async def test_enabling_pre_arrival_reservation_changes_the_answer_with_no_code_edit(
    prearrival_allowed: ReturnPlatformConfiguration,
) -> None:
    """The flag was declared and read by nothing until the case path needed it.

    With pre-arrival allowed, production's `require_physical_receipt` still
    refuses -- two configured gates that contradict each other. That is
    surfaced by its own name rather than folded into `NO_ELIGIBLE_BAY`, because
    the fix is a configuration change and nothing about the estate.
    """
    repository = FakeCaseRepository()
    observations = FakeObservations(_observed(_bay("B-1", capacity=4)))

    result = await _placement(prearrival_allowed, repository, observations).recommend(CASE_ID)

    assert result.reason == "PHYSICAL_RECEIPT_REQUIRED"
    assert result.bay_reference is None


async def test_pre_arrival_placement_recommends_when_both_gates_agree(
    prearrival_allowed: ReturnPlatformConfiguration,
) -> None:
    """The configuration a deployment that wants pre-arrival bays would set."""
    configuration = prearrival_allowed.model_copy(
        update={
            "bay": prearrival_allowed.bay.model_copy(update={"require_physical_receipt": False})
        }
    )
    repository = FakeCaseRepository({FACT_WAREHOUSE: WAREHOUSE})
    observations = FakeObservations(_observed(_bay("B-1", capacity=4)))

    result = await _placement(configuration, repository, observations).recommend(CASE_ID)

    assert result.bay_reference == "B-1"
    assert result.return_location == f"{WAREHOUSE}/B-1"
    assert result.confidence_millionths == 1_000_000


async def test_placement_refuses_a_case_that_does_not_exist(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """A missing case is a caller error, not a bay state."""
    repository = FakeCaseRepository()

    with pytest.raises(KeyError):
        await _placement(production_configuration, repository, FakeObservations()).recommend(
            "case-nope"
        )


# ---------------------------------------------------------------------------
# The activity the workflow actually runs
# ---------------------------------------------------------------------------


class RecordingFactRepository:
    def __init__(self) -> None:
        self.facts: dict[str, Any] = {}

    async def append_case_fact(self, **fields: Any) -> dict[str, Any]:
        self.facts[str(fields["fact_name"])] = fields["value"]
        return fields


class StubPlacement:
    def __init__(self, recommendation: Any) -> None:
        self._recommendation = recommendation
        self.calls: list[str] = []

    async def recommend(self, case_id: str) -> Any:
        self.calls.append(case_id)
        return self._recommendation


async def test_the_activity_answers_with_the_whole_result_and_records_it(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """The BAY-01 defect, inverted.

    `request_bay_assignment` used to append `bay_assignment_requested` and
    return None. It now returns a `BayResultNotice` carrying every C2 field,
    and writes the recommendation onto the case so the associate's next turn
    reads it out of the fact projection with no second query.
    """
    repository = FakeCaseRepository(
        {FACT_WAREHOUSE: WAREHOUSE, FACT_PHYSICAL_STATUS: RECEIVED}
    )
    recommendation = await _placement(
        production_configuration, repository, FakeObservations(_observed(_bay("B-1", capacity=4)))
    ).recommend(CASE_ID)

    facts = RecordingFactRepository()
    activities = ReturnCaseActivities(
        repository=facts,  # type: ignore[arg-type]
        support_service=None,
        bay_placement=StubPlacement(recommendation),
    )

    notice = await activities.request_bay_assignment(
        RequestBayAssignmentInput(case_id=CASE_ID, tenant_id="tenant-a")
    )

    assert notice.bay_reference == "B-1"
    assert notice.warehouse_reference == WAREHOUSE
    assert notice.return_location == f"{WAREHOUSE}/B-1"
    assert notice.confidence_millionths == 1_000_000
    assert notice.reason == "RECOMMENDED"
    assert notice.explanation
    assert notice.evidence_reference == "WAREHOUSE_OBSERVED:gen-bay-1:1"
    assert notice.graph_generation_id == "gen-bay-1"

    assert facts.facts["bay_assignment_requested"] is True
    assert facts.facts["bay_reference"] == "B-1"
    assert facts.facts["bay_return_location"] == f"{WAREHOUSE}/B-1"
    assert facts.facts["bay_confidence_millionths"] == 1_000_000
    assert facts.facts["bay_evidence_reference"] == "WAREHOUSE_OBSERVED:gen-bay-1:1"


async def test_a_worker_registered_without_placement_says_so() -> None:
    """Named, not silent. Otherwise every case gets the same empty bay."""
    facts = RecordingFactRepository()
    activities = ReturnCaseActivities(
        repository=facts,  # type: ignore[arg-type]
        support_service=None,
    )

    notice = await activities.request_bay_assignment(
        RequestBayAssignmentInput(case_id=CASE_ID, tenant_id="tenant-a")
    )

    assert notice.reason == "BAY_PLACEMENT_NOT_CONFIGURED"
    assert notice.bay_reference is None


async def test_the_activity_never_raises_for_a_bay_state(
    production_configuration: ReturnPlatformConfiguration,
) -> None:
    """Best-effort, at the boundary the workflow's ActivityError branch guards.

    A graph outage is a reason on the notice. If it escaped as an exception the
    workflow would record `REQUEST_FAILED` and lose which state applied -- and
    the audit is explicit that the best-effort semantics here are correct.
    """
    repository = FakeCaseRepository(
        {FACT_WAREHOUSE: WAREHOUSE, FACT_PHYSICAL_STATUS: RECEIVED}
    )
    recommendation = await _placement(
        production_configuration,
        repository,
        FakeObservations(error=ConnectionRefusedError("neo4j is down")),
    ).recommend(CASE_ID)

    activities = ReturnCaseActivities(
        repository=RecordingFactRepository(),  # type: ignore[arg-type]
        support_service=None,
        bay_placement=StubPlacement(recommendation),
    )

    notice = await activities.request_bay_assignment(
        RequestBayAssignmentInput(case_id=CASE_ID, tenant_id="tenant-a")
    )

    assert notice.reason == "WAREHOUSE_UNAVAILABLE_CONNECTIONREFUSEDERROR"
    assert notice.bay_reference is None
