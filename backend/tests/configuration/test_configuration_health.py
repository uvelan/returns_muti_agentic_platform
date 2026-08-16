"""Plan sect. 5.4: an invalid configuration refuses production and is visible in dev.

Phase 1 shipped `validate_copilot_agent_binding` and Phase 3A shipped
`validate_return_eligibility_policy`, and both were called only from a request
path -- so a deployment whose mapping was dangling started perfectly, reported
itself ready, and failed one turn at a time in front of an associate. The plan
asks for the other half:

```text
production  -> startup fails (as the Vault rule already does)
dev / CI    -> /health/ready reports configuration unhealthy
```

`require_healthy_configuration` is that split, and `evaluate_configuration_health`
is the one place that decides what "healthy" means, so the two environments
cannot drift into disagreeing about it. The readiness half is proven in
`tests/api/test_readiness_reports_configuration_health.py`.

The third property here is the one that is easiest to lose and worst to lose: an
absent eligibility policy is an **operational** failure, reported as its own
check with its own code. It is not a bad agent mapping, and above all it is not
`REVIEW_REQUIRED` -- a platform that answered review to "no rule set is
published" would queue every return to a human and look like it was working.
"""

from __future__ import annotations

import pytest

from return_platform.configuration.return_configuration import (
    ConfigurationHealthFailure,
    ConfigurationInvalidError,
    ReturnPlatformConfiguration,
    evaluate_configuration_health,
    load_return_configuration,
    require_healthy_configuration,
)
from return_platform.configuration.settings import (
    DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH,
    DEFAULT_RETURN_CONFIGURATION_PATH,
    PRODUCTION_ENVIRONMENT,
)
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.policy import EligibilityDecision


@pytest.fixture(scope="module")
def shipped_configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration


@pytest.fixture(scope="module")
def shipped_agent_policy_ids() -> tuple[str, ...]:
    return tuple(load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH).agent_policies)


def _with_copilot_agent(
    configuration: ReturnPlatformConfiguration,
    agent_id: str | None,
) -> ReturnPlatformConfiguration:
    return configuration.model_copy(
        update={
            "copilot": configuration.copilot.model_copy(
                update={"order_discovery_agent_id": agent_id}
            )
        }
    )


def _without_eligibility_policy(
    configuration: ReturnPlatformConfiguration,
) -> ReturnPlatformConfiguration:
    return configuration.model_copy(update={"return_eligibility_policy": None})


def _codes(failures: tuple[ConfigurationHealthFailure, ...]) -> tuple[str, ...]:
    return tuple(failure.code for failure in failures)


# --- The shipped configuration is the live case ------------------------------


