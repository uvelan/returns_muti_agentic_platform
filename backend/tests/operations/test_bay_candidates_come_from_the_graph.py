"""W2.7: `WarehousePlacementService` ranks what the graph observed.

The SQL bay query is the direct source read R2 forbids from an agent path. What
matters below is not that it is slower -- it is that it goes round every guard,
every field allowlist and every generation fence the graph read passes through,
and that it answered "which bays may take this parcel" with a predicate that
collapsed to true.

`SQLBusinessStateRepository` is stubbed with a repository that fails the test if
`list_bay_candidates` is called at all. A fallback that quietly reappeared would
otherwise pass every assertion here.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.operations.warehouse.observations import (
    BayEvidence,
    WarehouseObservation,
)
from return_platform.operations.warehouse.service import (
    _candidate_of,
    _permits,
    _string_list,
)


def _row(bay_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "bay_id": bay_id,
        "bay_type": "PPL",
        "warehouse_id": "WH-CHENNAI-01",
        "branch_id": "BR-CHENNAI",
        "active": True,
        "priority": 10,
        "supported_shipping_paths": '["PPL"]',
        "supported_product_types": '["STANDARD","BULKY"]',
        "hazardous_allowed": False,
        "oversized_allowed": False,
        "max_package_count": 50,
        "max_handling_unit_count": 40,
        "capacity_unit": "HANDLING_UNIT",
        **overrides,
    }


class _RefusingSql:
    """Any bay read through this is the bypass coming back."""

    async def list_bay_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError(
            "bay candidates were read from SQL Server; W2.7 removed that path from "
            f"the agent flow (arguments: {kwargs})"
        )


class _Observations:
    def __init__(self, observation: WarehouseObservation) -> None:
        self.observation = observation
        self.asked: list[str | None] = []

    async def observe(self, warehouse_reference: str | None) -> WarehouseObservation:
        self.asked.append(warehouse_reference)
        return self.observation


async def _candidates(
    service: Any, *, warehouse_id: str | None, return_method: str = "BRANCH_UPS"
) -> tuple[WarehouseObservation | None, list[dict[str, Any]]]:
    return await service._candidates(
        warehouse_id=warehouse_id,
        return_method=return_method,
        product_type="STANDARD",
    )


def _service(observation: WarehouseObservation) -> Any:
    """A placement service with only the two collaborators `_candidates` uses.

    Constructed without `__init__` because the real one builds an agent registry
    from a loaded configuration, and nothing here reaches the agent.
    """
    from return_platform.operations.warehouse.service import WarehousePlacementService

    service = object.__new__(WarehousePlacementService)
    service._sql = _RefusingSql()  # type: ignore[attr-defined]
    service._observations = _Observations(observation)  # type: ignore[attr-defined]
    return service


@pytest.mark.asyncio
async def test_an_absent_warehouse_yields_no_candidates_and_never_reads_sql() -> None:
    """The whole defect, in one assertion.

    A return with no processing warehouse used to be handed every bay in the
    estate to rank, because the SQL predicate treated a NULL as "no constraint".
    """
    service = _service(
        WarehouseObservation(
            warehouse_reference=None,
            evidence=BayEvidence.ABSENT,
            absent_reason="NO_WAREHOUSE_REFERENCE",
        )
    )

    observation, candidates = await _candidates(service, warehouse_id=None)

    assert candidates == []
    assert observation is not None
    assert observation.evidence is BayEvidence.ABSENT


@pytest.mark.asyncio
async def test_an_unreadable_graph_yields_no_candidates_rather_than_a_sql_fallback() -> None:
    """`best_effort` means "record the reason and continue", not "read the source".

    Falling back would make the removal a comment: the agent would be reading
    SQL directly again, on exactly the occasions when nobody is watching.
    """
    service = _service(
        WarehouseObservation(
            warehouse_reference="WH-CHENNAI-01",
            evidence=BayEvidence.UNAVAILABLE,
            unavailable_reason="RUNTIMEERROR",
        )
    )

    observation, candidates = await _candidates(service, warehouse_id="WH-CHENNAI-01")

    assert candidates == []
    assert observation is not None
    assert observation.evidence is BayEvidence.UNAVAILABLE


@pytest.mark.asyncio
async def test_a_graph_outage_becomes_a_reading_rather_than_a_failed_request() -> None:
    """The step's Failure condition: bay is `best_effort` and never parks the case.

    `GraphWarehouseBayObservations.observe` raises when the graph cannot be read
    at all -- reporting ABSENT on an outage would look like data. Somebody has to
    own the policy that turns that into a state, and it is this layer: letting it
    propagate would 500 the bay-recommendation endpoint over a stage the policy
    says may be skipped.
    """

    class _Broken:
        async def observe(self, warehouse_reference: str | None) -> WarehouseObservation:
            raise ConnectionRefusedError("bolt connection refused")

    from return_platform.operations.warehouse.service import WarehousePlacementService

    service = object.__new__(WarehousePlacementService)
    service._sql = _RefusingSql()  # type: ignore[attr-defined]
    service._observations = _Broken()  # type: ignore[attr-defined]

    observation, candidates = await _candidates(service, warehouse_id="WH-CHENNAI-01")

    assert candidates == []
    assert observation is not None
    assert observation.evidence is BayEvidence.UNAVAILABLE
    assert observation.evidence_reference == "WAREHOUSE_UNAVAILABLE:CONNECTIONREFUSEDERROR"


@pytest.mark.asyncio
async def test_observed_bays_are_filtered_and_ordered_the_way_the_sql_query_was() -> None:
    """Eligibility and ordering move with the read, not away from it.

    Priority ascending then bay id, because two identical requests producing
    different recommendations is indistinguishable from the agent changing its
    mind.
    """
    service = _service(
        WarehouseObservation(
            warehouse_reference="WH-CHENNAI-01",
            evidence=BayEvidence.OBSERVED,
            graph_generation_id="gen-1",
            candidates=(
                _row("BAY-PPL-02", priority=20),
                _row("BAY-PPL-01", priority=10),
                _row("BAY-BOL-01", supported_shipping_paths='["BOL"]'),
                _row("BAY-HAZ-01", supported_product_types='["HAZARDOUS_REVIEW"]'),
            ),
        )
    )

    _, candidates = await _candidates(service, warehouse_id="WH-CHENNAI-01")

    assert [candidate["bayId"] for candidate in candidates] == ["BAY-PPL-01", "BAY-PPL-02"]


def test_the_shipping_path_aliases_the_sql_query_carried_are_preserved() -> None:
    """`PPL` and `BOL` are the codes bay configuration was written with.

    A bay row declaring the old code still means the new method, and re-deriving
    that mapping would be a second answer to a question the source answers one
    way.
    """
    assert _permits(("PPL",), "BRANCH_UPS")
    assert _permits(("BOL",), "BRANCH_LTL")
    assert _permits(("BOL",), "OFFSITE_LTL")
    assert not _permits(("PPL",), "BRANCH_LTL")
    # A bay stating no restriction accepts everything, as the SQL path had it.
    assert _permits((), "ANYTHING")


def test_a_json_column_that_will_not_parse_states_no_restriction() -> None:
    assert _string_list('["PPL","BOL"]') == ("PPL", "BOL")
    assert _string_list(None) == ()
    assert _string_list("not json") == ()


def test_capacity_is_the_declared_maximum_and_the_field_names_say_which() -> None:
    """The reservation deduction is gone, and this is where that shows.

    The SQL query subtracted `SUM(reserved_capacity)` over unexpired
    reservations -- a figure that changes with the clock rather than with any
    source write, so no sync makes a graph node current for it. Over-recommending
    is caught in `reserve_and_assign_handling_unit`, which is the only place
    holding a lock over the decision.
    """
    assert _candidate_of(_row("BAY-PPL-01"), "BRANCH_UPS")["capacityAvailable"] == 40
    # Falls back to the package count where no handling-unit maximum is set,
    # matching the COALESCE the SQL query used.
    assert (
        _candidate_of(_row("BAY-PPL-01", max_handling_unit_count=None), "BRANCH_UPS")[
            "capacityAvailable"
        ]
        == 50
    )


def test_a_bit_column_arriving_as_text_is_not_read_as_true() -> None:
    """`bool("0")` is `True`, which would make every bay hazmat-capable.

    Neo4j stores what the projector wrote and pymssql yields a bit as `0`/`1` or
    a boolean depending on the path, so the string spellings are enumerated
    rather than coerced.
    """
    assert not _candidate_of(_row("BAY-1", hazardous_allowed="0"), "BRANCH_UPS")[
        "supportsHazardous"
    ]
    assert _candidate_of(_row("BAY-1", hazardous_allowed="1"), "BRANCH_UPS")["supportsHazardous"]
    assert _candidate_of(_row("BAY-1", hazardous_allowed=1), "BRANCH_UPS")["supportsHazardous"]
    assert not _candidate_of(_row("BAY-1", hazardous_allowed=0), "BRANCH_UPS")["supportsHazardous"]
