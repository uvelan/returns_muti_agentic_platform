"""`POST /api/config/policy/preview` -- what a draft policy would decide (CFG-8).

The Policy screen's block 7: an operator changes `standard_stock_return.purchase_window.days`
from 30 to 45 and wants to know, before publishing, whether that does what they think. This
module answers that against a **fabricated** case only -- `PolicyPreviewSample` -- never a real
one. There is no case id, no graph read, and no persistence: the two blocks the caller submits
(`return_eligibility_policy`, `policy_evaluation`) are validated with the exact models the release
pipeline validates them with (`ReturnEligibilityPolicy`, `PolicyEvaluationConfiguration`) and, if
they parse, handed straight to `policy.evaluator.evaluate_return_eligibility` -- the same pure
function the workflow's `evaluate_case_eligibility` activity calls. Two implementations of "what
would this policy decide" would disagree eventually; this has one.

**The disabled gate is answered the same way the workflow answers it**, not invented here.
`evaluate_case_eligibility` (`workflows/return_case_activities.py`) checks
`policy_evaluation.enabled` before it requires a policy at all, and when it is off records
`policy_evaluation_state = PolicyGateState.SKIPPED_BY_CONFIGURATION` and
`policy_evaluation_skip_reason` on the case -- no route, no decision. This module imports
`PolicyGateState` from the workflow (read-only) rather than restating its string literal, so the
two can never drift the way a screen inventing its own "SKIPPED" label could.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from return_platform.configuration.api.releases import _dotted_error_path
from return_platform.configuration.return_configuration import PolicyEvaluationConfiguration
from return_platform.policy.eligibility_policy import ReturnEligibilityPolicy
from return_platform.policy.evaluation_input import PolicyEvaluationInput
from return_platform.policy.evaluator import PolicyClock, evaluate_return_eligibility
from return_platform.policy.vocabulary import ReturnReason, TriState
from return_platform.workflows.return_case_workflow import PolicyGateState

__all__ = [
    "PolicyPreviewError",
    "PolicyPreviewRequest",
    "PolicyPreviewSample",
    "evaluate_policy_preview",
]


class PolicyPreviewError(Exception):
    """A submitted block, or the sample, failed validation.

    Carries the same `{path, message, type}` shape `_validation_errors`
    (`POST /validate/{domain_key}`) reports, built through the same
    `_dotted_error_path` -- so a 422 from this route maps to a field exactly
    the way a 422 from `/validate` does, and the two never invent two spellings
    of the same path.
    """

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("policy preview request failed validation")
        self.errors = errors


#: Block 3's checklist names ("What the item must be"), mapped to the
#: `PolicyEvaluationInput` field each one resolves. Every name but `new` is
#: identical to its field; `new` is the requirement name
#: (`ResaleConditionRequirements.new`) but the fact it gates is `condition_new`
#: (`PolicyEvaluationInput.resale_condition_facts` pairs them) -- `bool` is a
#: reserved-ish name to shadow on a fact model, which is exactly why the field
#: was never called that.
_FACT_FIELD_BY_CHECK_NAME: dict[str, str] = {
    "new": "condition_new",
    "suitable_for_resale": "suitable_for_resale",
    "original_packaging": "original_packaging",
    "packaging_undamaged": "packaging_undamaged",
    "all_original_parts": "all_original_parts",
    "used": "used",
    "installed": "installed",
    "modified": "modified",
    "rebuilt": "rebuilt",
    "reconditioned": "reconditioned",
    "repaired": "repaired",
    "altered": "altered",
    "damaged": "damaged",
}

_TRISTATE_BY_WIRE_VALUE: dict[str, TriState] = {
    "TRUE": TriState.TRUE,
    "FALSE": TriState.FALSE,
    "UNKNOWN": TriState.UNKNOWN,
}

StockClassificationSample = Literal["STANDARD_STOCK", "SPECIAL_ORDER", "UNRESOLVED"]


class PolicyPreviewSample(BaseModel):
    """The preview form's fabricated case (brief item 7), never a real one."""

    model_config = ConfigDict(extra="forbid")

    days_since_purchase: int = Field(default=10, ge=0, le=36_500)
    #: What the preview form calls "stock classification (standard / special
    #: order / unresolved)". `UNRESOLVED` leaves the three underlying facts
    #: `UNKNOWN`, so the *submitted* `stock_classification.unresolved_default`
    #: decides -- which is the point of the row: it shows what "unresolved"
    #: does under the draft, not a canned answer this form supplies instead.
    stock_classification: StockClassificationSample = "STANDARD_STOCK"
    #: One of `_FACT_FIELD_BY_CHECK_NAME`'s keys -> `"TRUE" | "FALSE" | "UNKNOWN"`.
    #: A name omitted here is `UNKNOWN` ("not stated"), matching the checklist's
    #: own tri-state default.
    facts: dict[str, str] = Field(default_factory=dict)
    #: A `ReturnReason` value, or `None` for "not stated" (`UNKNOWN`).
    reason: str | None = None