def test_the_shipped_configuration_is_healthy(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """The check that would have caught the original defect, run on the real files.

    Renaming the agent in a schema release without renaming the mapping fails
    here rather than in an associate's conversation.
    """
    assert evaluate_configuration_health(shipped_configuration, shipped_agent_policy_ids) == ()


def test_a_healthy_configuration_starts_in_production(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    assert (
        require_healthy_configuration(
            shipped_configuration,
            shipped_agent_policy_ids,
            environment=PRODUCTION_ENVIRONMENT,
        )
        == ()
    )


# --- An unset mapping --------------------------------------------------------


def test_an_unset_agent_mapping_refuses_production_startup(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """No fallback to "the only registered policy", and no starting anyway."""
    unset = _with_copilot_agent(shipped_configuration, None)

    with pytest.raises(ConfigurationInvalidError) as refused:
        require_healthy_configuration(
            unset,
            shipped_agent_policy_ids,
            environment=PRODUCTION_ENVIRONMENT,
        )

    assert _codes(refused.value.failures) == ("COPILOT_AGENT_CONFIGURATION_INVALID",)


@pytest.mark.parametrize("environment", ["development", "test", "staging"])
def test_an_unset_agent_mapping_is_reported_rather_than_fatal_outside_production(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
    environment: str,
) -> None:
    """A developer mid-change must be able to boot and be told.

    `staging` is included deliberately: the Vault precedent this copies keys on
    `== "production"` alone, and quietly widening the refusal here would make the
    two rules disagree about which environments are allowed to degrade.
    """
    failures = require_healthy_configuration(
        _with_copilot_agent(shipped_configuration, None),
        shipped_agent_policy_ids,
        environment=environment,
    )

    assert _codes(failures) == ("COPILOT_AGENT_CONFIGURATION_INVALID",)
    assert "is not configured" in failures[0].message


# --- A dangling mapping ------------------------------------------------------


def test_a_dangling_agent_mapping_refuses_production_startup(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """The realistic regression: the schema release renamed the agent."""
    dangling = _with_copilot_agent(shipped_configuration, "order_discovery")

    with pytest.raises(ConfigurationInvalidError) as refused:
        require_healthy_configuration(
            dangling,
            shipped_agent_policy_ids,
            environment=PRODUCTION_ENVIRONMENT,
        )

    assert _codes(refused.value.failures) == ("COPILOT_AGENT_CONFIGURATION_INVALID",)
    assert "names no agent policy" in refused.value.failures[0].message


def test_a_dangling_agent_mapping_is_reported_in_development(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    failures = require_healthy_configuration(
        _with_copilot_agent(shipped_configuration, "order_discovery"),
        shipped_agent_policy_ids,
        environment="development",
    )

    assert _codes(failures) == ("COPILOT_AGENT_CONFIGURATION_INVALID",)


def test_an_empty_active_schema_dangles_every_mapping(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    """Publishing no agent policies is a dangling mapping, not an exemption."""
    failures = evaluate_configuration_health(shipped_configuration, ())

    assert _codes(failures) == ("COPILOT_AGENT_CONFIGURATION_INVALID",)


# --- An absent eligibility policy, and why it is its own check ---------------


def test_an_absent_eligibility_policy_refuses_production_startup(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ConfigurationInvalidError) as refused:
        require_healthy_configuration(
            _without_eligibility_policy(shipped_configuration),
            shipped_agent_policy_ids,
            environment=PRODUCTION_ENVIRONMENT,
        )

    assert _codes(refused.value.failures) == ("RETURN_ELIGIBILITY_POLICY_MISSING",)


def test_an_absent_eligibility_policy_is_distinct_from_a_bad_agent_mapping(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """Two defects, two checks, both reported.

    Collected rather than raised one at a time: an operator fixing a dangling
    mapping must not have to redeploy to discover the policy is missing too.
    """
    broken = _without_eligibility_policy(_with_copilot_agent(shipped_configuration, None))

    failures = evaluate_configuration_health(broken, shipped_agent_policy_ids)

    assert {failure.check for failure in failures} == {
        "COPILOT_AGENT_BINDING",
        "RETURN_ELIGIBILITY_POLICY",
    }
    assert _codes(failures) == (
        "COPILOT_AGENT_CONFIGURATION_INVALID",
        "RETURN_ELIGIBILITY_POLICY_MISSING",
    )


def test_an_absent_eligibility_policy_is_an_operational_failure_not_a_review(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """The distinction the whole check exists to preserve.

    `REVIEW_REQUIRED` is the evaluator working: a published rule set looked at a
    return and asked for a human. An absent policy is no rule set at all. If the
    second ever presented as the first, every return would queue to a human and
    the platform would report itself healthy while doing it -- so an absent
    policy surfaces only as a configuration failure and never as an eligibility
    decision.
    """
    failures = evaluate_configuration_health(
        _without_eligibility_policy(shipped_configuration),
        shipped_agent_policy_ids,
    )

    (failure,) = failures
    assert failure.check == "RETURN_ELIGIBILITY_POLICY"
    assert failure.code == "RETURN_ELIGIBILITY_POLICY_MISSING"
    assert failure.code not in {decision.value for decision in EligibilityDecision}
    assert EligibilityDecision.REVIEW_REQUIRED.value not in failure.message
    assert "review" in failure.message, (
        "the refusal should say out loud that falling to review is the wrong answer"
    )
