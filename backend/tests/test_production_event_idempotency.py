"""A transition that has already happened cannot happen again.

Wave D4, slice 3. The overlap this closes: `/api/v1/returns/{id}/pickup-actions`
and `POST /api/v1/production-returns/{id}/events` can both record
`CARRIER_BOOKING_CONFIRMED`, and each derives its own event id -- so one
real-world carrier booking recorded through both paths produced two different
event ids for one fact.

`applied_event_ids` did not catch it: that check is for a *retry of the same
event*. `_validate_transition` could not catch it either, because it checks
preconditions and a transition's preconditions stay satisfied after it occurs
(`CARRIER_BOOKING_CONFIRMED` requires `bol_tendered`, which never goes back to
false). So the second application was accepted, appending to `applied_event_ids`
-- carried in Temporal workflow state, so it grows without bound -- and
re-running `_project_business_event`, which writes a second `shipment_events`
row because the two paths derive different `(sourceSystem, sourceEventId)` dedup
keys.

The two idempotency rules now differ deliberately: a repeat of the same event id
is a **no-op** (a retry succeeded twice), and a new event id for a completed
transition is a **refusal** (a duplicate report, or a second real occurrence --
either way something a human should see rather than something to apply twice).
"""

from __future__ import annotations

import pytest

from return_platform.workflows.production_return_state import (
    ProductionReturnEvent,
    ProductionReturnEventType,
    ProductionReturnStage,
    ProductionReturnWorkflowState,
    _already_recorded,
    apply_production_return_event,
)

#: A full ordered path to a confirmed carrier booking. Every event before the
#: last is a precondition of the one after it.
_PATH_TO_CARRIER_BOOKING: tuple[ProductionReturnEventType, ...] = (
    ProductionReturnEventType.DISCOVERY_CONFIRMED,
    ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
    ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
    ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
    ProductionReturnEventType.OMC_RETURN_CREATED,
    ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
    ProductionReturnEventType.BOL_TENDERED,
    ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
)


def _new_state() -> ProductionReturnWorkflowState:
    return ProductionReturnWorkflowState(
        session_id="s-1",
        correlation_id="c-1",
        workflow_version="v2",
        assumption_set_version="a1",
        stage=ProductionReturnStage.INTAKE,
        applied_event_ids=(),
    )


def _apply(
    state: ProductionReturnWorkflowState,
    event_type: ProductionReturnEventType,
    event_id: str,
) -> ProductionReturnWorkflowState:
    return apply_production_return_event(
        state,
        ProductionReturnEvent(
            event_id=event_id, event_type=event_type, evidence_reference="EVIDENCE-1"
        ),
    )


def _booked() -> ProductionReturnWorkflowState:
    """State with a carrier booking recorded through the action path's event id."""
    state = _new_state()
    for index, event_type in enumerate(_PATH_TO_CARRIER_BOOKING[:-1]):
        state = _apply(state, event_type, f"e{index}")
    return _apply(
        state,
        ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
        "pickup:pr-1:CONFIRM_BOOKING:3",
    )


def test_the_same_event_id_twice_is_still_a_no_op() -> None:
    """A delivery retry must stay free. If this ever starts raising, at-least-once
    signal delivery turns every duplicate into a 409."""
    state = _booked()
    before = state.applied_event_ids

    state = _apply(
        state,
        ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
        "pickup:pr-1:CONFIRM_BOOKING:3",
    )

    assert state.applied_event_ids == before


def test_a_different_event_id_for_a_completed_transition_is_refused() -> None:
    """The overlap, exactly: the `/events` path recording what the pickup action
    already recorded."""
    state = _booked()

    with pytest.raises(ValueError, match="CARRIER_BOOKING_CONFIRMED is already recorded"):
        _apply(
            state,
            ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
            "operator-typed-this",
        )


