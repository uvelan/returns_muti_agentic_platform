"""The return window as the one determinative question (operator, 2026-08-16).

The operator's narrowing, in their own words: "only evaluate basic policy -- is
the item in return window / policy should be in graph and configurable / if the
update policy that should evaluate based on rules / no hardcoded / dont over
engineer".

So there is no rules engine here and no new rule. There is one released setting,
`standard_stock_return.unstated_condition_facts`, which decides what a fact
nobody stated is allowed to mean:

```text
REVIEW_REQUIRED  silence queues the return          (the model default, unchanged)
NOT_EVALUATED    silence decides nothing; the window decides
```

Every test below is written so that its opposite is also tested: the narrowing
is reversible by configuration alone, a *stated* failure still rejects under it,
an undated order still reviews under it, and an approval taken without condition
evidence is never recorded as one taken with it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import ValidationError

from return_platform.policy import (
    EligibilityDecision,
    PolicyClock,
    PolicyEvaluationInput,
    PolicyOutcome,
    PolicyProvenance,
    PolicyReasonCode,
    PolicyRoute,
    PolicyRule,
    ReturnEligibilityPolicy,
    TriState,
    UnstatedConditionFacts,
    evaluate_return_eligibility,
)

NEW_YORK = ZoneInfo("America/New_York")

#: The release under test, as an operator writes it. Parsed from YAML rather
#: than built in Python for the reason the evaluator suite gives: a release that
#: would not load must fail here and not in a container.
POLICY_YAML = """
id: ferguson-standard-return-policy
version: "2026-08-16"
authority: FERGUSON_PUBLIC_TERMS
source_document: Ferguson Terms and Conditions of Sale
source_revision: "Terms and Conditions of Sale - Rev. May 2025"
precedence:
  - CUSTOMER_CONTRACT_OVERRIDE
  - SPECIAL_ORDER_MANUFACTURER_POLICY
  - FERGUSON_STANDARD_RETURN
standard_stock_return:
  purchase_window: { days: 30, basis: PURCHASE_DATE }
stock_classification:
  unresolved_default: STANDARD_STOCK
outside_standard_window:
  decision: REVIEW_REQUIRED
  reason_code: OUTSIDE_STANDARD_RETURN_WINDOW
delivery_claim:
  conditions: [SHIPPING_DAMAGE, SHORTAGE, SHIPMENT_ERROR, IMPROPER_DELIVERY]
warranty_issue:
  reasons:
    - MANUFACTURING_DEFECT
    - PRODUCT_FAILURE_AFTER_INSTALLATION
    - COVERED_PRIVATE_LABEL_DEFECT
    - MANUFACTURER_WARRANTY_ISSUE
