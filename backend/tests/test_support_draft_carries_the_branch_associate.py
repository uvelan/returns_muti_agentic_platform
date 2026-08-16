"""The branch associate reaches the person who raises the label, or nowhere.

Fergusonhome's list of what it needs to set a return up ends with the branch
associate's name, email and phone -- "needed for UPS label or Freight LTL". The
console now collects them and `POST /selected-items` records them on the case
fact log. That is only half the requirement: a contact that reaches a database
and not a person has not been collected at all.

**The message text is the whole of what Support receives.** `SupportWorkItemView`
carries a subject, a queue, a status and a stack of reference fields, and no
case detail; the opening message's `businessPayload` is `{"caseId": ...}`. A
human on the Returns Support desk reads the thread. So the deterministic
template in `draft_support_request` is the only path by which these three values
become visible to the person who has to address a label -- and that template is
what runs in production today, because `run_return_workflow_worker.py` wires
`ReturnCaseActivities` with no `drafter`.

The model drafter, when one is configured, already receives every fact through
`facts=plain` and can phrase them itself. It is the fallback that had to be
told, and these tests pin it.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import DraftSupportRequestInput

pytestmark = pytest.mark.asyncio

CASE = "case-1"


class _Repository:
    """One method: the latest-per-name fact projection the drafter reads."""

    def __init__(self, facts: dict[str, Any]) -> None:
        self._facts = facts

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        assert case_id == CASE
        return {name: {"value": value} for name, value in self._facts.items()}


async def _draft(**facts: Any) -> str:
    activities = ReturnCaseActivities(
        repository=_Repository({"confirmed_order_reference": "CQ363350", **facts}),  # type: ignore[arg-type]
        support_service=object(),
    )
    return await activities.draft_support_request(
        DraftSupportRequestInput(case_id=CASE, configuration_release_id="release-1")
    )


async def test_the_branch_associate_is_named_in_the_message_support_reads() -> None:
    drafted = await _draft(
        branch_associate_name="D. Reyes",
        branch_associate_email="d.reyes@branch.example",
        branch_associate_phone="704-555-0134",
    )

    assert "D. Reyes" in drafted
    assert "d.reyes@branch.example" in drafted
    assert "704-555-0134" in drafted
    # Still the request it always was. The contact is added to the ask, not
    # substituted for it.
    assert "CQ363350" in drafted
    assert "return label or pickup" in drafted


async def test_a_case_with_no_associate_reads_exactly_as_it_did() -> None:
    """Optional, so the ordinary return is unchanged -- and says nothing extra."""
    drafted = await _draft()

    assert "branch associate" not in drafted.lower()
    assert drafted == (
        "Hello -- we have a return to raise against CQ363350. "
        "Could you create the RMA and send the return label or pickup "
        "instructions when you have a moment? Happy to supply anything else "
        "you need. Thank you."
    )


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            {"branch_associate_name": "D. Reyes"},
            "The branch associate for this return is D. Reyes. ",
        ),
        (
            {"branch_associate_email": "d.reyes@branch.example"},
            "The branch associate for this return can be reached at d.reyes@branch.example. ",
        ),
        (
            {"branch_associate_phone": "704-555-0134"},
            "The branch associate for this return can be reached at 704-555-0134. ",
        ),
        (
            {"branch_associate_name": "D. Reyes", "branch_associate_phone": "704-555-0134"},
            "The branch associate for this return is D. Reyes (704-555-0134). ",
        ),
    ],
)
async def test_only_what_was_stated_is_said(facts: dict[str, str], expected: str) -> None:
    """Nothing is filled in to complete the sentence.

    A return that named a person and no phone number says the person and stops.
    An address manufactured to round the sentence off is exactly the failure the
    optionality of these three fields exists to prevent, and it would be a
    fabrication addressed to a carrier.
    """
    assert expected in await _draft(**facts)


async def test_a_retracted_contact_reads_as_absent() -> None:
    """The fact log is append-only, so a correction is an empty value.

    `api/order_lines.py` records a cleared box that way because there is nothing
    to delete. Support must then see the same thing the console does -- nobody
    recorded -- rather than an empty pair of brackets after a name.
    """
    drafted = await _draft(
        branch_associate_name="D. Reyes",
        branch_associate_email="",
        branch_associate_phone="   ",
    )

    assert "The branch associate for this return is D. Reyes. " in drafted
    assert "(" not in drafted


async def test_a_configured_drafter_still_wins_and_still_sees_the_contact() -> None:
    """The template is the fallback, not a wrapper around the model's answer."""
    seen: dict[str, Any] = {}

    class _Drafter:
        async def draft(self, *, case_id: str, facts: dict[str, Any]) -> str:
            seen.update(facts)
            return "A drafted request."

    activities = ReturnCaseActivities(
        repository=_Repository(  # type: ignore[arg-type]
            {
                "confirmed_order_reference": "CQ363350",
                "branch_associate_email": "d.reyes@branch.example",
            }
        ),
        support_service=object(),
        drafter=_Drafter(),
    )

    drafted = await activities.draft_support_request(
        DraftSupportRequestInput(case_id=CASE, configuration_release_id="release-1")
    )

    assert drafted == "A drafted request."
    # It was handed the contact and chose its own words. Nothing is appended to
    # a model's draft: a template sentence bolted onto it would be two voices in
    # one message, and the model was given the fact to use.
    assert seen["branch_associate_email"] == "d.reyes@branch.example"
