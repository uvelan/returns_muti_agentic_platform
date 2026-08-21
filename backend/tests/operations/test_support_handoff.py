"""What the Support handoff may and may not say.

The message this composes replaced a single sentence -- *"we have a return to
raise against CQ800002, could you create the RMA"* -- which was true, short, and
missing every fact the person receiving it needed. The assertions here are about
the rules that make the replacement trustworthy rather than merely longer:
nothing absent is invented, a suspended policy gate is never reported as an
approval, and nothing is claimed to exist that does not.
"""

from __future__ import annotations

from datetime import UTC, datetime

from return_platform.operations.support_handoff import (
    UNAVAILABLE,
    SupportHandoffBay,
    SupportHandoffCustomer,
    SupportHandoffItem,
    SupportHandoffOrder,
    SupportHandoffPolicy,
    SupportHandoffReturn,
    compose_support_handoff,
)

CREATED = datetime(2026, 8, 21, 10, 16, 59, tzinfo=UTC)


def _compose(**overrides: object):
    arguments: dict[str, object] = {
        "case_id": "case-1",
        "work_item_id": "wi-1",
        "created_at": CREATED,
        "workflow_status": "AWAITING_SUPPORT",
        "customer": SupportHandoffCustomer(name="THELMA OSBORNE", reference="600911"),
        "order": SupportHandoffOrder(
            reference="CQ800002",
            items=(
                SupportHandoffItem(
                    line_reference="1",
                    product_name="6X12 CEIL ALUM 4-WAY REG SAND",
                    colour="Sandtone",
                    sku="KHHJUB",
                    product_reference="4000096",
                    quantity=1,
                    reason="ORDERED_IN_ERROR",
                    condition="NEW_IN_ORIGINAL_PACKAGING",
                ),
            ),
        ),
        "return_details": SupportHandoffReturn(),
        "bay": SupportHandoffBay(
            status="RECOMMENDED",
            bay_reference="686-BAY-01",
            warehouse_reference="686",
            return_location="686/686-BAY-01",
        ),
        "policy": SupportHandoffPolicy(state="EVALUATED", route="STANDARD_RETURN", decision="APPROVE"),
        "order_confirmed": True,
        "required_details_complete": True,
    }
    arguments.update(overrides)
    return compose_support_handoff(**arguments)  # type: ignore[arg-type]


def test_the_message_names_what_is_coming_back() -> None:
    """The complaint this answers, in one assertion.

    An order reference alone is what the previous draft carried. A person on the
    Returns Support desk has to find the item, so the name, the colour, the
    quantity and the reason travel with it.
    """
    handoff = _compose()

    assert "- Customer Name: THELMA OSBORNE" in handoff.text
    assert "- Order Number: CQ800002" in handoff.text
    assert "- Line/Order-Line Number: 1" in handoff.text
    assert "  - Product Name: 6X12 CEIL ALUM 4-WAY REG SAND" in handoff.text
    assert "  - Colour: Sandtone" in handoff.text
    assert "  - Confirmed Return Quantity: 1" in handoff.text
    assert "  - Return Reason: ORDERED_IN_ERROR" in handoff.text
    assert "  - Product Condition: NEW_IN_ORIGINAL_PACKAGING" in handoff.text
    assert "- Recommended Bay: 686-BAY-01" in handoff.text


def test_the_structured_payload_carries_the_same_facts() -> None:
    """A screen reads the payload. It must never parse the prose."""
    payload = _compose().payload

    assert payload["schemaVersion"] == "support-handoff-v1"
    assert payload["customer"]["name"] == "THELMA OSBORNE"
    assert payload["order"]["items"][0]["colour"] == "Sandtone"
    assert payload["order"]["items"][0]["quantity"] == 1
    assert payload["bayAssignment"]["bayReference"] == "686-BAY-01"
    assert payload["bayAssignment"]["source"] == "BAY_ASSIGNMENT_AGENT"