"""

#: Every check the standard path applies, in the order the evaluator names them.
ALL_CHECKS = (
    "new",
    "suitable_for_resale",
    "original_packaging",
    "packaging_undamaged",
    "all_original_parts",
    "used",
    "installed",
    "modified",
    "rebuilt",
    "reconditioned",
    "repaired",
    "altered",
    "damaged",
)

#: Every condition and prohibited-state fact stated, and all of them passing.
FULLY_EVIDENCED: dict[str, TriState] = {
    "condition_new": TriState.TRUE,
    "suitable_for_resale": TriState.TRUE,
    "original_packaging": TriState.TRUE,
    "packaging_undamaged": TriState.TRUE,
    "all_original_parts": TriState.TRUE,
    "used": TriState.FALSE,
    "installed": TriState.FALSE,
    "modified": TriState.FALSE,
    "rebuilt": TriState.FALSE,
    "reconditioned": TriState.FALSE,
    "repaired": TriState.FALSE,
    "altered": TriState.FALSE,
    "damaged": TriState.FALSE,
}


def _policy(unstated: UnstatedConditionFacts | None = None) -> ReturnEligibilityPolicy:
    """The release, optionally with the narrowing switched on.

    `None` leaves the key out of the YAML entirely, which is what a release cut
    before the setting existed looks like.
    """
    parsed: Any = yaml.safe_load(POLICY_YAML)
    if unstated is not None:
        parsed["standard_stock_return"]["unstated_condition_facts"] = unstated.value
    return ReturnEligibilityPolicy.model_validate(parsed)


def _local(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=NEW_YORK)


def _facts(**overrides: Any) -> PolicyEvaluationInput:
    values: dict[str, Any] = {
        "request_date": _local(2025, 11, 1),
        "purchase_date": _local(2025, 10, 14),
    }
    values.update(overrides)
    return PolicyEvaluationInput(**values)


def _evaluate(policy: ReturnEligibilityPolicy, facts: PolicyEvaluationInput) -> PolicyOutcome:
    return evaluate_return_eligibility(
        policy,
        facts,
        PolicyClock(evaluated_at=facts.request_date, local_zone=NEW_YORK),
    )


# ---------------------------------------------------------------------------
# The default is unchanged
# ---------------------------------------------------------------------------


def test_a_release_that_does_not_mention_the_setting_still_queues_unstated_facts() -> None:
    """The narrowing is opt-in. A release cut before it existed behaves as it did.

    This is the whole safety argument for adding a setting rather than editing
    the rule: every deployment that has not asked for the narrowing keeps the
    baseline's fail-safe, and gets it from the model default rather than from
    remembering to write a key.
    """
    outcome = _evaluate(_policy(), _facts())

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.REQUIRED_FACT_UNKNOWN,)
    assert PolicyRule.FAIL_SAFE_REVIEW in outcome.applied_rules
    assert PolicyRule.CONDITION_FACTS_NOT_EVALUATED not in outcome.applied_rules
    assert outcome.unevaluated_checks == ()


def test_the_default_is_the_baseline_fail_safe() -> None:
    assert (
        _policy().standard_stock_return.unstated_condition_facts
        is UnstatedConditionFacts.REVIEW_REQUIRED
    )


# ---------------------------------------------------------------------------
# The narrowing itself
# ---------------------------------------------------------------------------


def test_in_window_and_otherwise_unobjectionable_approves() -> None:
    """The operator's ask, exactly. Nothing is stated but the date, and it approves.

    Before this setting existed the same case reached `REVIEW_REQUIRED` on
    `REQUIRED_FACT_UNKNOWN`, and the only route to an approval was a supervisor
    override -- which is a policy engine deciding nothing.
    """
    outcome = _evaluate(_policy(UnstatedConditionFacts.NOT_EVALUATED), _facts())

    assert outcome.route is PolicyRoute.STANDARD_RETURN
    assert outcome.decision is EligibilityDecision.APPROVE
    assert outcome.reason_codes == (PolicyReasonCode.WITHIN_STANDARD_RETURN_WINDOW,)
    assert PolicyRule.WITHIN_30_DAYS in outcome.applied_rules


def test_outside_the_window_takes_the_configured_decision() -> None:
    outcome = _evaluate(
        _policy(UnstatedConditionFacts.NOT_EVALUATED),
        _facts(request_date=_local(2025, 12, 1)),
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.OUTSIDE_STANDARD_RETURN_WINDOW,)
    assert PolicyRule.OUTSIDE_STANDARD_WINDOW in outcome.applied_rules


@pytest.mark.parametrize(
    ("request_day", "expected"),
    [
        (13, EligibilityDecision.APPROVE),  # day 30
        (14, EligibilityDecision.REVIEW_REQUIRED),  # day 31
    ],
)
def test_the_window_boundary_still_decides_to_the_day(
    request_day: int, expected: EligibilityDecision
) -> None:
    """Narrowing what is determinative does not blur the boundary that remains."""
    outcome = _evaluate(
        _policy(UnstatedConditionFacts.NOT_EVALUATED),
        _facts(request_date=_local(2025, 11, request_day, 23)),
    )

    assert outcome.decision is expected


def test_an_undated_order_reviews_rather_than_approving() -> None:
    """`TriState` exists because known-false and not-mentioned differ, and so do
    "bought on the 14th" and "nobody can say when".

    With the other checks stood down the window is the only question left, so an
    absent basis is not a smaller problem than it was -- it is the whole
    problem, and the answer is a human, never a guessed date.
    """
    outcome = _evaluate(_policy(UnstatedConditionFacts.NOT_EVALUATED), _facts(purchase_date=None))

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.PURCHASE_DATE_UNKNOWN,)


def test_a_stated_failure_still_rejects_and_still_rejects_first() -> None:
    """The narrowing lets silence stop objecting. It does not silence evidence.

    An item the customer said was installed is rejected because it was
    installed, and it is rejected while it is still inside the window -- the
    ordering the evaluator has always kept, because "it was installed" is a more
    useful answer than "it was also late".
    """
    outcome = _evaluate(
        _policy(UnstatedConditionFacts.NOT_EVALUATED), _facts(installed=TriState.TRUE)
    )

    assert outcome.decision is EligibilityDecision.REJECT
    assert outcome.reason_codes == (PolicyReasonCode.STANDARD_RETURN_CONDITION_FAILED,)
    assert PolicyRule.NEW_RESALEABLE_CONDITION in outcome.applied_rules
    assert outcome.unevaluated_checks == ()


def test_switching_the_other_checks_back_on_is_one_configuration_value() -> None:
    """The same facts, two releases, two answers. No code between them.

    This is what "reversible by configuration" has to mean: the rules were never
    deleted, so a later release re-enables them by changing one value and
    publishing.
    """
    facts = _facts()

    narrowed = _evaluate(_policy(UnstatedConditionFacts.NOT_EVALUATED), facts)
    restored = _evaluate(_policy(UnstatedConditionFacts.REVIEW_REQUIRED), facts)

    assert narrowed.decision is EligibilityDecision.APPROVE
    assert restored.decision is EligibilityDecision.REVIEW_REQUIRED
    assert restored.reason_codes == (PolicyReasonCode.REQUIRED_FACT_UNKNOWN,)


# ---------------------------------------------------------------------------
# An approval that skipped checks says so
# ---------------------------------------------------------------------------


def test_a_window_only_approval_names_every_check_it_did_not_apply() -> None:
    """`STOCK_CLASS_FROM_CONFIGURATION`'s rule, applied to the same problem.

    An approval taken on an assumption must not read like one taken on evidence,
    so the marker and the list of skipped checks travel with the outcome.
    """
    outcome = _evaluate(_policy(UnstatedConditionFacts.NOT_EVALUATED), _facts())

    assert outcome.decision is EligibilityDecision.APPROVE
    assert PolicyRule.CONDITION_FACTS_NOT_EVALUATED in outcome.applied_rules
    assert outcome.unevaluated_checks == ALL_CHECKS
    # And it must not claim the rule it did not apply.
    assert PolicyRule.NEW_RESALEABLE_CONDITION not in outcome.applied_rules


def test_a_fully_evidenced_approval_is_distinguishable_from_a_window_only_one() -> None:
    """The other half of the same claim: with the evidence present, nothing is
    marked, and the record is byte-for-byte what it was before the setting
    existed."""
    outcome = _evaluate(_policy(UnstatedConditionFacts.NOT_EVALUATED), _facts(**FULLY_EVIDENCED))

    assert outcome.decision is EligibilityDecision.APPROVE
    assert outcome.unevaluated_checks == ()
    assert PolicyRule.CONDITION_FACTS_NOT_EVALUATED not in outcome.applied_rules
    assert PolicyRule.NEW_RESALEABLE_CONDITION in outcome.applied_rules


def test_only_the_checks_that_were_actually_unanswered_are_named() -> None:
    """Partial evidence is recorded as partial, not as none.

    A conversation that establishes four of the thirteen facts has established
    four of them, and an audit reading `unevaluated_checks` must be able to see
    exactly which nine were not.
    """
    stated = {
        "condition_new": TriState.TRUE,
        "used": TriState.FALSE,
        "installed": TriState.FALSE,
        "damaged": TriState.FALSE,
    }
    outcome = _evaluate(_policy(UnstatedConditionFacts.NOT_EVALUATED), _facts(**stated))

    assert outcome.decision is EligibilityDecision.APPROVE
    assert outcome.unevaluated_checks == (
        "suitable_for_resale",
        "original_packaging",
        "packaging_undamaged",
        "all_original_parts",
        "modified",
        "rebuilt",
        "reconditioned",
        "repaired",
        "altered",
    )


def test_a_review_past_the_window_carries_the_marker_too() -> None:
    """Not only approvals. Any outcome reached with checks stood down says so,
    because a supervisor looking at a late return needs to know the condition
    facts were never established either."""
    outcome = _evaluate(
        _policy(UnstatedConditionFacts.NOT_EVALUATED),
        _facts(request_date=_local(2025, 12, 1)),
    )

    assert PolicyRule.CONDITION_FACTS_NOT_EVALUATED in outcome.applied_rules
    assert outcome.unevaluated_checks == ALL_CHECKS


def test_an_undated_review_carries_the_marker_too() -> None:
    outcome = _evaluate(_policy(UnstatedConditionFacts.NOT_EVALUATED), _facts(purchase_date=None))

    assert PolicyRule.CONDITION_FACTS_NOT_EVALUATED in outcome.applied_rules
    assert outcome.unevaluated_checks == ALL_CHECKS


# ---------------------------------------------------------------------------
# The pairing is structural, not conventional
# ---------------------------------------------------------------------------


def _provenance() -> PolicyProvenance:
    return PolicyProvenance(
        policy_id="p",
        policy_version="v",
        authority="a",
        source_document="d",
        source_revision="r",
        evaluated_at=datetime(2025, 11, 1, tzinfo=UTC),
    )


def test_an_outcome_cannot_skip_checks_without_saying_which() -> None:
    """Two mechanisms rather than one, exactly as `APPROVE` already has.

    The evaluator is written to construct the marker and the list together; this
    is what stops a later edit that reorders a branch from shipping an approval
    whose skipped checks nobody recorded.
    """
    with pytest.raises(ValidationError, match="CONDITION_FACTS_NOT_EVALUATED"):
        PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=EligibilityDecision.APPROVE,
            reason_codes=(PolicyReasonCode.WITHIN_STANDARD_RETURN_WINDOW,),
            applied_rules=(PolicyRule.STANDARD_STOCK_ITEM,),
            unevaluated_checks=("installed",),
            provenance=_provenance(),
        )


def test_an_outcome_cannot_claim_it_skipped_checks_it_did_not() -> None:
    with pytest.raises(ValidationError, match="must name the checks"):
        PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=EligibilityDecision.APPROVE,
            reason_codes=(PolicyReasonCode.WITHIN_STANDARD_RETURN_WINDOW,),
            applied_rules=(
                PolicyRule.STANDARD_STOCK_ITEM,
                PolicyRule.CONDITION_FACTS_NOT_EVALUATED,
            ),
            provenance=_provenance(),
        )


def test_skipped_checks_are_not_recorded_twice() -> None:
    with pytest.raises(ValidationError, match="unevaluated checks must be unique"):
        PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=EligibilityDecision.APPROVE,
            reason_codes=(PolicyReasonCode.WITHIN_STANDARD_RETURN_WINDOW,),
            applied_rules=(
                PolicyRule.STANDARD_STOCK_ITEM,
                PolicyRule.CONDITION_FACTS_NOT_EVALUATED,
            ),
            unevaluated_checks=("installed", "installed"),
            provenance=_provenance(),
        )


# ---------------------------------------------------------------------------
# What the narrowing does not reach
# ---------------------------------------------------------------------------


def test_the_narrowing_does_not_reach_the_routed_paths() -> None:
    """A delivery claim is still a delivery claim, in or out of any window.

    The setting governs the standard stocked path's condition checks and nothing
    else -- warranty and delivery-claim routing are decided before it is read,
    and a release that narrowed those would be deciding a carrier claim on a
    30-day boundary.
    """
    from return_platform.policy import ReturnReason

    outcome = _evaluate(
        _policy(UnstatedConditionFacts.NOT_EVALUATED),
        _facts(return_reason=ReturnReason.SHIPPING_DAMAGE),
    )

    assert outcome.route is PolicyRoute.DELIVERY_CLAIM
    assert outcome.decision is None
    assert outcome.unevaluated_checks == ()


def test_the_narrowing_does_not_reach_a_contract_override() -> None:
    outcome = _evaluate(
        _policy(UnstatedConditionFacts.NOT_EVALUATED),
        _facts(contract_override_reference="MSA-2024-118"),
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.CUSTOMER_CONTRACT_OVERRIDE_REQUIRES_REVIEW,)
    assert outcome.unevaluated_checks == ()


def test_an_unknown_damage_cause_is_still_a_question_for_a_human() -> None:
    """`damaged = TRUE` with no established cause routes to review before the
    standard path is entered at all, so the narrowing never sees it. Approving
    that on a window would be the carrier claim the baseline forbids by name."""
    outcome = _evaluate(
        _policy(UnstatedConditionFacts.NOT_EVALUATED), _facts(damaged=TriState.TRUE)
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.DAMAGE_CAUSE_UNKNOWN,)
