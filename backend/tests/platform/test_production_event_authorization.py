"""One authorization table for production return events, enforced everywhere.

Wave D4's write consolidation. Before this, the event-to-roles table lived in
`api/production_workflow.py` and was applied by exactly one of the four API
routers that record production workflow events. The other three reach
`ProductionWorkflowCoordinator.record_event` as a side effect of a different
action and never consulted it.

Three of those three subsets happened to hold. The fourth did not: a
`return_support` user was refused `BOL_TENDERED` on
`POST /production-returns/{id}/events` and allowed the identical transition via
`POST /return-support/work-items/{id}/actions`.

The tests below are the invariants that make the coincidence into a rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from return_platform.operations.production_event_authorization import (
    PLATFORM_SERVICE_ROLES,
    ROLES_ALLOWED_TO_RECORD,
    ProductionEventNotPermitted,
    authorize_production_event,
    unauthorized_events_for,
)
from return_platform.operations.return_support.service import (
    SupportAction,
    SupportActionRequest,
    production_events_for_support_action,
)
from return_platform.security.roles import (
    ASSOCIATE_ROLES,
    LOGISTICS_ROLES,
    RETURN_SUPPORT,
    SUPPORT_ROLES,
    WAREHOUSE_ROLES,
)
from return_platform.workflows.production_return_workflow import ProductionReturnEventType

_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"


def _support_action(action: SupportAction, **overrides: object) -> SupportActionRequest:
    return SupportActionRequest(
        action=action,
        expectedVersion=1,
        reason="because the test says so",
        **overrides,  # type: ignore[arg-type]
    )


def test_the_table_covers_every_event_type() -> None:
    """A missing entry used to be a `KeyError` -- a 500, not a 403.

    It fails closed in effect, but it reports a forgotten table entry as a
    server fault, and whoever is refused has no way to tell the difference from
    a role problem. Adding a `ProductionReturnEventType` should fail here, at
    build time, and make someone decide who may record it.
    """
    missing = set(ProductionReturnEventType) - set(ROLES_ALLOWED_TO_RECORD)
    assert missing == set(), f"no role policy declared for: {sorted(e.value for e in missing)}"


def test_every_entry_admits_the_platform_service_role() -> None:
    """Platform-internal paths must be able to drive any transition a human can.

    If an entry omitted it, the dependency simulator would fail on exactly one
    event type with a role error, which reads as a permissions bug rather than
    the table omission it is.
    """
    for event_type, allowed in ROLES_ALLOWED_TO_RECORD.items():
        assert PLATFORM_SERVICE_ROLES & allowed, f"{event_type.value} excludes the platform service"


def test_an_unknown_role_records_nothing() -> None:
    for event_type in ProductionReturnEventType:
        with pytest.raises(ProductionEventNotPermitted):
            authorize_production_event(
                event_type=event_type, actor_roles=frozenset({"not-a-real-role"})
            )


def test_the_refusal_names_the_event_and_the_roles_that_would_work() -> None:
    """A 403 that says only "insufficient permissions" sends the reader to the
    source. This one carries what was refused and what would have worked."""
    with pytest.raises(ProductionEventNotPermitted) as excinfo:
        authorize_production_event(
            event_type=ProductionReturnEventType.RECEIPT_CONFIRMED,
            actor_roles=frozenset({"console_viewer"}),
        )
    assert "RECEIPT_CONFIRMED" in str(excinfo.value)
    assert "warehouse_associate" in str(excinfo.value)
    assert excinfo.value.event_type is ProductionReturnEventType.RECEIPT_CONFIRMED


# ---------------------------------------------------------------------------
# The invariant that was previously a coincidence
# ---------------------------------------------------------------------------

#: Each implicit path: the role set its route dependency admits, and every event
#: recording that route can cause. Hand-maintained on purpose -- the point is to
#: state what each route may do, and fail if the table stops agreeing.
_IMPLICIT_EVENT_PATHS: dict[str, tuple[frozenset[str], tuple[ProductionReturnEventType, ...]]] = {
    "physical_operations.apply_pickup_action": (
        LOGISTICS_ROLES,
        (
            ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED,
            ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED,
        ),
    ),
    "warehouse_placement.assign_bay": (
        WAREHOUSE_ROLES,
        (ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED,),
    ),
    "associate_returns.submit_return_details": (
        ASSOCIATE_ROLES,
        (
            ProductionReturnEventType.DISCOVERY_CONFIRMED,
            ProductionReturnEventType.RETURN_DETAILS_CONFIRMED,
            ProductionReturnEventType.SUPPORT_REQUEST_CREATED,
        ),
    ),
    "return_support.apply_action": (
        SUPPORT_ROLES,
        (
            ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
            ProductionReturnEventType.OMC_RETURN_CREATED,
            ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
            ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED,
            ProductionReturnEventType.CANCELLED,
            ProductionReturnEventType.BOL_TENDERED,
        ),
    ),
}


@pytest.mark.parametrize("path_name", sorted(_IMPLICIT_EVENT_PATHS))
def test_every_role_a_route_admits_may_record_every_event_that_route_causes(
    path_name: str,
) -> None:
    """The whole point of the consolidation, as an assertion.

    A route whose role dependency is *wider* than the table is a way to reach a
    transition the explicit endpoint would refuse. This failed for
    `return_support.apply_action` + `BOL_TENDERED` before Wave D4.
    """
    admitted_roles, events = _IMPLICIT_EVENT_PATHS[path_name]
    for role in sorted(admitted_roles):
        for event_type in events:
            authorize_production_event(event_type=event_type, actor_roles=frozenset({role}))


def test_return_support_may_tender_a_bol() -> None:
    """The regression, stated as its own case rather than only inside the sweep.

    `return_support` was absent from `BOL_TENDERED` while
    `RECORD_SHIPPING_INSTRUCTIONS` with an LTL type emitted exactly that event
    from a support-gated route.
    """
    assert RETURN_SUPPORT in ROLES_ALLOWED_TO_RECORD[ProductionReturnEventType.BOL_TENDERED]


# ---------------------------------------------------------------------------
# The support action's emission set
# ---------------------------------------------------------------------------


def test_recording_ltl_shipping_instructions_emits_two_events() -> None:
    planned = production_events_for_support_action(
        _support_action(SupportAction.RECORD_SHIPPING_INSTRUCTIONS, shippingInstructionType="LTL")
    )
    assert planned == (
        ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,
        ProductionReturnEventType.BOL_TENDERED,
    )


def test_recording_parcel_shipping_instructions_emits_one() -> None:
    planned = production_events_for_support_action(
        _support_action(
            SupportAction.RECORD_SHIPPING_INSTRUCTIONS, shippingInstructionType="PARCEL"
        )
    )
    assert planned == (ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED,)


def test_instruction_type_matching_is_case_insensitive() -> None:
    """The router lower-cased nothing and upper-cased once; a mixed-case value
    from a client must not decide whether a BOL is tendered."""
    planned = production_events_for_support_action(
        _support_action(
            SupportAction.RECORD_SHIPPING_INSTRUCTIONS, shippingInstructionType="Branch_Ltl"
        )
    )
    assert ProductionReturnEventType.BOL_TENDERED in planned


def test_a_bookkeeping_action_records_no_workflow_event() -> None:
    assert production_events_for_support_action(_support_action(SupportAction.ASSIGN)) == ()


def test_unauthorized_events_for_reports_only_the_refused_ones() -> None:
    """Callers whose one action emits several events need the full refused set,
    not the first failure: a 403 naming one of two refusals invites a retry that
    fails again on the other."""
    refused = unauthorized_events_for(
        event_types=(
            ProductionReturnEventType.SUPPORT_ACKNOWLEDGED,
            ProductionReturnEventType.RECEIPT_CONFIRMED,
            ProductionReturnEventType.LICENSE_PLATE_ASSIGNED,
        ),
        actor_roles=frozenset({RETURN_SUPPORT}),
    )
    assert refused == [
        ProductionReturnEventType.RECEIPT_CONFIRMED,
        ProductionReturnEventType.LICENSE_PLATE_ASSIGNED,
    ]


# ---------------------------------------------------------------------------
# Structural: the check cannot be skipped
# ---------------------------------------------------------------------------


def test_no_caller_omits_actor_roles_when_recording_an_event() -> None:
    """`actor_roles` is a required keyword, so omitting it is a TypeError rather
    than a silent bypass -- but a *default* could be reintroduced without any
    test noticing. This asserts every call site passes it explicitly, which is
    what makes the required keyword meaningful rather than incidental.
    """
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "record_event"):
                continue
            if not any(keyword.arg == "actor_roles" for keyword in node.keywords):
                offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert offenders == [], f"record_event called without actor_roles: {offenders}"


def test_the_role_table_lives_in_one_module() -> None:
    """The original failure mode was a second copy of the policy. An API module
    building its own event-to-roles mapping is that failure returning."""
    authorization_module = _SRC / "operations" / "production_event_authorization.py"
    offenders: list[str] = []
    for path in sorted((_SRC / "api").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys_are_events = [
                key
                for key in node.keys
                if isinstance(key, ast.Attribute)
                and isinstance(key.value, ast.Name)
                and key.value.id == "ProductionReturnEventType"
            ]
            values_are_role_sets = any(
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "frozenset"
                for value in node.values
            )
            if keys_are_events and values_are_role_sets:
                offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert offenders == [], (
        f"a second event-to-roles table appeared outside {authorization_module.name}: {offenders}"
    )
