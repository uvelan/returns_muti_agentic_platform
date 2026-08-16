"""Returnable quantity and the reservation state machine, without a database.

Everything here is a property of the *rules*, so a datastore would add nothing
but time: the arithmetic of plan sect. 12.2, the boundary that keeps its terms
from double counting, and the shape of the frozen lifecycle of plan sect. 12.3.
The properties a datastore *does* decide -- that two writers cannot both hold
the last unit, that an authorization and an expiry cannot both win -- are in
`test_order_line_reservations_real_infra.py`, where a fake would answer "yes" to
questions the production repository answers "no".

The source projection is exercised against two genuinely different shapes: a
real Ferguson `salesInv` line (`salesLnsEventData.lineNumber`,
`lineData.altCode1`, `lineData.netPrice`) and a seeded sandbox line, which
carries none of those. The point is that the reading comes from the active
schema and neither shape is special-cased.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.operations.models import CaseStatus
from return_platform.operations.order_lines import (
    DataInconsistency,
    HoldingKind,
    ReservationState,
    ReservationView,
    case_line_holdings,
    compute_line_availability,
    compute_order_line_availability,
    is_held,
    project_source_order_lines,
    read_reservation,
    reservation_transition_is_legal,
)
from return_platform.operations.order_lines.source import OrderLineSchemaUnavailableError

NOW = datetime(2026, 8, 15, 9, 30, tzinfo=UTC)
LINE = "1"
ORDER = "CQ363350"
TENANT = "tenant-a"


@pytest.fixture(scope="module")
def schema() -> ActiveSchema:
    """The shipped schema. The same file the runtime falls back to."""
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _item(
    case_id: str,
    *,
    line: str = LINE,
    quantity: int | None = 1,
    record_id: str | None = "rec-1",
) -> dict[str, Any]:
    return {
        "returnItemId": f"item-{case_id}-{line}",
        "caseId": case_id,
        "returnRecordId": record_id,
        "orderLineId": line,
        "quantity": quantity,
    }


def _reservation(
    case_id: str,
    *,
    line: str = LINE,
    quantity: int = 1,
    state: ReservationState = ReservationState.ACTIVE,
    expires_at: datetime | None = None,
) -> ReservationView:
    return ReservationView(
        reservation_id=f"res-{case_id}-{line}",
        tenant_id=TENANT,
        case_id=case_id,
        order_reference=ORDER,
        order_line_reference=line,
        quantity=quantity,
        state=state,
        expires_at=expires_at or (NOW + timedelta(minutes=30)),
    )


def _availability(
    *,
    ordered: int | None,
    items: list[dict[str, Any]] | None = None,
    statuses: dict[str, Any] | None = None,
    reservations: list[ReservationView] | None = None,
    viewing: str | None = "case-self",
) -> Any:
    return compute_line_availability(
        line_reference=LINE,
        ordered_quantity=ordered,
        holdings=case_line_holdings(
            line_reference=LINE,
            items=items or [],
            case_status_by_id=statuses or {},
            reservations=reservations or [],
            now=NOW,
        ),
        viewing_case_id=viewing,
    )


# ---------------------------------------------------------------------------
# The four terms
# ---------------------------------------------------------------------------


def test_an_untouched_line_is_returnable_in_full() -> None:
    assert _availability(ordered=3).returnable_quantity == 3


def test_a_completed_return_is_subtracted_and_named_as_completed() -> None:
    result = _availability(
        ordered=3,
        items=[_item("case-old", quantity=2)],
        statuses={"case-old": CaseStatus.CLOSED.value},
    )
    assert result.completed_return_quantity == 2
    assert result.open_authorized_quantity == 0
    assert result.returnable_quantity == 1


def test_an_authorized_return_still_running_is_open_not_completed() -> None:
    result = _availability(
        ordered=3,
        items=[_item("case-live", quantity=2)],
        statuses={"case-live": CaseStatus.AWAITING_SUPPORT.value},
    )
    assert result.open_authorized_quantity == 2
    assert result.completed_return_quantity == 0
    assert result.returnable_quantity == 1


@pytest.mark.parametrize(
    "status",
    [CaseStatus.CANCELLED.value, CaseStatus.POLICY_REJECTED.value],
)
def test_a_case_that_ended_without_returning_gives_the_quantity_back(status: str) -> None:
    """`UNSUCCESSFUL_TERMINAL_STATUSES` contributes to neither total.

    A cancelled return did not happen. Counting its lines as `open` would hold
    the units out of reach forever, and counting them as `completed` would claim
    goods came back that never did.
    """
    result = _availability(
        ordered=3,
        items=[_item("case-dead", quantity=3)],
        statuses={"case-dead": status},
    )
    assert (result.completed_return_quantity, result.open_authorized_quantity) == (0, 0)
    assert result.returnable_quantity == 3


def test_another_case_holding_the_line_subtracts_from_what_is_returnable() -> None:
    result = _availability(ordered=2, reservations=[_reservation("case-other", quantity=2)])
    assert result.active_reservation_quantity == 2
    assert result.returnable_quantity == 0


def test_an_expired_hold_subtracts_nothing_even_before_any_sweep_runs() -> None:
    """The leak the plan asks about. `is_held` reads the deadline, not the sweep."""
    stale = _reservation("case-other", quantity=2, expires_at=NOW - timedelta(seconds=1))
    assert is_held(stale, now=NOW) is False
    result = _availability(ordered=2, reservations=[stale])
    assert result.active_reservation_quantity == 0
    assert result.returnable_quantity == 2


# ---------------------------------------------------------------------------
# The boundary: never two terms for one unit
# ---------------------------------------------------------------------------


def test_completed_and_open_never_count_the_same_unit() -> None:
    """The plan's named requirement, asserted as arithmetic over the partition.

    Two cases, one completed and one running, each with two units on the same
    line of an order of six. The two terms must sum to exactly four -- not six,
    which is what a rule that counted an item under both readings would produce.
    """
    result = _availability(
        ordered=6,
        items=[_item("case-a", quantity=2), _item("case-b", quantity=2)],
        statuses={
            "case-a": CaseStatus.CLOSED.value,
            "case-b": CaseStatus.AWAITING_SUPPORT.value,
        },
    )
    assert result.completed_return_quantity == 2
    assert result.open_authorized_quantity == 2
    assert result.completed_return_quantity + result.open_authorized_quantity == 4
    assert result.returnable_quantity == 2


def test_a_case_whose_hold_was_authorized_is_counted_once_not_twice() -> None:
    """The item wins over the hold, so a stale `ACTIVE` cannot double count.

    The reservation would normally be `CONSUMED` in the same transaction as the
    assignment. This is the state that transaction exists to prevent, arranged
    by hand: the answer must still subtract two, not four.
    """
    result = _availability(
        ordered=4,
        items=[_item("case-a", quantity=2)],
        statuses={"case-a": CaseStatus.AWAITING_SUPPORT.value},
        reservations=[_reservation("case-a", quantity=2)],
    )
    holdings = case_line_holdings(
        line_reference=LINE,
        items=[_item("case-a", quantity=2)],
        case_status_by_id={"case-a": CaseStatus.AWAITING_SUPPORT.value},
        reservations=[_reservation("case-a", quantity=2)],
        now=NOW,
    )
    assert [holding.kind for holding in holdings] == [HoldingKind.OPEN_AUTHORIZED]
    assert result.active_reservation_quantity == 0
    assert result.returnable_quantity == 2


def test_one_case_contributes_at_most_one_holding_per_line() -> None:
    """The partition, stated directly: one case, one contribution."""
    holdings = case_line_holdings(
        line_reference=LINE,
        items=[_item("case-a", quantity=1), _item("case-b", quantity=1)],
        case_status_by_id={
            "case-a": CaseStatus.CLOSED.value,
            "case-b": CaseStatus.AWAITING_SUPPORT.value,
        },
        reservations=[_reservation("case-a"), _reservation("case-b"), _reservation("case-c")],
        now=NOW,
    )
    assert [holding.case_id for holding in holdings] == ["case-a", "case-b", "case-c"]
    assert len({holding.case_id for holding in holdings}) == len(holdings)


def test_an_unassigned_item_alone_holds_nothing() -> None:
    """A named line with no RMA and no hold is not a subtrahend.

    The plan's formula has three of them and an unassigned item is in none: the
    quantity it stands for is held by the reservation beside it, and counting
    the item as well would be the double count.
    """
    result = _availability(
        ordered=2,
        items=[_item("case-a", quantity=2, record_id=None)],
        statuses={"case-a": CaseStatus.GATHERING_INFO.value},
    )
    assert result.returnable_quantity == 2


# ---------------------------------------------------------------------------
# Self-reservation exclusion
# ---------------------------------------------------------------------------


def test_a_case_editing_its_own_hold_upward_does_not_reject_itself() -> None:
    """Plan sect. 12.3's self-reservation exclusion.

    The case already holds one of the two on the line. Raising it to two must
    see two returnable, not one -- otherwise the edit is refused by the hold it
    is replacing.
    """
    result = _availability(
        ordered=2,
        reservations=[_reservation("case-self", quantity=1)],
        viewing="case-self",
    )
    assert result.self_reserved_quantity == 1
    assert result.active_reservation_quantity == 0
    assert result.returnable_quantity == 2


def test_the_same_hold_seen_by_another_case_is_subtracted() -> None:
    """The exclusion is per-viewer, not a hole in the arithmetic."""
    result = _availability(
        ordered=2,
        reservations=[_reservation("case-self", quantity=1)],
        viewing="case-other",
    )
    assert result.active_reservation_quantity == 1
    assert result.returnable_quantity == 1


def test_a_case_does_not_get_its_own_authorized_quantity_back() -> None:
    """Only the *hold* is excluded. An authorized unit is gone from every case."""
    result = _availability(
        ordered=2,
        items=[_item("case-self", quantity=2)],
        statuses={"case-self": CaseStatus.AWAITING_SUPPORT.value},
        viewing="case-self",
    )
    assert result.returnable_quantity == 0


# ---------------------------------------------------------------------------
# Inconsistency, never a negative and never a silent clamp
# ---------------------------------------------------------------------------


def test_commitments_beyond_the_ordered_quantity_surface_zero_and_a_flag() -> None:
    result = _availability(
        ordered=1,
        items=[_item("case-a", quantity=2)],
        statuses={"case-a": CaseStatus.CLOSED.value},
    )
    assert result.returnable_quantity == 0
    assert result.data_inconsistency is DataInconsistency.COMMITMENTS_EXCEED_ORDERED_QUANTITY
    assert result.completed_return_quantity == 2, "the raw term is reported, not clamped with it"


def test_an_unknown_ordered_quantity_is_not_treated_as_zero_silently() -> None:
    result = _availability(ordered=None)
    assert result.returnable_quantity == 0
    assert result.data_inconsistency is DataInconsistency.ORDERED_QUANTITY_UNKNOWN


def test_an_unreadable_stored_quantity_counts_one_and_says_so() -> None:
    """Conservative *and* visible: it errs towards refusing, and it flags."""
    result = _availability(
        ordered=2,
        items=[_item("case-a", quantity=None)],
        statuses={"case-a": CaseStatus.AWAITING_SUPPORT.value},
    )
    assert result.open_authorized_quantity == 1
    assert result.data_inconsistency is DataInconsistency.HOLDING_QUANTITY_UNREADABLE


def test_a_case_status_with_no_projection_is_treated_as_still_committed() -> None:
    result = _availability(
        ordered=2,
        items=[_item("case-a", quantity=1)],
        statuses={"case-a": "SOMETHING_NOBODY_MAPPED"},
    )
    assert result.open_authorized_quantity == 1
    assert result.data_inconsistency is DataInconsistency.CASE_STATUS_UNREADABLE


def test_a_line_the_order_does_not_have_is_not_invented_into_a_row() -> None:
    lines = compute_order_line_availability(
        ordered_by_line={"1": 2},
        items=[_item("case-a", line="7", quantity=1)],
        case_status_by_id={"case-a": CaseStatus.AWAITING_SUPPORT.value},
        reservations=[_reservation("case-b", line="7")],
        now=NOW,
        viewing_case_id="case-self",
    )
    assert set(lines) == {"1"}
    assert lines["1"].returnable_quantity == 2


# ---------------------------------------------------------------------------
# The frozen lifecycle (plan sect. 12.3)
# ---------------------------------------------------------------------------


def test_the_state_set_is_exactly_the_four_the_plan_freezes() -> None:
    assert {state.value for state in ReservationState} == {
        "ACTIVE",
        "CONSUMED",
        "RELEASED",
        "EXPIRED",
    }


@pytest.mark.parametrize(
    "target",
    [ReservationState.CONSUMED, ReservationState.RELEASED, ReservationState.EXPIRED],
)
def test_active_may_reach_every_settled_state(target: ReservationState) -> None:
    assert reservation_transition_is_legal(ReservationState.ACTIVE, target) is True


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ReservationState.EXPIRED, ReservationState.CONSUMED),
        (ReservationState.RELEASED, ReservationState.CONSUMED),
        (ReservationState.CONSUMED, ReservationState.EXPIRED),
    ],
)
def test_the_three_forbidden_transitions_are_forbidden(
    current: ReservationState, target: ReservationState
) -> None:
    """The plan names these three by hand. They are absent from the graph."""
    assert reservation_transition_is_legal(current, target) is False


def test_no_settled_state_has_any_outgoing_edge_at_all() -> None:
    """Stronger than the three: settled is settled, in every direction."""
    for current in ReservationState:
        if current is ReservationState.ACTIVE:
            continue
        for target in ReservationState:
            assert reservation_transition_is_legal(current, target) is False


def test_reasserting_a_state_is_not_a_legal_transition() -> None:
    for state in ReservationState:
        assert reservation_transition_is_legal(state, state) is False


def test_an_unreadable_reservation_document_is_dropped_not_defaulted() -> None:
    assert read_reservation({"state": "NOT_A_STATE"}) is None
    assert read_reservation({"state": "ACTIVE", "expiresAt": "not a datetime"}) is None
    assert (
        read_reservation(
            {
                "reservationId": "r",
                "tenantId": TENANT,
                "caseId": "c",
                "orderReference": ORDER,
                "orderLineReference": LINE,
                "quantity": 0,
                "state": "ACTIVE",
                "expiresAt": NOW,
            }
        )
        is None
    )


# ---------------------------------------------------------------------------
# The source projection, driven by the schema and not by a literal
# ---------------------------------------------------------------------------


def _ferguson_document() -> dict[str, Any]:
    """A real `salesInv` shape, trimmed to the keys the entity declares."""
    return {
        "salesHdrEventData": {"orderId": ORDER, "accountId": "CHARLOTTE"},
        "salesLines": [
            {
                "salesLnsEventData": {"lineNumber": "1", "lineType": "MP", "account": "CHARLOTTE"},
                "lineData": {
                    "altCode1": "Q1685",
                    "productDesc": "16X25 SILV FLEX AIR DUCT R8.0",
                    "masterProductId": "3180140",
                    "productId": "3180140*1969",
                    "orderQty": 3,
                    "shipQty": 3,
                    "netPrice": 146.306,
                    "invenWhse": "1969",
                },
            },
            {
                "salesLnsEventData": {"lineNumber": "2", "lineType": "CB"},
                "lineData": {"productDesc": "DELIVER TO SIDE GATE"},
            },
        ],
    }


def test_a_real_sales_line_projects_every_field_the_pane_needs(schema: ActiveSchema) -> None:
    lines = project_source_order_lines(schema, _ferguson_document())
    assert [line.line_reference for line in lines] == ["1", "2"]
    first = lines[0]
    assert first.sku == "Q1685"
    assert first.description == "16X25 SILV FLEX AIR DUCT R8.0"
    assert first.ordered_quantity == 3
    assert first.product_reference == "3180140"
    assert first.unit_price == Decimal("146.306")


def test_a_price_is_a_decimal_not_a_float(schema: ActiveSchema) -> None:
    """A refund basis that round-trips through binary floating point is wrong.

    `Decimal(str(146.306))` is exact; `Decimal(146.306)` is not, and the
    difference reaches an invoice.
    """
    line = project_source_order_lines(schema, _ferguson_document())[0]
    assert isinstance(line.unit_price, Decimal)
    assert str(line.unit_price) == "146.306"


def test_a_comment_line_projects_with_no_quantity_rather_than_being_hidden(
    schema: ActiveSchema,
) -> None:
    """Filtering `line_type` here would be this module deciding what is returnable."""
    comment = project_source_order_lines(schema, _ferguson_document())[1]
    assert comment.ordered_quantity is None
    assert comment.unit_price is None


def test_a_seeded_line_with_no_line_number_falls_back_to_its_position(
    schema: ActiveSchema,
) -> None:
    """The sandbox shape carries no `salesLnsEventData` at all.

    It must still project: the order's own array order is the only other
    identity the document offers, and a line that could not be referenced could
    not be selected, reserved or audited.
    """
    document = {
        "salesHdrEventData": {"orderId": "CW273354"},
        "salesLines": [
            {"lineData": {"productDesc": "Emerson pump", "orderQty": 1, "masterProductId": "M-1"}},
            {"lineData": {"productDesc": "Flange", "orderQty": 4, "masterProductId": "M-2"}},
        ],
    }
    lines = project_source_order_lines(schema, document)
    assert [line.line_reference for line in lines] == ["1", "2"]
    assert [line.ordered_quantity for line in lines] == [1, 4]
    assert [line.sku for line in lines] == [None, None], "altCode1 is absent, so the SKU is absent"
    assert [line.unit_price for line in lines] == [None, None]


def test_an_order_with_no_lines_projects_nothing_rather_than_raising(
    schema: ActiveSchema,
) -> None:
    assert project_source_order_lines(schema, {"salesHdrEventData": {"orderId": ORDER}}) == ()


def test_a_schema_without_the_entity_refuses_rather_than_answering_empty(
    schema: ActiveSchema,
) -> None:
    """An empty list would present a configuration fault as an empty order."""
    without = schema.model_copy(
        update={"entities": {k: v for k, v in schema.entities.items() if k != "order_line"}}
    )
    with pytest.raises(OrderLineSchemaUnavailableError):
        project_source_order_lines(without, _ferguson_document())