class PolicyPreviewRequest(BaseModel):
    """`POST /api/config/policy/preview`'s body (brief item A)."""

    model_config = ConfigDict(extra="forbid")

    return_eligibility_policy: dict[str, Any]
    policy_evaluation: dict[str, Any]
    sample: PolicyPreviewSample = Field(default_factory=PolicyPreviewSample)


def _validated_blocks(
    body: PolicyPreviewRequest,
) -> tuple[ReturnEligibilityPolicy, PolicyEvaluationConfiguration]:
    """Validate the two submitted blocks against the release models, collecting
    every error under its own block's path rather than stopping at the first."""
    errors: list[dict[str, Any]] = []
    policy: ReturnEligibilityPolicy | None = None
    evaluation: PolicyEvaluationConfiguration | None = None

    try:
        policy = ReturnEligibilityPolicy.model_validate(body.return_eligibility_policy)
    except ValidationError as exc:
        errors.extend(
            {
                "path": f"return_eligibility_policy.{_dotted_error_path(error['loc'])}"
                if error["loc"]
                else "return_eligibility_policy",
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        )

    try:
        evaluation = PolicyEvaluationConfiguration.model_validate(body.policy_evaluation)
    except ValidationError as exc:
        errors.extend(
            {
                "path": f"policy_evaluation.{_dotted_error_path(error['loc'])}"
                if error["loc"]
                else "policy_evaluation",
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        )

    unknown_facts = sorted(set(body.sample.facts) - set(_FACT_FIELD_BY_CHECK_NAME))
    for name in unknown_facts:
        errors.append(
            {
                "path": f"sample.facts.{name}",
                "message": f"{name!r} is not one of the standard-stock-return checklist facts",
                "type": "value_error",
            }
        )
    for name, value in body.sample.facts.items():
        if name in _FACT_FIELD_BY_CHECK_NAME and value not in _TRISTATE_BY_WIRE_VALUE:
            errors.append(
                {
                    "path": f"sample.facts.{name}",
                    "message": f"{value!r} is not TRUE, FALSE or UNKNOWN",
                    "type": "value_error",
                }
            )
    if body.sample.reason is not None and body.sample.reason not in set(ReturnReason):
        errors.append(
            {
                "path": "sample.reason",
                "message": f"{body.sample.reason!r} is not a recognised return reason",
                "type": "value_error",
            }
        )

    if errors or policy is None or evaluation is None:
        raise PolicyPreviewError(errors)
    return policy, evaluation


_STOCK_CLASSIFICATION_FACTS: dict[StockClassificationSample, tuple[TriState, TriState, TriState]] = {
    # (seller_stocked, special_order, non_stock)
    "STANDARD_STOCK": (TriState.TRUE, TriState.FALSE, TriState.FALSE),
    "SPECIAL_ORDER": (TriState.FALSE, TriState.TRUE, TriState.TRUE),
    "UNRESOLVED": (TriState.UNKNOWN, TriState.UNKNOWN, TriState.UNKNOWN),
}


def _fabricated_facts(sample: PolicyPreviewSample, *, evaluated_at: datetime) -> PolicyEvaluationInput:
    """Build the one-off `PolicyEvaluationInput` the sample describes.

    Never built from a real case: `purchase_date` and `delivery_date` are both
    derived from `days_since_purchase` counted back from the evaluation
    instant, so either purchase-window basis the draft configures produces the
    same window answer -- the sample does not collect a basis, only a window
    length.
    """
    seller_stocked, special_order, non_stock = _STOCK_CLASSIFICATION_FACTS[sample.stock_classification]
    purchase_date = evaluated_at - timedelta(days=sample.days_since_purchase)
    fact_values = {
        _FACT_FIELD_BY_CHECK_NAME[name]: _TRISTATE_BY_WIRE_VALUE[value]
        for name, value in sample.facts.items()
        if name in _FACT_FIELD_BY_CHECK_NAME and value in _TRISTATE_BY_WIRE_VALUE
    }
    return PolicyEvaluationInput(
        request_date=evaluated_at,
        purchase_date=purchase_date,
        delivery_date=purchase_date,
        seller_stocked=seller_stocked,
        special_order=special_order,
        non_stock=non_stock,
        return_reason=ReturnReason(sample.reason) if sample.reason else ReturnReason.UNKNOWN,
        **fact_values,
    )


def evaluate_policy_preview(body: PolicyPreviewRequest) -> dict[str, Any]:
    """The route's whole job: validate, then decide the fabricated sample.

    Raises `PolicyPreviewError` (mapped to a 422 by the router) for anything
    that does not validate; otherwise always returns -- there is no case to
    fail closed on and no IO to raise from.
    """
    policy, evaluation = _validated_blocks(body)

    if not evaluation.enabled:
        # The exact fact names `evaluate_case_eligibility` writes when the gate
        # is off (`configuration/return_configuration.py`'s own docstring
        # names them as "the two fact names to grep for"). No decision, no
        # route, no rule -- recording one here would tell an operator this
        # form decides eligibility when the gate does not.
        #
        # `evaluation.disabled_reason` is never `None` here --
        # `PolicyEvaluationConfiguration.require_reason_when_disabled` already
        # refused a disabled block with no reason, so `_validated_blocks`
        # above would have raised first. The `"UNSPECIFIED"` fallback is a
        # type-safety guard against that invariant loosening, not a reachable
        # branch today -- `test_preview_disabled_with_no_stated_reason_422s`
        # pins that it is refused before evaluation, not defaulted here.
        return {
            "evaluation_enabled": False,
            "decision": None,
            "route": None,
            "applied_rules": [],
            "conditions": [],
            "unanswered_checks": [],
            "reason_codes": [],
            "policy_evaluation_state": PolicyGateState.SKIPPED_BY_CONFIGURATION.value,
            "policy_evaluation_skip_reason": evaluation.disabled_reason or "UNSPECIFIED",
        }

    evaluated_at = datetime.now(UTC)
    facts = _fabricated_facts(body.sample, evaluated_at=evaluated_at)
    clock = PolicyClock(evaluated_at=evaluated_at, local_zone=ZoneInfo("UTC"), business_calendar=None)
    outcome = evaluate_return_eligibility(policy, facts, clock)

    return {
        "evaluation_enabled": True,
        "decision": outcome.decision.value if outcome.decision is not None else None,
        "route": outcome.route.value,
        "applied_rules": [rule.value for rule in outcome.applied_rules],
        "conditions": [condition.value for condition in outcome.conditions],
        "unanswered_checks": list(outcome.unevaluated_checks),
        "reason_codes": [code.value for code in outcome.reason_codes],
        "policy_evaluation_state": PolicyGateState.EVALUATED.value,
        "policy_evaluation_skip_reason": None,
    }