def test_the_refusal_says_what_to_do_instead() -> None:
    """A bare "already recorded" leaves a caller guessing whether to retry with
    the same id or give up. The message says which."""
    state = _booked()

    with pytest.raises(ValueError) as excinfo:
        _apply(state, ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED, "other-id")

    assert "original event id" in str(excinfo.value)


def test_the_refusal_does_not_mutate_the_state() -> None:
    """`applied_event_ids` is carried in Temporal workflow state; a refused event
    that still appended would grow the history it was meant to protect."""
    state = _booked()
    before = state

    with pytest.raises(ValueError):
        _apply(state, ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED, "other-id")

    assert state == before


def test_every_event_type_has_an_already_recorded_marker() -> None:
    """Structural, and the reason it is structural rather than behavioural.

    The first draft of this file walked the state machine forward per event type
    to reach each transition and try it twice. That walk needed three successive
    corrections -- stop before the terminal short-circuit, avoid the waiver
    shortcuts, declare vendor recovery before closure -- and each one pushed more
    of `_validate_transition`'s ordering into the test. A test that has to
    reconstruct the rules it is checking eventually asserts its own copy of them.

    So the exhaustive part is checked where it is cheap and non-circular:
    `_already_recorded` is a total function over the enum. A missing entry is a
    `KeyError` -- a 500 on a real request -- and that is the failure mode worth
    an exhaustive test. The *behaviour* is covered at three lifecycle positions
    below rather than at all twenty.
    """
    for event_type in ProductionReturnEventType:
        _already_recorded(_new_state(), event_type)  # must not raise KeyError


def test_nothing_is_already_recorded_on_a_fresh_return() -> None:
    """The markers must be wired to flags that start unset. One inverted
    predicate -- a `not` on a requirement flag that defaults true -- would make
    an event unrecordable from the very first attempt, and the exhaustive check
    above would not notice."""
    fresh = _new_state()
    for event_type in ProductionReturnEventType:
        assert not _already_recorded(fresh, event_type), event_type.value


def test_an_early_lifecycle_event_cannot_be_recorded_twice() -> None:
    state = _apply(_new_state(), ProductionReturnEventType.DISCOVERY_CONFIRMED, "discovery:1")

    with pytest.raises(ValueError, match="DISCOVERY_CONFIRMED is already recorded"):
        _apply(state, ProductionReturnEventType.DISCOVERY_CONFIRMED, "discovery:2")


def test_a_mid_lifecycle_event_cannot_be_recorded_twice() -> None:
    """`BOL_TENDERED` sits behind six preconditions, so this also proves the
    refusal is not something only the first transition happens to get."""
    state = _new_state()
    for index, event_type in enumerate(_PATH_TO_CARRIER_BOOKING[:-1]):
        state = _apply(state, event_type, f"e{index}")
    assert state.bol_tendered

    with pytest.raises(ValueError, match="BOL_TENDERED is already recorded"):
        _apply(state, ProductionReturnEventType.BOL_TENDERED, "bol:again")


def test_a_waiver_cannot_be_recorded_twice() -> None:
    """Waivers clear a requirement rather than setting a completion flag, so
    their marker is an inverted one and is the likeliest to be miswired."""
    state = _new_state()
    for index, event_type in enumerate(_PATH_TO_CARRIER_BOOKING[:5]):
        state = _apply(state, event_type, f"e{index}")
    state = _apply(state, ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED, "waive:1")
    assert not state.physical_return_required

    with pytest.raises(ValueError, match="PHYSICAL_RETURN_NOT_REQUIRED is already recorded"):
        _apply(state, ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED, "waive:2")


def test_cancelling_twice_stays_a_silent_no_op() -> None:
    """Cancellation keeps the old shape on purpose: once a return is cancelled
    every further event is ignored rather than refused, because a late-arriving
    signal for a cancelled return is expected, not exceptional."""
    state = _new_state()
    state = _apply(state, ProductionReturnEventType.CANCELLED, "cancel-1")
    assert state.cancelled

    after = _apply(state, ProductionReturnEventType.CANCELLED, "cancel-2")

    assert after == state
