"""The branch associate reaches the person who raises the label, or nowhere.

Fergusonhome's list of what it needs to set a return up ends with the branch
associate's name, email and phone -- "needed for UPS label or Freight LTL". The
console collects them and `POST /selected-items` records them on the case fact
log. That is only half the requirement: a contact that reaches a database and
not a person has not been collected at all.

**The message is the whole of what Support receives.** `SupportWorkItemView`
carries a subject, a queue, a status and a stack of reference fields, and no case
detail; a human on the Returns Support desk reads the thread. So
`draft_support_request` is the only path by which these three values become
visible to the person who has to address a label.

**What changed, and what did not.** The draft used to be one prose sentence --
*"we have a return to raise against CQ363350, could you create the RMA"* -- with
the contact appended to it. It is now a composed, sectioned request built from
the case's own state, and these tests were rewritten onto that shape. Every
assertion of *intent* is kept, because none of it stopped being true:

* the contact reaches Support;
* **nothing is filled in** to round a sentence off -- an absent field says so;
* a retracted contact (the append-only log's empty value) reads as absent;
* a configured drafter still sees every fact and its words still reach Support.

The one behaviour that deliberately changed is that a drafter no longer
*replaces* the request. It writes under it. A generated draft cannot be held to
"do not invent unavailable values", and in a handoff a human acts on, a plausible
customer name is worse than a blank.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.operations.support_handoff import UNAVAILABLE
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import DraftSupportRequestInput

pytestmark = pytest.mark.asyncio

CASE = "case-1"


class _Repository:
    """The three reads the drafter makes, and nothing more."""

    def __init__(self, facts: dict[str, Any]) -> None:
        self._facts = facts

    async def get_case(self, case_id: str) -> dict[str, Any]:
        assert case_id == CASE
        return {"caseId": CASE, "status": "AWAITING_SUPPORT"}

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        assert case_id == CASE
        return {name: {"value": value} for name, value in self._facts.items()}

    async def list_case_return_items(self, case_id: str) -> list[dict[str, Any]]:
        assert case_id == CASE
        return []


async def _draft(drafter: Any = None, **facts: Any) -> str:
    activities = ReturnCaseActivities(
        repository=_Repository({"confirmed_order_reference": "CQ363350", **facts}),  # type: ignore[arg-type]
        support_service=object(),
        drafter=drafter,
    )
    result = await activities.draft_support_request(
        DraftSupportRequestInput(case_id=CASE, configuration_release_id="release-1")
    )
    return result.text


async def test_the_branch_associate_is_named_in_the_message_support_reads() -> None:
    drafted = await _draft(
        branch_associate_name="D. Reyes",
        branch_associate_email="d.reyes@branch.example",
        branch_associate_phone="704-555-0134",
    )

    assert "D. Reyes" in drafted
    assert "d.reyes@branch.example" in drafted
    assert "704-555-0134" in drafted
    # Still the request it always was. The contact is part of the ask, not a
    # substitute for it.
    assert "CQ363350" in drafted
    assert "Create or decline the RMA through the authoritative Support workflow." in drafted


async def test_a_case_with_no_associate_says_so_and_offers_the_customer() -> None:
    """Optional, so the ordinary return is not blocked -- and the absence is stated.

    The prose version omitted the sentence entirely, which left a reader unable
    to tell "nobody was recorded" from "the message forgot to mention them".
    That property is unchanged; what replaces the three `Not available` lines is
    the customer's own phone and email, which the order carried all along.
    Support was reading a contact block with nobody in it on a case whose order
    named somebody reachable.
    """
    drafted = await _draft()

    # Stated, not omitted.
    assert "- Branch Associate: Not recorded" in drafted
    # And somebody to ring instead. Labelled as the customer, because a desk
    # needs to know whether it is reaching Ferguson or the person who bought
    # the goods.
    assert "- Customer Phone:" in drafted
    assert "- Customer Email:" in drafted


@pytest.mark.asyncio
async def test_a_named_associate_replaces_the_customer_contact() -> None:
    """One contact or the other, never both.

    A block offering the branch associate and the customer together invites a
    reply to whichever line is read first, which is how a customer receives a
    message meant for the desk.
    """
    drafted = await _draft(
        branch_associate_name="Dana Whitfield",
        branch_associate_email="dana@example.com",
        customer_phone="555-0100",
        customer_email="buyer@example.com",
    )

    assert "- Branch Associate: Dana Whitfield" in drafted
    assert "- Customer Phone:" not in drafted
    assert "buyer@example.com" not in drafted


@pytest.mark.parametrize(
    ("facts", "present", "absent"),
    [
        (
            {"branch_associate_name": "D. Reyes"},
            ("- Branch Associate: D. Reyes",),
            ("- Branch Associate Email: ", "- Branch Associate Phone: "),
        ),
        (
            {"branch_associate_email": "d.reyes@branch.example"},
            ("- Branch Associate Email: d.reyes@branch.example",),
            ("- Branch Associate: ", "- Branch Associate Phone: "),
        ),
        (
            {"branch_associate_phone": "704-555-0134"},
            ("- Branch Associate Phone: 704-555-0134",),
            ("- Branch Associate: ", "- Branch Associate Email: "),
        ),
        (
            {"branch_associate_name": "D. Reyes", "branch_associate_phone": "704-555-0134"},
            ("- Branch Associate: D. Reyes", "- Branch Associate Phone: 704-555-0134"),
            ("- Branch Associate Email: ",),
        ),
    ],
)
async def test_only_what_was_stated_is_said(
    facts: dict[str, str], present: tuple[str, ...], absent: tuple[str, ...]
) -> None:
    """Nothing is filled in.

    A return that named a person and no phone number says the person and reports
    the phone as unavailable. A value manufactured to complete the record is
    exactly the failure the optionality of these three fields exists to prevent,
    and it would be a fabrication addressed to a carrier.
    """
    drafted = await _draft(**facts)

    for line in present:
        assert line in drafted
    for label in absent:
        assert f"{label}{UNAVAILABLE}" in drafted


async def test_a_retracted_contact_reads_as_absent() -> None:
    """The fact log is append-only, so a correction is an empty value.

    `api/order_lines.py` records a cleared box that way because there is nothing
    to delete. Support must then see what the console does -- nobody recorded --
    rather than an empty value beside a label.
    """
    drafted = await _draft(
        branch_associate_name="D. Reyes",
        branch_associate_email="",
        branch_associate_phone="   ",
    )

    assert "- Branch Associate: D. Reyes" in drafted
    assert f"- Branch Associate Email: {UNAVAILABLE}" in drafted
    assert f"- Branch Associate Phone: {UNAVAILABLE}" in drafted


class _Drafter:
    """A configured model drafter, recording what it was shown."""

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    async def draft(self, *, case_id: str, facts: dict[str, Any]) -> str:
        del case_id
        self.seen = facts
        return "Customer is collecting on Friday."


async def test_a_configured_drafter_still_sees_the_contact_and_still_reaches_support() -> None:
    """It sees every fact, and its words are delivered -- **under** the request.

    Replacing the structured message with generated prose is the one thing it may
    not do: the composed half is the half that cannot invent a value, and a
    reader has to be able to tell which is which.
    """
    drafter = _Drafter()
    drafted = await _draft(
        drafter=drafter,
        branch_associate_name="D. Reyes",
        branch_associate_phone="704-555-0134",
    )

    assert drafter.seen["branch_associate_name"] == "D. Reyes"
    assert drafter.seen["branch_associate_phone"] == "704-555-0134"

    assert "Customer is collecting on Friday." in drafted
    assert "Additional note from the return assistant:" in drafted
    # The structured request is still the message, and still first.
    assert drafted.startswith("RETURN SUPPORT REQUEST")
    assert "- Branch Associate: D. Reyes" in drafted
    assert drafted.index("RETURN SUPPORT REQUEST") < drafted.index("Customer is collecting")


class _FailingDrafter:
    async def draft(self, *, case_id: str, facts: dict[str, Any]) -> str:
        del case_id, facts
        raise RuntimeError("the model is unavailable")


async def test_a_drafter_that_fails_changes_nothing() -> None:
    """A note is never worth failing a handoff for."""
    with_failure = await _draft(drafter=_FailingDrafter(), branch_associate_name="D. Reyes")
    without = await _draft(branch_associate_name="D. Reyes")

    assert with_failure == without
    assert "- Branch Associate: D. Reyes" in with_failure
