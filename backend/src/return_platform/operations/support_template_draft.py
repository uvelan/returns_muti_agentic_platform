"""The draft-time half of a support template's input, and its one vocabulary.

A `case_fact:` binding in the released template reads one of two things, and
the distinction is the whole reason this module exists:

- a **fact-log** name -- something an activity has already written to the case
  fact log, which arrives with a `factId` and traces to who recorded it;
- a **snapshot** name -- something the handoff *derives* at draft time from the
  case, which has no `factId` because no one ever recorded it as a fact.

Both arrive at the renderer through the same `facts` mapping. Neither the
renderer nor the template can tell them apart, and neither should have to. What
matters is that the second kind has a producer, and that the producer and the
template agree about the spelling.

## Why this is a module and not a docstring

It was a docstring. `production.yaml` bound five names that existed nowhere in
the tree, the seam that would fill them was described in prose, and the failure
mode was silent by construction: a phase-2 assembler spelling `selected_items`
as `selected_lines` would leave the field on its `fallback`, raise no gap, and
quietly drop the Order block's item list out of a message a person then acts
on. Nothing would have failed.

So the vocabulary is declared here, once, and `support_template_snapshot` is
the only thing that produces the snapshot half. The workflow's template-draft
activity (phase 2) calls it with exactly the arguments it already passes to
`compose_support_handoff`, merges the result over
`latest_case_facts_scoped`, and hands the union to the renderer. A rename is
then a one-line change in a place the drift test reads, and a typo is an
`ImportError` rather than a blank line in a support message.

## Why the conditional values are computed here

Four lines of today's handoff are conditional, and the §8 clause grammar
(`shipping_modes`, `return_reason_classes`, `order_sources`, item counts)
describes the *shape of a case*, not the presence of a value -- so no
`visibility_rule` can express "only when the bay was not recommended". The
conditionals are therefore resolved here into keys that are **present or
absent**, and the template binds them with no `fallback`, so an absent key
omits its line exactly the way the composed path omits it.

That is the important half of the design: **the absence is the signal.** A
`fallback` on one of these fields would print "Not available" where the truth
is "no bay could be recommended, resolve manually" -- which is the divergence
this module was written to close.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

# Borrowed rather than re-implemented, deliberately. These four are how the
# composed path spells a value, a person-typed value, one labelled line and one
# item payload; a second implementation here would be a second spelling, and
# the whole point of this module is that the two paths cannot diverge. They are
# private to `support_handoff` because nothing outside the handoff should need
# them -- and this module is the handoff, from the other side.
from return_platform.operations.support_handoff import (  # noqa: PLC2701
    UNAVAILABLE,
    SupportHandoffBay,
    SupportHandoffCustomer,
    SupportHandoffItem,
    SupportHandoffOrder,
    SupportHandoffPolicy,
    SupportHandoffReturn,
    _clean,
    _item_payload,
    _line,
    _safe,
)

__all__ = [
    "ASSOCIATE_NOTES_RENDERED",
    "AWAITING_FROM_SUPPORT",
    "BAY_ASSIGNMENT_STATUS",
    "BAY_UNRESOLVED_REASON",
    "CASE_ID",
    "CONTACT_ASSOCIATE_EMAIL",
    "CONTACT_ASSOCIATE_NAME",
    "CONTACT_ASSOCIATE_PHONE",
    "CONTACT_CUSTOMER_EMAIL",
    "CONTACT_CUSTOMER_NOTICE",
    "CONTACT_CUSTOMER_PHONE",
    "CREATED_AT",
    "CUSTOMER_CONTACT_NOTICE",
    "FACT_LOG_KEYS",
    "MANUAL_BAY_ACTION",
    "MANUAL_BAY_ACTION_LINE",
    "ORDER_CONFIRMATION",
    "POLICY_EVALUATION_RENDERED",
    "REQUIRED_RETURN_INFORMATION",
    "RETURN_DETAILS_ADDITIONAL",
    "SAMPLE_CASE",
    "SELECTED_ITEMS",
    "SNAPSHOT_KEYS",
    "SUPPORT_STATE_UNKNOWN",
    "TEMPLATE_CASE_FACT_KEYS",
    "WORKFLOW_STATUS_AT_HANDOFF",
    "WORK_ITEM_ID",
    "draft_facts",
    "fact_log_projection",
    "snapshot_as_facts",
    "support_template_snapshot",
]


# --- snapshot key names -----------------------------------------------------
#
# Every name a `case_fact:` binding may use that is *not* on the fact log.
# Adding one means adding its producer below in the same change.

#: The case's own id. Never a fact: it identifies the case rather than
#: describing it.
CASE_ID: Final[str] = "case_id"

#: The support work item this handoff belongs to, from the draft request.
WORK_ITEM_ID: Final[str] = "work_item_id"

#: When the case was last touched, as the composed path reads it.
CREATED_AT: Final[str] = "created_at"

#: The workflow status **at handoff**, not the current one -- the request is
#: composed before the case moves to `AWAITING_SUPPORT`, so a field labelled
#: "current" would always disagree with the status shown beside it.
WORKFLOW_STATUS_AT_HANDOFF: Final[str] = "workflow_status_at_handoff"

#: The selected lines, as item payloads the `item_list` formatter renders.
SELECTED_ITEMS: Final[str] = "selected_items"

#: The associate's own words, with anything that could impersonate the
#: message's framing removed. The raw `associate_notes` fact is deliberately
#: **not** bound by the template: neutralisation is a rule of the composition,
#: and binding the raw fact would quietly drop it.
ASSOCIATE_NOTES_RENDERED: Final[str] = "associate_notes_rendered"

#: Every other configured required detail the case carries, already rendered as
#: labelled lines. The labels are the release's, not the template's, which is
#: what lets a release add a field without either module being edited.
RETURN_DETAILS_ADDITIONAL: Final[str] = "return_details_additional"

#: The bay agent's answer as the handoff states it: `RECOMMENDED` when a bay
#: came back, otherwise the agent's own reason code.
BAY_ASSIGNMENT_STATUS: Final[str] = "bay_assignment_status"

#: Present **only** when no bay was recommended. Its absence is what omits the
#: line, exactly as the composed path omits it.
BAY_UNRESOLVED_REASON: Final[str] = "bay_unresolved_reason"

#: `Confirmed` / `Not confirmed`.
ORDER_CONFIRMATION: Final[str] = "order_confirmation"

#: `Complete` / `Incomplete` -- the associate's half, judged on what the
#: associate supplies, never on what Support has not done yet.
REQUIRED_RETURN_INFORMATION: Final[str] = "required_return_information"

#: Present only when something is outstanding, or when the case state could not
#: be read at all. "Nothing is outstanding" and "we could not find out" send
#: Support to opposite actions, so they are two values and neither is silence.
AWAITING_FROM_SUPPORT: Final[str] = "awaiting_from_support"

#: The eligibility gate reported in the words of what actually happened.
POLICY_EVALUATION_RENDERED: Final[str] = "policy_evaluation_rendered"

#: Present only when no bay was recommended: the extra action line Support
#: needs on exactly those cases.
MANUAL_BAY_ACTION_LINE: Final[str] = "manual_bay_action_line"

# The contact block. Two mutually exclusive arms, because a desk that rings
# "the contact" needs to know whether it is reaching the branch or the person
# who bought the goods. Exactly one arm's three keys are ever present.
CONTACT_ASSOCIATE_NAME: Final[str] = "contact_associate_name"
CONTACT_ASSOCIATE_EMAIL: Final[str] = "contact_associate_email"
CONTACT_ASSOCIATE_PHONE: Final[str] = "contact_associate_phone"
CONTACT_CUSTOMER_NOTICE: Final[str] = "contact_customer_notice"
CONTACT_CUSTOMER_PHONE: Final[str] = "contact_customer_phone"
CONTACT_CUSTOMER_EMAIL: Final[str] = "contact_customer_email"


# --- the conditional wording ------------------------------------------------
#
# Text the clause grammar cannot make conditional, so it lives beside its
# producer rather than in the template. Named constants so it has one home and
# a grep finds it when the handoff's wording is next revisited.

CUSTOMER_CONTACT_NOTICE: Final[str] = "Not recorded -- customer contact below"
SUPPORT_STATE_UNKNOWN: Final[str] = "UNKNOWN -- case state could not be read"
MANUAL_BAY_ACTION: Final[str] = "- Resolve manual bay assignment: no bay could be recommended."


#: Every snapshot name, for the drift test and for a reader who wants the list.
SNAPSHOT_KEYS: Final[frozenset[str]] = frozenset(
    {
        CASE_ID,
        WORK_ITEM_ID,
        CREATED_AT,
        WORKFLOW_STATUS_AT_HANDOFF,
        SELECTED_ITEMS,
        ASSOCIATE_NOTES_RENDERED,
        RETURN_DETAILS_ADDITIONAL,
        BAY_ASSIGNMENT_STATUS,
        BAY_UNRESOLVED_REASON,
        ORDER_CONFIRMATION,
        REQUIRED_RETURN_INFORMATION,
        AWAITING_FROM_SUPPORT,
        POLICY_EVALUATION_RENDERED,
        MANUAL_BAY_ACTION_LINE,
        CONTACT_ASSOCIATE_NAME,
        CONTACT_ASSOCIATE_EMAIL,
        CONTACT_ASSOCIATE_PHONE,
        CONTACT_CUSTOMER_NOTICE,
        CONTACT_CUSTOMER_PHONE,
        CONTACT_CUSTOMER_EMAIL,
    }
)

#: The fact-log names the shipped template binds directly. Every one is read by
#: `draft_support_request` today; the drift test proves it against that source
#: rather than trusting this comment.
FACT_LOG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "customer_name",
        "customer_id",
        "customer_account",
        "confirmed_order_reference",
        "return_method",
        "requested_resolution",
        "product_presence",
        "bay_reference",
        "bay_warehouse_reference",
        "bay_return_location",
        "bay_handling_instructions",
    }
)

#: The whole `case_fact:` vocabulary the shipped template may bind. The drift
#: test asserts this equals what `production.yaml` actually binds, in both
#: directions -- an unknown binding fails, and so does vocabulary nothing uses.
TEMPLATE_CASE_FACT_KEYS: Final[frozenset[str]] = SNAPSHOT_KEYS | FACT_LOG_KEYS


#: The case a template preview renders against.
#:
#: **It lives here, beside the producer, rather than in the preview route.** It
#: was a second hand-written copy of the whole binding vocabulary in
#: `api/template_preview.py`, with nothing keeping the two in step -- and the
#: route's docstring claimed the preview showed "what
#: `compose_support_handoff` says today", which no test checked. Expressed as
#: `compose_support_handoff`'s own arguments, both statements become
#: mechanical: the facts come from `draft_facts`, and the claim is a test that
#: renders it both ways.
#:
#: Fabricated by construction -- every value announces itself as a sample -- so
#: a preview can never read a real customer's data.
SAMPLE_CASE: Final[dict[str, Any]] = {
    "case_id": "sample-case",
    "work_item_id": "sample-work-item",
    "created_at": datetime(2026, 1, 15, 9, 30, tzinfo=UTC),
    "workflow_status": "AWAITING_SUPPORT_HANDOFF",
    "customer": SupportHandoffCustomer(
        name="Sample Customer Ltd",
        reference="SAMPLE-CUST-1",
        account="SAMPLE-ACCT-1",
        contact_name="Sample Associate",
        contact_email="associate@example.com",
        contact_phone="555-0100",
        customer_phone="555-0199",
        customer_email="buyer@example.com",
    ),
    "order": SupportHandoffOrder(
        reference="SAMPLE-ORDER-1",
        items=(
            SupportHandoffItem(
                line_reference="10",
                product_name="Sample Water Filter Housing",
                colour="Blue",
                sku="SAMPLE-SKU-1",
                quantity=2,
                reason="SHIPPING_DAMAGE",
                condition="NEW_IN_ORIGINAL_PACKAGING",
            ),
        ),
    ),
    "return_details": SupportHandoffReturn(
        method="PREPAID_PARCEL",
        requested_resolution="REFUND",
        product_presence="AT_BRANCH",
        associate_notes="Sample note from the branch associate.",
    ),
    "bay": SupportHandoffBay(
        status="RECOMMENDED",
        bay_reference="SAMPLE-BAY-1",
        warehouse_reference="SAMPLE-WH-1",
        return_location="Sample Dock",
        handling_instructions="Keep upright.",
    ),
    "policy": SupportHandoffPolicy(state="EVALUATED", route="AUTO", decision="APPROVE"),
    "order_confirmed": True,
    "required_details_complete": True,
    "outstanding_support_dimensions": (),
    "support_state_known": True,
}


def support_template_snapshot(
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
    outstanding_support_dimensions: Sequence[str] = (),
    support_state_known: bool = True,
) -> dict[str, Any]:
    """The snapshot half of a template render's input.

    **The argument list is `compose_support_handoff`'s, deliberately.** The two
    are the same handoff by two routes, and a caller that can compose one can
    produce the other without deciding anything new. Keeping the signatures
    identical is also what makes the equivalence matrix possible: one set of
    inputs, two renderings, compared.

    A key is **absent** where the composed path omits its line. Nothing here
    invents a value: where the composed path prints `Not available`, so does
    this, by putting that text in the value rather than by leaving the key out.
    """
    snapshot: dict[str, Any] = {
        CASE_ID: case_id,
        SELECTED_ITEMS: [_item_payload(item) for item in order.items],
        ORDER_CONFIRMATION: "Confirmed" if order_confirmed else "Not confirmed",
        REQUIRED_RETURN_INFORMATION: "Complete" if required_details_complete else "Incomplete",
        POLICY_EVALUATION_RENDERED: policy.rendered(),
        BAY_ASSIGNMENT_STATUS: ("RECOMMENDED" if bay.recommended else (bay.status or "UNRESOLVED")),
    }

    # Present-or-absent, with the template's `fallback` covering the absence
    # the same way `_line` covers a `None`.
    _put(snapshot, WORK_ITEM_ID, _clean(work_item_id))
    _put(snapshot, CREATED_AT, created_at)
    _put(snapshot, WORKFLOW_STATUS_AT_HANDOFF, _clean(workflow_status))
    _put(snapshot, ASSOCIATE_NOTES_RENDERED, _safe(return_details.associate_notes))

    # The contact block: whichever arm applies, all three of its lines, with
    # `Not available` spelled out where the composed path spells it. The other
    # arm's keys are absent, which is what omits its lines.
    if (
        _safe(customer.contact_name)
        or _safe(customer.contact_email)
        or _safe(customer.contact_phone)
    ):
        snapshot[CONTACT_ASSOCIATE_NAME] = _safe(customer.contact_name) or UNAVAILABLE
        snapshot[CONTACT_ASSOCIATE_EMAIL] = _safe(customer.contact_email) or UNAVAILABLE
        snapshot[CONTACT_ASSOCIATE_PHONE] = _safe(customer.contact_phone) or UNAVAILABLE
    else:
        snapshot[CONTACT_CUSTOMER_NOTICE] = CUSTOMER_CONTACT_NOTICE
        snapshot[CONTACT_CUSTOMER_PHONE] = _clean(customer.customer_phone) or UNAVAILABLE
        snapshot[CONTACT_CUSTOMER_EMAIL] = _clean(customer.customer_email) or UNAVAILABLE

    # Whatever else the release requires, already labelled. Absent when the case
    # carries none, which omits the lines rather than printing an empty block.
    if return_details.additional:
        snapshot[RETURN_DETAILS_ADDITIONAL] = "\n".join(
            _line(label, value) for label, value in sorted(return_details.additional.items())
        )

    if not bay.recommended:
        snapshot[BAY_UNRESOLVED_REASON] = _clean(bay.unresolved_reason or bay.status) or UNAVAILABLE
        snapshot[MANUAL_BAY_ACTION_LINE] = MANUAL_BAY_ACTION

    if not support_state_known:
        snapshot[AWAITING_FROM_SUPPORT] = SUPPORT_STATE_UNKNOWN
    elif outstanding_support_dimensions:
        snapshot[AWAITING_FROM_SUPPORT] = ", ".join(outstanding_support_dimensions)

    return snapshot


def _put(snapshot: dict[str, Any], key: str, value: Any) -> None:
    """Set the key only where there is something to set."""
    if value is not None:
        snapshot[key] = value


def fact_log_projection(
    *,
    customer: SupportHandoffCustomer,
    order: SupportHandoffOrder,
    return_details: SupportHandoffReturn,
    bay: SupportHandoffBay,
    **_ignored: Any,
) -> dict[str, Any]:
    """Which fact feeds which template binding, declared once.

    The eleven names in `FACT_LOG_KEYS`, taken off the same objects
    `compose_support_handoff` reads them from. This is not how production
    assembles them -- production reads the real scoped facts, which carry their
    ids and their provenance. It is the *declaration* of the correspondence, so
    the preview sample and the equivalence matrix cannot disagree with each
    other about which fact stands behind `- Customer Name:`.

    `**_ignored` so a caller can splat a whole `compose_support_handoff`
    argument set at it without filtering first.
    """
    values = {
        "customer_name": customer.name,
        "customer_id": customer.reference,
        "customer_account": customer.account,
        "confirmed_order_reference": order.reference,
        "return_method": return_details.method,
        "requested_resolution": return_details.requested_resolution,
        "product_presence": return_details.product_presence,
        "bay_reference": bay.bay_reference,
        "bay_warehouse_reference": bay.warehouse_reference,
        "bay_return_location": bay.return_location,
        "bay_handling_instructions": bay.handling_instructions,
    }
    return {name: value for name, value in values.items() if value is not None}


def draft_facts(**case: Any) -> dict[tuple[str | None, str], dict[str, Any]]:
    """Both halves of a render's input, from one `compose_support_handoff`
    argument set.

    For a **fabricated** case -- the preview's sample, a test's scenario -- so
    nothing here carries a `factId`: no fact log stands behind any of it, and
    claiming one would put a provenance chip on a value nobody recorded.
    Production merges the real scoped facts (which carry their ids) with
    `support_template_snapshot`; it does not call this.
    """
    facts = {(None, name): {"value": value} for name, value in fact_log_projection(**case).items()}
    facts.update(snapshot_as_facts(support_template_snapshot(**case)))
    return facts


def snapshot_as_facts(
    snapshot: Mapping[str, Any],
) -> dict[tuple[str | None, str], dict[str, Any]]:
    """The snapshot in the shape the renderer reads facts in.

    Case-level partition, and **no `factId`** -- that absence is the provenance:
    a rendered field with no fact id is one the draft derived rather than one
    the fact log recorded, and the panel can say so.
    """
    return {(None, name): {"value": value} for name, value in snapshot.items()}
