"""V1: `support_gate`, the review gate's released settings.

Contracts.md sect. 6 (the gate, its cadence, its timeout policy) and sect. 10
(the config key). Three things this file is actually guarding, none of which is
"the model has fields":

* the **shipped `production.yaml` block** parses into this model and says what
  DR-4 says it says -- the gate is on;
* a release cut before the block existed still loads, and loads with the gate
  **on** rather than off, because the failure mode of the other default is
  silently sending unreviewed messages to Support;
* the bounds are real. A release is operator-authored, and `max_reminders:
  100000` is a mailer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.support_gate_configuration import (
    MAX_REVIEW_REMINDERS,
    RequestGrouping,
    SupportGateConfiguration,
    TemplateReviewConfiguration,
    TemplateReviewTimeoutPolicy,
)

PRODUCTION_YAML = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"


@pytest.fixture(scope="module")
def production_block() -> dict[str, Any]:
    document = yaml.safe_load(PRODUCTION_YAML.read_text(encoding="utf-8"))
    assert "support_gate" in document, "the shipped release must carry the block"
    return dict(document["support_gate"])


# --------------------------------------------------------------------------- #
# The shipped release
# --------------------------------------------------------------------------- #


def test_the_shipped_block_parses(production_block: dict[str, Any]) -> None:
    gate = SupportGateConfiguration.model_validate(production_block)
    assert gate.request_grouping is RequestGrouping.ONE_PER_CASE
    assert gate.template_review.enabled is True
    assert gate.template_review.on_timeout is TemplateReviewTimeoutPolicy.HOLD


def test_the_review_wait_does_not_inherit_the_support_dev_override(
    production_block: dict[str, Any],
) -> None:
    """The reviewer's clock is not Support's clock, and here that is load-bearing.

    `return_case.support_response_wait_seconds` is 1800 in this file under an
    explicit `DEV SETTING ... BEFORE LIVE: restore 28800` banner -- a temporary
    workaround for an item-hold overselling window. The obvious-looking thing
    to do with a new wait was to mirror it, which would silently give a branch
    associate thirty minutes to review a draft and would spread a dev shortcut
    into a block that has nothing to do with holds.

    Pinned in *both* directions so this stays a real statement: the review wait
    is a working day, and it is not whatever that field currently says.
    """
    document = yaml.safe_load(PRODUCTION_YAML.read_text(encoding="utf-8"))
    support_wait = int(document["return_case"]["support_response_wait_seconds"])
    review_wait = SupportGateConfiguration.model_validate(
        production_block
    ).template_review.review_wait_seconds

    assert review_wait == 28_800, "one working day"
    assert review_wait != support_wait or support_wait == 28_800, (
        "if these have converged it must be because the Support SLA was restored, "
        "not because the review wait was shortened to match a dev override"
    )


def test_the_reminder_cadence_fits_inside_the_wait(production_block: dict[str, Any]) -> None:
    """A reminder interval longer than the wait sends nothing before the
    deadline, which is a gate that reminds nobody while claiming to."""
    review = SupportGateConfiguration.model_validate(production_block).template_review
    assert review.reminder_interval_seconds < review.review_wait_seconds
    assert review.max_reminders * review.reminder_interval_seconds <= review.review_wait_seconds


# --------------------------------------------------------------------------- #
# Defaults, and which way they fail
# --------------------------------------------------------------------------- #


def test_a_release_without_the_block_loads_with_the_gate_on() -> None:
    """DR-4, and the reason the default is not `False`.

    An older release that quietly became one that skips the review would send
    unreviewed messages to a human queue and look exactly like a release that
    had been configured to.
    """
    gate = SupportGateConfiguration()
    assert gate.template_review.enabled is True
    assert gate.template_review.on_timeout is TemplateReviewTimeoutPolicy.HOLD
    assert gate.request_grouping is RequestGrouping.ONE_PER_CASE


def test_the_field_is_defaulted_on_the_platform_model() -> None:
    """`ReturnPlatformConfiguration` must not require the block to construct."""
    assert "support_gate" in ReturnPlatformConfiguration.model_fields
    field = ReturnPlatformConfiguration.model_fields["support_gate"]
    assert field.default_factory is SupportGateConfiguration


# --------------------------------------------------------------------------- #
# The grammar is closed
# --------------------------------------------------------------------------- #


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        SupportGateConfiguration.model_validate(
            {"request_grouping": "one_per_case", "escalation_email": "ops@example.com"}
        )
    with pytest.raises(ValidationError):
        TemplateReviewConfiguration.model_validate({"enabled": True, "auto_approve_after": 10})


def test_an_unknown_grouping_is_refused() -> None:
    """Not silently collapsed to `one_per_case`: that would send one message
    where the operator asked for two."""
    with pytest.raises(ValidationError):
        SupportGateConfiguration.model_validate({"request_grouping": "by_customer"})


def test_an_unknown_timeout_policy_is_refused() -> None:
    with pytest.raises(ValidationError):
        TemplateReviewConfiguration.model_validate({"on_timeout": "send_anyway"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("review_wait_seconds", 0),
        ("review_wait_seconds", 59),
        ("reminder_interval_seconds", 0),
        ("max_reminders", -1),
        ("max_reminders", MAX_REVIEW_REMINDERS + 1),
    ],
)
def test_out_of_range_values_are_refused(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        TemplateReviewConfiguration.model_validate({field: value})


def test_zero_reminders_is_legal() -> None:
    """A deployment that wants the deadline and no nagging is a real choice,
    and it is different from a misconfigured one."""
    assert TemplateReviewConfiguration.model_validate({"max_reminders": 0}).max_reminders == 0


def test_the_model_is_frozen() -> None:
    """Configuration is a release, not a runtime knob: nothing mutates one."""
    gate = SupportGateConfiguration()
    with pytest.raises(ValidationError):
        gate.request_grouping = RequestGrouping.BY_SHIP_FROM  # type: ignore[misc]


def test_every_declared_grouping_is_in_the_contract_enumeration() -> None:
    """Contracts.md sect. 6 froze three. Not two, and not four."""
    assert {member.value for member in RequestGrouping} == {
        "one_per_case",
        "by_shipping_mode",
        "by_ship_from",
    }


def test_every_declared_timeout_policy_is_in_the_contract_enumeration() -> None:
    assert {member.value for member in TemplateReviewTimeoutPolicy} == {
        "auto_send",
        "hold",
        "escalate",
    }