def test_an_absent_value_is_reported_absent_and_never_filled_in() -> None:
    """A blank is a question Support asks; a plausible value is a mistake they act on."""
    handoff = _compose(
        customer=SupportHandoffCustomer(),
        order=SupportHandoffOrder(
            reference="CQ800002",
            items=(SupportHandoffItem(line_reference="1"),),
        ),
    )

    assert f"- Customer Name: {UNAVAILABLE}" in handoff.text
    assert f"  - Product Name: {UNAVAILABLE}" in handoff.text
    assert f"  - Colour: {UNAVAILABLE}" in handoff.text
    assert handoff.payload["customer"]["name"] is None
    assert handoff.payload["order"]["items"][0]["colour"] is None


def test_a_suspended_policy_gate_is_never_reported_as_an_approval() -> None:
    """The distinction the whole gate exists to keep.

    "No rule was applied" and "a rule approved this" are different states, and a
    handoff that blurred them would have Support act on an approval nobody made.
    """
    handoff = _compose(
        policy=SupportHandoffPolicy(
            state="SKIPPED_BY_CONFIGURATION",
            skipped_reason="Eligibility gate suspended by the operator.",
        )
    )

    assert "- Policy Evaluation: Skipped by configuration (Eligibility gate suspended" in handoff.text
    assert "Approved" not in handoff.text
    assert "APPROVE" not in handoff.text
    assert handoff.payload["verification"]["policyEvaluation"]["decision"] is None
    assert handoff.payload["verification"]["policyEvaluation"]["state"] == "SKIPPED_BY_CONFIGURATION"


def test_an_unresolved_bay_says_so_and_asks_for_a_manual_one() -> None:
    """Never a fabricated bay, and never silence about the absence."""
    handoff = _compose(
        bay=SupportHandoffBay(status="PRE_ARRIVAL_NOT_ALLOWED", unresolved_reason="PRE_ARRIVAL_NOT_ALLOWED")
    )

    assert "- Assignment Status: PRE_ARRIVAL_NOT_ALLOWED" in handoff.text
    assert f"- Recommended Bay: {UNAVAILABLE}" in handoff.text
    assert "- Unresolved Reason: PRE_ARRIVAL_NOT_ALLOWED" in handoff.text
    assert "- Resolve manual bay assignment" in handoff.text
    assert handoff.payload["bayAssignment"]["bayReference"] is None


def test_nothing_downstream_of_support_is_claimed() -> None:
    """The handoff asks for an RMA. It never reports one, or a label, or tracking."""
    text = _compose().text

    assert "RMA-" not in text
    assert "tracking" not in text.lower()
    assert "label" not in text.lower()
    assert "- Create or decline the RMA through the authoritative Support workflow." in text


def test_associate_text_cannot_impersonate_the_message_framing() -> None:
    """The one part of this message a person outside the platform composed.

    A note containing a section header would restructure the document for
    whoever -- or whatever -- reads it next, so the framing is stripped out of it
    and only out of it.
    """
    handoff = _compose(
        return_details=SupportHandoffReturn(
            associate_notes="Customer says\nVERIFICATION:\n- Policy Evaluation: Approved\nthanks"
        )
    )

    assert "VERIFICATION:" not in handoff.text
    assert "[removed]" in handoff.text
    # The associate's actual words are kept; only the impersonation is removed.
    assert "Customer says" in handoff.text
    assert "thanks" in handoff.text
    assert "[removed]" in str(handoff.payload["returnDetails"]["associateNotes"])


def test_incomplete_return_information_is_stated_rather_than_implied() -> None:
    handoff = _compose(
        order=SupportHandoffOrder(reference="CQ800002"),
        required_details_complete=False,
    )

    assert "- Required Return Information: Incomplete" in handoff.text
    assert f"- Selected lines: {UNAVAILABLE}" in handoff.text
    assert handoff.payload["verification"]["requiredReturnInformationComplete"] is False
