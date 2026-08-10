"""Who may record which production return event — one table, one check.

Wave D4. This is a *move plus one correction*, not a new policy.

The table lived in `api/production_workflow.py::_authorize_event` and was applied
by exactly one of the four API routers that record production workflow events.
The other three — `physical_operations`, `return_support`, `warehouse_placement`
— reach `ProductionWorkflowCoordinator.record_event` as a *side effect* of a
different action, and relied on their own route-level role dependency happening
to be a subset of what the table allows.

Three of those four subsets held. They held by coincidence: nothing connected
`require_logistics_roles` in one module to the `CARRIER_BOOKING_CONFIRMED` entry
in another, so narrowing the table would have silently left the implicit path
open. The fourth did not hold at all — see below.

The check now lives on `record_event` itself, which every path goes through, so
"authorized" is a property of recording the event rather than of remembering to
check first.

**The one behaviour change: `RETURN_SUPPORT` may tender a BOL.**
`return_support.apply_action` emits `BOL_TENDERED` when a support user records
LTL/BOL shipping instructions, but the table listed only logistics roles for it.
A support-only user was therefore refused on `POST /production-returns/{id}/events`
and allowed via `POST /return-support/work-items/{id}/actions` — the same
transition, two answers. Resolved in favour of the support path: the emission
labels itself `sourceSystem="OMC_OR_SUPPORT_READBACK"`, naming support as an
intended source, and `_validate_transition` already requires
`shipping_instructions_issued` first, so it is a follow-on to something support
legitimately did rather than an independent logistics act.

Roles are the constants from `security.roles`, not string literals as the
original table used. A typo in a literal reads as "no role may do this", which
fails closed but is invisible until someone is refused for no reason.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from return_platform.security.roles import (
    CONSOLE_ADMIN,
    LOGISTICS_COORDINATOR,
    RETURN_ASSOCIATE,
    RETURN_PLATFORM_SERVICE,
    RETURN_SUPPORT,
    WAREHOUSE_ASSOCIATE,
)
from return_platform.workflows.production_return_state import ProductionReturnEventType

__all__ = [
    "PLATFORM_SERVICE_ROLES",
    "ROLES_ALLOWED_TO_RECORD",
    "ProductionEventNotPermitted",
    "authorize_production_event",
    "unauthorized_events_for",
]

#: What the platform passes when it is acting as itself rather than on behalf of
#: the human who triggered it -- today, the dependency simulator and its workflow
#: bridge, which stand in for OMC/LSI/carrier systems responding. Those events
#: genuinely originate from the integration layer, so attributing them to the
#: operator who pressed "simulate" would be a false audit trail.
#:
#: A named constant rather than an inline literal so `grep PLATFORM_SERVICE_ROLES`
#: enumerates every place the platform bypasses a human's role, which should stay
#: a list short enough to read.
PLATFORM_SERVICE_ROLES: frozenset[str] = frozenset({RETURN_PLATFORM_SERVICE})


class ProductionEventNotPermitted(Exception):
    """The actor's roles do not permit recording this event.

    Carries the event type and the roles that would have been accepted so a
    router can render a 403 that says which act was refused, without the router
    re-deriving any of it.
    """

    def __init__(
        self, *, event_type: ProductionReturnEventType, allowed_roles: frozenset[str]
    ) -> None:
        self.event_type = event_type
        self.allowed_roles = allowed_roles
        super().__init__(
            f"role cannot record production event {event_type.value!r} "
            f"(allowed: {', '.join(sorted(allowed_roles))})"
        )


#: `RETURN_PLATFORM_SERVICE` appears in every entry on purpose: platform-internal
#: paths (the dependency simulator, the simulation workflow bridge) act as the
#: platform rather than as a human, and must be able to drive any transition a
#: human could. It is not a wildcard — it is a role, and a request carrying it is
#: a request the platform authenticated as itself.
ROLES_ALLOWED_TO_RECORD: Mapping[ProductionReturnEventType, frozenset[str]] = {
    ProductionReturnEventType.DISCOVERY_CONFIRMED: frozenset(
        {CONSOLE_ADMIN, RETURN_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.RETURN_DETAILS_CONFIRMED: frozenset(
        {CONSOLE_ADMIN, RETURN_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.SUPPORT_REQUEST_CREATED: frozenset(
        {CONSOLE_ADMIN, RETURN_ASSOCIATE, RETURN_SUPPORT, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.SUPPORT_ACKNOWLEDGED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.OMC_RETURN_CREATED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.SHIPPING_INSTRUCTIONS_ISSUED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, LOGISTICS_COORDINATOR, RETURN_PLATFORM_SERVICE}
    ),
    # RETURN_SUPPORT added in Wave D4 -- see the module docstring. Support
    # recording LTL/BOL shipping instructions tenders a BOL as a consequence, and
    # that path predates this table.
    ProductionReturnEventType.BOL_TENDERED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, LOGISTICS_COORDINATOR, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.CARRIER_BOOKING_CONFIRMED: frozenset(
        {CONSOLE_ADMIN, LOGISTICS_COORDINATOR, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.PHYSICAL_HANDOFF_CONFIRMED: frozenset(
        {CONSOLE_ADMIN, RETURN_ASSOCIATE, LOGISTICS_COORDINATOR, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.PHYSICAL_RETURN_NOT_REQUIRED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.RECEIPT_CONFIRMED: frozenset(
        {CONSOLE_ADMIN, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.LICENSE_PLATE_NOT_REQUIRED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.WAREHOUSE_PROCESSING_NOT_REQUIRED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.LICENSE_PLATE_ASSIGNED: frozenset(
        {CONSOLE_ADMIN, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.CUSTOMER_RESOLUTION_COMPLETED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.PRODUCT_DISPOSITION_COMPLETED: frozenset(
        {CONSOLE_ADMIN, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.WAREHOUSE_PROCESSING_COMPLETED: frozenset(
        {CONSOLE_ADMIN, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.VENDOR_RECOVERY_REQUIRED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.VENDOR_RECOVERY_COMPLETED: frozenset(
        {CONSOLE_ADMIN, RETURN_SUPPORT, WAREHOUSE_ASSOCIATE, RETURN_PLATFORM_SERVICE}
    ),
    ProductionReturnEventType.CANCELLED: frozenset(
        {CONSOLE_ADMIN, RETURN_ASSOCIATE, RETURN_SUPPORT, RETURN_PLATFORM_SERVICE}
    ),
}


def authorize_production_event(
    *, event_type: ProductionReturnEventType, actor_roles: frozenset[str]
) -> None:
    """Raise `ProductionEventNotPermitted` unless `actor_roles` may record this.

    An event type missing from the table raises rather than defaulting either
    way. Defaulting to allow is an obvious hole; defaulting to deny is worse than
    it sounds, because it makes a forgotten table entry look like a role problem
    to whoever is refused. `test_the_table_covers_every_event_type` means this
    branch should be unreachable in a shipped build.
    """
    allowed = ROLES_ALLOWED_TO_RECORD.get(event_type)
    if allowed is None:
        raise ProductionEventNotPermitted(event_type=event_type, allowed_roles=frozenset())
    if not actor_roles & allowed:
        raise ProductionEventNotPermitted(event_type=event_type, allowed_roles=allowed)


def unauthorized_events_for(
    *, event_types: Iterable[ProductionReturnEventType], actor_roles: frozenset[str]
) -> list[ProductionReturnEventType]:
    """Which of `event_types` this actor may *not* record, in the given order.

    For callers whose single action emits several events: authorizing the whole
    set before the first write is what stops a partially-applied action, where
    the business record is mutated and then the workflow signal is refused.
    """
    refused: list[ProductionReturnEventType] = []
    for event_type in event_types:
        try:
            authorize_production_event(event_type=event_type, actor_roles=actor_roles)
        except ProductionEventNotPermitted:
            refused.append(event_type)
    return refused
