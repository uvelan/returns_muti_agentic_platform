"""The message Returns Support actually reads, and the data behind it.

## What this replaces

```text
Hello -- we have a return to raise against CQ800002. Could you create the RMA
and send the return label or pickup instructions when you have a moment?
```

That was the whole handoff. One order reference and a request. Not the customer,
not the product, not how many, not why, not what condition it is in, not where
the platform recommends staging it. A human on the Returns Support desk received
a task and none of the facts needed to do it, and had to go and ask.

## Two outputs, and the split is the point

`text` is what a person reads. `payload` is the same facts, structured, and it
is what a screen reads. Nothing may parse `text` back into fields: a UI that
recovered the return quantity by splitting a formatted string is a UI that
breaks when the wording changes, and it is how a display quietly disagrees with
the database.

## Rules the composition holds to

**Nothing absent is invented.** Every field a case does not carry renders as
`Not available` in the text and as `null` in the payload. There is no default
quantity, no assumed condition and no placeholder customer. A blank in a Support
message is a question Support asks; a fabricated value is a mistake Support
acts on.

**Policy is reported as it happened.** When the deployment has suspended the
gate, the verification block says the evaluation was skipped by configuration
and quotes the operator's reason. It never says approved. "No rule was applied"
and "a rule approved this" are different states and the difference is the whole
point of having a gate.

**Nothing is claimed that has not happened.** No RMA number, no label, no
tracking, no carrier booking. The handoff *asks* for those; asserting them would
put a promise in front of a human who then has to work out whether it is true.

**Associate-typed text is neutralised.** Notes and contact details are the one
part of this message a person outside the platform composed, and the message has
a block structure a reader and a downstream transport both rely on. Anything in
that text that could impersonate the framing is stripped, the same way
`prompt_context.neutralize_delimiters` strips it out of an analyzer prompt.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "UNAVAILABLE",
    "SupportHandoff",
    "SupportHandoffBay",
    "SupportHandoffCustomer",
    "SupportHandoffItem",
    "SupportHandoffOrder",
    "SupportHandoffPolicy",
    "SupportHandoffReturn",
    "compose_support_handoff",
]

#: How an absent value reads. One spelling, everywhere, so a reader learns it
#: once -- and deliberately not an empty string, which is indistinguishable from
#: a rendering bug.
UNAVAILABLE = "Not available"

#: Anything that could impersonate this message's own framing. A section header
#: is a line of capitals ending in a colon; a separator is a run of dashes. A
#: note containing either would restructure the message for whoever reads it
#: next.
_FRAMING = re.compile(r"^\s*(?:[A-Z][A-Z /_-]{2,}:|-{3,}|={3,})\s*$", re.MULTILINE)
_NEUTRALIZED = "[removed]"


def _clean(value: Any) -> str | None:
    """One field, as text, or `None`. Never a string that says "None"."""
    if value is None or isinstance(value, bool):
        return None if value is None else str(value)
    text = str(value).strip()
    return text or None


def _safe(value: Any) -> str | None:
    """A field a person typed, with any impersonation of the framing removed."""
    text = _clean(value)
    if text is None:
        return None
    return _FRAMING.sub(_NEUTRALIZED, text)


def _line(label: str, value: Any, *, indent: int = 0) -> str:
    text = _clean(value)
    return f"{'  ' * indent}- {label}: {text if text is not None else UNAVAILABLE}"


@dataclass(frozen=True, slots=True)
class SupportHandoffCustomer:
    name: str | None = None
    reference: str | None = None
    #: The branch associate a carrier can reach about this return. Every one of
    #: these is optional by operator instruction and none may acquire a default:
    #: an invented email routes a label to nobody.
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


@dataclass(frozen=True, slots=True)
class SupportHandoffItem:
    """One line the associate selected, with what is coming back off it."""

    line_reference: str
    product_name: str | None = None
    colour: str | None = None
    sku: str | None = None
    product_reference: str | None = None
    quantity: int | None = None
    reason: str | None = None
    condition: str | None = None


@dataclass(frozen=True, slots=True)
class SupportHandoffOrder:
    reference: str | None = None
    items: tuple[SupportHandoffItem, ...] = ()


@dataclass(frozen=True, slots=True)
class SupportHandoffReturn:
    """Case-level return detail, as distinct from per-line detail."""

    method: str | None = None
    requested_resolution: str | None = None
    product_presence: str | None = None
    associate_notes: str | None = None
    #: Every other configured required detail the case carries, label -> value,
    #: so a release that adds a field does not need this module edited.
    additional: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SupportHandoffBay:
    """The Bay Assignment Agent's answer, whatever it was.

    `status` is the agent's own reason code. `RECOMMENDED` carries a bay;
    anything else is an unresolved result and says which kind, which is why the
    reason is carried separately rather than folded into an absent bay.
    """

    status: str | None = None
    bay_reference: str | None = None
    warehouse_reference: str | None = None
    return_location: str | None = None
    handling_instructions: str | None = None
    unresolved_reason: str | None = None

    @property
    def recommended(self) -> bool:
        return self.bay_reference is not None


@dataclass(frozen=True, slots=True)
class SupportHandoffPolicy:
    """What the eligibility gate did, in the words of what actually happened."""

    state: str | None = None
    #: Set only when the deployment suspended the gate. Quoted verbatim so the
    #: operator's stated reason reaches the person reading the handoff.
    skipped_reason: str | None = None
    route: str | None = None
    decision: str | None = None

    def rendered(self) -> str:
        if self.state == "SKIPPED_BY_CONFIGURATION":
            reason = _clean(self.skipped_reason)
            suffix = f" ({reason})" if reason else ""
            return f"Skipped by configuration{suffix}"
        if self.decision is not None:
            route = _clean(self.route)
            return f"{self.decision}{f' on the {route} route' if route else ''}"
        if self.state is not None:
            return str(self.state)
        return "Not evaluated"


@dataclass(frozen=True, slots=True)
class SupportHandoff:
    text: str
    payload: dict[str, Any]


def _item_payload(item: SupportHandoffItem) -> dict[str, Any]:
    return {
        "lineReference": item.line_reference,
        "productName": _clean(item.product_name),
        "colour": _clean(item.colour),
        "sku": _clean(item.sku),
        "productReference": _clean(item.product_reference),
        "quantity": item.quantity,
        "reason": _clean(item.reason),
        "condition": _clean(item.condition),
    }


def _item_lines(items: Sequence[SupportHandoffItem]) -> list[str]:
    if not items:
        return [f"- Selected lines: {UNAVAILABLE}"]
    lines: list[str] = []
    for item in items:
        lines.append(f"- Line/Order-Line Number: {item.line_reference}")
        lines.append(_line("Product Name", item.product_name, indent=1))
        lines.append(_line("Colour", item.colour, indent=1))
        lines.append(_line("SKU", item.sku, indent=1))
        lines.append(_line("Confirmed Return Quantity", item.quantity, indent=1))
        lines.append(_line("Return Reason", item.reason, indent=1))
        lines.append(_line("Product Condition", item.condition, indent=1))
    return lines


def compose_support_handoff(
    *,
    case_id: str,
    work_item_id: str | None,
    created_at: datetime | None,
    workflow_status: str | None,
    customer: SupportHandoffCustomer,
    order: SupportHandoffOrder,
    return_details: SupportHandoffReturn,
    bay: SupportHandoffBay,
    policy: SupportHandoffPolicy,
    order_confirmed: bool,
    required_details_complete: bool,
) -> SupportHandoff:
    """The handoff, as text a person reads and as data a screen reads.

    Pure: every value is passed in, so the composition is testable without a
    platform and cannot quietly acquire a second source for a field.
    """
    notes = _safe(return_details.associate_notes)

    sections: list[str] = ["RETURN SUPPORT REQUEST", ""]

    sections.append("Case:")
    sections.append(_line("Case ID", case_id))
    sections.append(_line("Work Item ID", work_item_id))
    sections.append(
        _line("Created Date/Time", created_at.isoformat() if created_at is not None else None)
    )
    sections.append(_line("Current Workflow Status", workflow_status))
    sections.append("")

    sections.append("Customer:")
    sections.append(_line("Customer Name", customer.name))
    sections.append(_line("Customer Reference", customer.reference))
    sections.append(_line("Branch Associate", _safe(customer.contact_name)))
    sections.append(_line("Branch Associate Email", _safe(customer.contact_email)))
    sections.append(_line("Branch Associate Phone", _safe(customer.contact_phone)))
    sections.append("")

    sections.append("Order:")
    sections.append(_line("Order Number", order.reference))
    sections.extend(_item_lines(order.items))
    sections.append("")

    sections.append("Return Details:")
    sections.append(_line("Return Method", return_details.method))
    sections.append(_line("Requested Resolution", return_details.requested_resolution))
    sections.append(_line("Product Presence", return_details.product_presence))
    for label, value in sorted(return_details.additional.items()):
        sections.append(_line(label, value))
    sections.append(_line("Associate Notes", notes))
    sections.append("")

    sections.append("Bay Assignment:")
    sections.append(
        _line("Assignment Status", "RECOMMENDED" if bay.recommended else (bay.status or "UNRESOLVED"))
    )
    sections.append(_line("Recommended Bay", bay.bay_reference))
    sections.append(_line("Warehouse/Branch", bay.warehouse_reference))
    sections.append(_line("Return Location", bay.return_location))
    sections.append(_line("Handling/Staging Instructions", bay.handling_instructions))
    if not bay.recommended:
        sections.append(_line("Unresolved Reason", bay.unresolved_reason or bay.status))
    sections.append("")

    sections.append("Verification:")
    sections.append(_line("Order Confirmation", "Confirmed" if order_confirmed else "Not confirmed"))
    sections.append(
        _line(
            "Required Return Information",
            "Complete" if required_details_complete else "Incomplete",
        )
    )
    sections.append(_line("Policy Evaluation", policy.rendered()))
    sections.append(_line("Bay Assignment Source", "Bay Assignment Agent"))
    sections.append("")

    sections.append("Requested Support Action:")
    sections.append("- Review the complete return request.")
    sections.append("- Review or confirm the bay recommendation.")
    if not bay.recommended:
        sections.append("- Resolve manual bay assignment: no bay could be recommended.")
    sections.append("- Verify all Support-owned conditions, including warranty when applicable.")
    sections.append("- Create or decline the RMA through the authoritative Support workflow.")

    payload: dict[str, Any] = {
        "schemaVersion": "support-handoff-v1",
        "caseId": case_id,
        "workItemId": work_item_id,
        "createdAt": created_at.isoformat() if created_at is not None else None,
        "workflowStatus": _clean(workflow_status),
        "customer": {
            "name": _clean(customer.name),
            "reference": _clean(customer.reference),
            "contactName": _safe(customer.contact_name),
            "contactEmail": _safe(customer.contact_email),
            "contactPhone": _safe(customer.contact_phone),
        },
        "order": {
            "reference": _clean(order.reference),
            "items": [_item_payload(item) for item in order.items],
        },
        "returnDetails": {
            "method": _clean(return_details.method),
            "requestedResolution": _clean(return_details.requested_resolution),
            "productPresence": _clean(return_details.product_presence),
            "associateNotes": notes,
            "additional": {key: _clean(value) for key, value in return_details.additional.items()},
        },
        "bayAssignment": {
            "status": "RECOMMENDED" if bay.recommended else _clean(bay.status),
            "bayReference": _clean(bay.bay_reference),
            "warehouseReference": _clean(bay.warehouse_reference),
            "returnLocation": _clean(bay.return_location),
            "handlingInstructions": _clean(bay.handling_instructions),
            "unresolvedReason": None if bay.recommended else _clean(bay.unresolved_reason or bay.status),
            "source": "BAY_ASSIGNMENT_AGENT",
        },
        "verification": {
            "orderConfirmed": order_confirmed,
            "requiredReturnInformationComplete": required_details_complete,
            "policyEvaluation": {
                "state": _clean(policy.state),
                "skippedReason": _clean(policy.skipped_reason),
                "route": _clean(policy.route),
                "decision": _clean(policy.decision),
            },
        },
    }
    return SupportHandoff(text="\n".join(sections).rstrip() + "\n", payload=payload)
