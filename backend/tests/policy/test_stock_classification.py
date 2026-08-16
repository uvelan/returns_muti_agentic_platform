"""Where stocked-vs-special-order comes from when the source cannot say.

The extract carries no such field (verified 2026-08-15), so the answer is the
operator's, supplied through `stock_classification` in a released policy. These
tests pin the three properties that make that safe rather than a fabrication:

* configuration fills **silence only** -- a line the source classified is never
  overwritten;
* a decision taken on the configured default carries
  `STOCK_CLASS_FROM_CONFIGURATION`, so an assumption is auditable as one;
* `REVIEW_REQUIRED` still means review, so a deployment can put the fail-safe
  back with one line.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from return_platform.policy.eligibility_policy import (
    ReturnEligibilityPolicy,
    StockClassificationConfiguration,
)
from return_platform.policy.evaluation_input import PolicyEvaluationInput
from return_platform.policy.evaluator import evaluate_return_eligibility
from return_platform.policy.vocabulary import (
    PolicyRule,
    StockClassificationDefault,
    TriState,
)
from return_platform.workflows.stage_results import EligibilityDecision
from tests.policy.test_return_eligibility_evaluator import POLICY, _clock

pytestmark = pytest.mark.unit

_REQUEST = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
_PURCHASE = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _policy(classification: StockClassificationConfiguration) -> ReturnEligibilityPolicy:
    return POLICY.model_copy(update={"stock_classification": classification})


def _resaleable(**overrides: object) -> PolicyEvaluationInput:
    """A line that satisfies every standard-return condition except stock class."""
    facts: dict[str, object] = {
        "request_date": _REQUEST,
        "purchase_date": _PURCHASE,
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
    facts.update(overrides)
    return PolicyEvaluationInput(**facts)  # type: ignore[arg-type]


def test_review_required_is_the_model_default_so_silence_still_queues() -> None:
    """An unconfigured policy behaves exactly as the baseline safety rule says."""
    assert (
        StockClassificationConfiguration().unresolved_default
        is StockClassificationDefault.REVIEW_REQUIRED
    )
    outcome = evaluate_return_eligibility(
        _policy(StockClassificationConfiguration()), _resaleable(), _clock()
    )
    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert PolicyRule.STOCK_CLASS_FROM_CONFIGURATION not in outcome.applied_rules


def test_standard_stock_default_decides_and_records_that_it_assumed() -> None:
    """The operator's decision resolves the line -- and says so on the outcome."""
    outcome = evaluate_return_eligibility(
        _policy(
            StockClassificationConfiguration(
                unresolved_default=StockClassificationDefault.STANDARD_STOCK
            )
        ),
        _resaleable(),
        _clock(),
    )
    assert outcome.decision is EligibilityDecision.APPROVE
    assert PolicyRule.STOCK_CLASS_FROM_CONFIGURATION in outcome.applied_rules, (
        "an approval taken on an assumption must not read like one taken on evidence"
    )


def test_special_order_default_sends_unclassified_lines_to_the_manufacturer_path() -> None:
    outcome = evaluate_return_eligibility(
        _policy(
            StockClassificationConfiguration(
                unresolved_default=StockClassificationDefault.SPECIAL_ORDER
            )
        ),
        _resaleable(),
        _clock(),
    )
    # Manufacturer acceptance is unknown, so the special-order path reviews.
    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert PolicyRule.STOCK_CLASS_FROM_CONFIGURATION in outcome.applied_rules


def test_a_designated_sku_is_special_order_whatever_the_default_says() -> None:
    outcome = evaluate_return_eligibility(
        _policy(
            StockClassificationConfiguration(
                unresolved_default=StockClassificationDefault.STANDARD_STOCK,
                special_order_skus=("EM-9821",),
            )
        ),
        _resaleable(sku="EM-9821"),
        _clock(),
    )
    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert PolicyRule.STOCK_CLASS_FROM_CONFIGURATION in outcome.applied_rules


def test_a_designated_prefix_matches_and_an_undesignated_sku_does_not() -> None:
    classification = StockClassificationConfiguration(
        unresolved_default=StockClassificationDefault.STANDARD_STOCK,
        special_order_sku_prefixes=("SP",),
    )
    designated = evaluate_return_eligibility(
        _policy(classification), _resaleable(sku="SP-1234"), _clock()
    )
    ordinary = evaluate_return_eligibility(
        _policy(classification), _resaleable(sku="EM-9821"), _clock()
    )
    assert designated.decision is EligibilityDecision.REVIEW_REQUIRED
    assert ordinary.decision is EligibilityDecision.APPROVE


def test_configuration_fills_silence_and_never_contradicts_the_source() -> None:
    """`known false != not mentioned` runs in this direction too."""
    outcome = evaluate_return_eligibility(
        _policy(
            StockClassificationConfiguration(
                unresolved_default=StockClassificationDefault.SPECIAL_ORDER
            )
        ),
        _resaleable(
            seller_stocked=TriState.TRUE,
            special_order=TriState.FALSE,
            non_stock=TriState.FALSE,
        ),
        _clock(),
    )
    assert outcome.decision is EligibilityDecision.APPROVE
    assert PolicyRule.STOCK_CLASS_FROM_CONFIGURATION not in outcome.applied_rules, (
        "the source classified this line; configuration must not have been consulted"
    )


def test_the_seller_schedule_supplies_a_rate_attributed_to_the_seller() -> None:
    """A configured rate reaches the outcome, and can never pass as Ferguson's."""
    from return_platform.policy.eligibility_policy import SellerRestockingFeeSchedule
    from return_platform.policy.vocabulary import FeeAmountSource

    policy = POLICY.model_copy(
        update={
            "stock_classification": StockClassificationConfiguration(
                unresolved_default=StockClassificationDefault.STANDARD_STOCK
            ),
            "restocking_fee": POLICY.restocking_fee.model_copy(
                update={
                    "seller_schedule": SellerRestockingFeeSchedule(default_rate_basis_points=1500)
                }
            ),
        }
    )
    outcome = evaluate_return_eligibility(policy, _resaleable(), _clock())
    assert outcome.decision is EligibilityDecision.APPROVE
    fee = outcome.restocking_fee
    assert fee.applies is True
    assert fee.rate_basis_points == 1500
    assert fee.rate_source is FeeAmountSource.SELLER_CONFIGURATION, (
        "a seller-chosen rate must never present itself as published Ferguson policy"
    )
    assert fee.amount is None, (
        "the pure evaluator holds no line prices and must originate no figure"
    )


def test_no_schedule_means_applicability_with_no_figure() -> None:
    """Removing the schedule restores the original behaviour exactly."""
    policy = POLICY.model_copy(
        update={
            "stock_classification": StockClassificationConfiguration(
                unresolved_default=StockClassificationDefault.STANDARD_STOCK
            )
        }
    )
    fee = evaluate_return_eligibility(policy, _resaleable(), _clock()).restocking_fee
    assert fee.applies is True
    assert fee.rate_basis_points is None and fee.rate_source is None
