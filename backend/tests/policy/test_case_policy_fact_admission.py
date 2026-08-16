"""What the case fact log is allowed to tell the evaluator (3A.3).

The adapter is pure, so every claim here is a list of dictionaries in and one
`PolicyEvaluationInput` out. Two properties are asserted over and over because
they are the two that fail quietly:

* **Nothing resolves to `FALSE` that was not stated false.** An empty log, a
  value nobody can parse, an unrecognised acquisition method, a superseded
  correction -- all of them are `UNKNOWN`, and the difference between `UNKNOWN`
  and `FALSE` is the difference between a return a human looks at and a return
  the platform approves on evidence nobody produced.
* **Admission is enforced, not assumed.** The provenance the log already carries
  decides whether a fact counts, and the rule is the one the policy package
  publishes rather than a second copy of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.policy.evaluation_input import EvidenceState
from return_platform.policy.vocabulary import (
    DamageCause,
    ManufacturerAcceptance,
    ReturnReason,
    TriState,
)
from return_platform.workflows.case_policy_facts import (
    TRI_STATE_FACT_NAMES,
    assemble_policy_evaluation_input,
    evidence_state_of,
    tri_state_of,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _fact(
    name: str,
    value: Any,
    *,
    acquisition: str = "STATED",
    fact_id: str | None = None,
    recorded_at: datetime | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    return {
        "factId": fact_id or f"fact-{name}",
        "caseId": "case-1",
        "factName": name,
        "value": value,
        "acquisitionMethod": acquisition,
        "sourceSystem": "CONVERSATION",
        "sourcePath": "CONVERSATION_MESSAGE",
        "supersedesFactId": supersedes,
        "observedAt": recorded_at or NOW - timedelta(hours=1),
        "recordedAt": recorded_at or NOW - timedelta(hours=1),
    }


def _assemble(documents: list[dict[str, Any]]) -> Any:
    return assemble_policy_evaluation_input(documents, request_date=NOW)


# ---------------------------------------------------------------------------
# Tri-state
# ---------------------------------------------------------------------------


def test_an_empty_log_is_unknown_everywhere_and_false_nowhere() -> None:
    """The forbidden defaults, checked as a set rather than one at a time."""
    assembled = _assemble([])

    for name in TRI_STATE_FACT_NAMES:
        assert getattr(assembled.facts, name) is TriState.UNKNOWN, name
    assert assembled.facts.damage_cause is DamageCause.UNKNOWN
    assert assembled.facts.return_reason is ReturnReason.UNKNOWN
    assert assembled.facts.manufacturer_return_acceptance is ManufacturerAcceptance.UNKNOWN
    assert assembled.facts.purchase_date is None
    assert assembled.admitted == ()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, TriState.TRUE),
        (False, TriState.FALSE),
        ("TRUE", TriState.TRUE),
        ("true", TriState.TRUE),
        ("Yes", TriState.TRUE),
        ("FALSE", TriState.FALSE),
        ("no", TriState.FALSE),
        ("UNKNOWN", TriState.UNKNOWN),
        ("maybe", TriState.UNKNOWN),
        (None, TriState.UNKNOWN),
        (1, TriState.UNKNOWN),
        (0, TriState.UNKNOWN),
        ([], TriState.UNKNOWN),
    ],
)
def test_only_a_stated_no_reads_as_false(value: Any, expected: TriState) -> None:
    """`1` and `0` are deliberately not yes and no.

    The log is heterogeneous: a `1` is as likely to be a quantity as an
    affirmation, and guessing wrong in the `TRUE` direction is how a return is
    approved on a fact nobody stated.
    """
    assert tri_state_of(value) is expected


def test_a_stated_false_is_evidence_and_reaches_the_evaluator() -> None:
    """The other half of tri-state: a real "no" must not be lost as unknown."""
    assembled = _assemble([_fact("installed", False)])

    assert assembled.facts.installed is TriState.FALSE
    assert assembled.admitted == ("installed",)


def test_an_unparseable_value_is_unknown_rather_than_false() -> None:
    assembled = _assemble([_fact("used", "probably not")])

    assert assembled.facts.used is TriState.UNKNOWN
    assert ("used", "UNPARSEABLE") in assembled.excluded


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("acquisition", "expected"),
    [
        ("STATED", EvidenceState.ACCEPTED),
        ("OBSERVED", EvidenceState.ACCEPTED),
        ("DERIVED", EvidenceState.ACCEPTED),
        ("INFERRED", EvidenceState.PENDING_VALIDATION),
        ("", EvidenceState.PENDING_VALIDATION),
        ("SOMETHING_NEW", EvidenceState.PENDING_VALIDATION),
    ],
)
def test_the_admission_rule_reads_the_acquisition_method(
    acquisition: str, expected: EvidenceState
) -> None:
    """`INFERRED` is inadmissible: the model is advisory, not evidentiary.

    An acquisition method this release has never heard of is treated the same
    way. A fact whose provenance cannot be classified is not a fact that may
    decide a return.
    """
    state = evidence_state_of(
        _fact("used", False, acquisition=acquisition), superseded_ids=frozenset()
    )

    assert state is expected


def test_an_inferred_fact_is_dropped_and_recorded() -> None:
    assembled = _assemble([_fact("installed", False, acquisition="INFERRED")])

    assert assembled.facts.installed is TriState.UNKNOWN
    assert assembled.admitted == ()
    assert assembled.excluded == (("installed", EvidenceState.PENDING_VALIDATION.value),)


def test_a_superseded_fact_is_dropped_even_when_it_is_the_latest_by_name() -> None:
    """Supersession is a relation between two documents, not a timestamp.

    A correction can be recorded before the fact it supersedes -- an out-of-order
    write, a backfill -- so "latest wins" alone would restore the withdrawn
    reading. The `supersedesFactId` link is read from the whole log for exactly
    that reason.
    """
    withdrawn = _fact("used", False, fact_id="used-old", recorded_at=NOW - timedelta(minutes=1))
    correction = _fact(
        "used",
        True,
        fact_id="used-new",
        recorded_at=NOW - timedelta(minutes=5),
        supersedes="used-old",
    )

    assembled = assemble_policy_evaluation_input([correction, withdrawn], request_date=NOW)

    assert assembled.facts.used is TriState.UNKNOWN
    assert ("used", EvidenceState.SUPERSEDED.value) in assembled.excluded


def test_the_newest_fact_for_a_name_is_the_one_that_counts() -> None:
    assembled = assemble_policy_evaluation_input(
        [
            _fact("damaged", True, fact_id="damaged-1", recorded_at=NOW - timedelta(hours=2)),
            _fact("damaged", False, fact_id="damaged-2", recorded_at=NOW - timedelta(hours=1)),
        ],
        request_date=NOW,
    )

    assert assembled.facts.damaged is TriState.FALSE


# ---------------------------------------------------------------------------
# Vocabularies, dates and everything deliberately not read
# ---------------------------------------------------------------------------


def test_a_reason_outside_the_vocabulary_is_unknown_rather_than_an_error() -> None:
    """A source system emitting a new reason produces a case a human looks at.

    Not a workflow that dies: the evaluator's fail-safe already sends an unknown
    reason to review, and raising here would turn a vocabulary drift into an
    outage.
    """
    assembled = _assemble([_fact("return_reason", "SOMETHING_THE_RELEASE_HAS_NOT_HEARD_OF")])

    assert assembled.facts.return_reason is ReturnReason.UNKNOWN


def test_a_stated_reason_reaches_the_evaluator() -> None:
    assembled = _assemble([_fact("return_reason", "shipping_damage")])

    assert assembled.facts.return_reason is ReturnReason.SHIPPING_DAMAGE


def test_dates_arrive_as_aware_instants_however_the_log_stored_them() -> None:
    """Mongo hands back naive UTC; a producer may have serialised a string."""
    assembled = assemble_policy_evaluation_input(
        [
            _fact("purchase_date", datetime(2026, 8, 1, 10, 0)),
            _fact("delivery_date", "2026-08-03T09:00:00Z"),
        ],
        request_date=NOW,
    )

    assert assembled.facts.purchase_date == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    assert assembled.facts.delivery_date == datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


def test_an_unreadable_date_is_absent_rather_than_now() -> None:
    assembled = _assemble([_fact("purchase_date", "last tuesday")])

    assert assembled.facts.purchase_date is None


def test_a_contradiction_between_stated_facts_raises() -> None:
    """A purchase after the request is not repaired here.

    Repairing it would mean choosing which of two stated facts to believe. The
    activity turns this into `REVIEW_REQUIRED`, which is a human choosing.
    """
    with pytest.raises(ValueError):
        _assemble([_fact("purchase_date", "2026-09-01T10:00:00+00:00")])


def test_no_fee_amount_is_ever_read_out_of_the_fact_log() -> None:
    """An amount must name the authority that set it, and the log has none.

    The audit's `estimatedRefund: 149.99` was a number with nothing behind it;
    reading one out of a heterogeneous fact value would be the same number in a
    new place.
    """
    assembled = _assemble(
        [
            _fact("seller_restocking_fee", "15.00"),
            _fact("manufacturer_restocking_fee", 25),
        ]
    )

    assert assembled.facts.seller_restocking_fee.amount is None
    assert assembled.facts.seller_restocking_fee.applicability is TriState.UNKNOWN
    assert assembled.facts.manufacturer_restocking_fee.amount is None


def test_an_approximate_purchase_date_is_not_a_purchase_date() -> None:
    """The one date the conversation captures today, and it is not evidence.

    A 30-day boundary decided from a date the associate described as approximate
    is a boundary nobody can defend, so it is left out and the case goes to
    review for want of a purchase date.
    """
    assembled = _assemble([_fact("approximate_purchase_date", "2026-08-01T10:00:00+00:00")])

    assert assembled.facts.purchase_date is None
    assert assembled.admitted == ()


def test_non_policy_facts_are_ignored_without_being_reported_as_excluded() -> None:
    """`excluded` is about admissibility, not about everything else on the log.

    An order reference is not a policy fact that failed admission; it is not a
    policy fact. Reporting it would bury the entries an operator needs to see.
    """
    assembled = _assemble(
        [_fact("confirmed_order_reference", "CW273354"), _fact("bay_reference", "BAY-7")]
    )

    assert assembled.excluded == ()
    assert assembled.admitted == ()
