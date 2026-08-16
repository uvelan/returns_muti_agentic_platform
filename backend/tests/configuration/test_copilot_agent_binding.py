"""The Copilot's agent id is configured, and the configured id must resolve.

The defect this closes: the frontend sent the literal `"order_discovery"` while
`config/dynamic_knowledge/active-schema.return-order.yaml` keys the policy
`order-discovery-agent`, so `order_discovery_activities` looked the id up in
`runtime.schema.agent_policies`, got `None`, and every single turn came back
`422 ORDER_AGENT_OUT_OF_SCOPE`. Nothing failed until an associate typed a
message, because nothing anywhere compared the two names.

That comparison is what these tests are. The first is the live one -- it reads
the two shipped files and refuses a mismatch between them, so renaming the agent
in a schema release without renaming the mapping fails here rather than in a
conversation. The rest prove the check has teeth, which the live test alone
cannot: a membership assertion that could never fail is indistinguishable from
one that always passes.
"""

from __future__ import annotations

import pytest

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
    validate_copilot_agent_binding,
)
from return_platform.configuration.settings import (
    DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH,
    DEFAULT_RETURN_CONFIGURATION_PATH,
)
from return_platform.dynamic_knowledge.config_loader import load_active_schema


@pytest.fixture(scope="module")
def shipped_configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration


@pytest.fixture(scope="module")
def shipped_agent_policy_ids() -> tuple[str, ...]:
    return tuple(load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH).agent_policies)


def test_the_shipped_configuration_states_the_copilot_agent(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    """Explicit, not inferred.

    "The only registered policy" would resolve correctly today and become a
    silent misroute the first time a second agent is published.
    """
    assert shipped_configuration.copilot.order_discovery_agent_id is not None


def test_the_configured_copilot_agent_exists_in_the_active_schema(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    resolved = validate_copilot_agent_binding(shipped_configuration, shipped_agent_policy_ids)

    assert resolved in shipped_agent_policy_ids


def test_a_dangling_mapping_is_refused(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """Renaming the agent without renaming the mapping is the realistic
    regression, and it is the exact shape of the original defect."""
    renamed = shipped_configuration.model_copy(
        update={
            "copilot": shipped_configuration.copilot.model_copy(
                update={"order_discovery_agent_id": "order_discovery"}
            )
        }
    )

    with pytest.raises(ValueError, match="names no agent policy"):
        validate_copilot_agent_binding(renamed, shipped_agent_policy_ids)


def test_an_unset_mapping_is_refused_rather_than_guessed(
    shipped_configuration: ReturnPlatformConfiguration,
    shipped_agent_policy_ids: tuple[str, ...],
) -> None:
    """A release cut before this block loads -- `copilot` is defaulted so it can
    -- but it does not silently acquire the one agent that happens to exist."""
    unset = shipped_configuration.model_copy(
        update={
            "copilot": shipped_configuration.copilot.model_copy(
                update={"order_discovery_agent_id": None}
            )
        }
    )

    with pytest.raises(ValueError, match="is not configured"):
        validate_copilot_agent_binding(unset, shipped_agent_policy_ids)
