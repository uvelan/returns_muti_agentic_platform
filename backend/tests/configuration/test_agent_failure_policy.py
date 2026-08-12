"""Every agent declares whether its failure parks the case or is absorbed.

The policy existed only in prose. `AgentDescriptor` carried identity, timeouts,
retries and a circuit-breaker threshold -- and nothing about what to do when the
agent fails, so the rule that Order Discovery is blocking and Bay is best-effort
was unenforceable by construction.

Pinned per agent rather than checked in aggregate: getting one of these
backwards is silent and expensive. A blocking Bay stalls every return over an
advisory step; a best-effort Order Discovery lets a case proceed with no
confirmed order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from return_platform.agents.registry import AgentRegistry
from return_platform.configuration.return_configuration import load_return_configuration

CONFIG = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"

EXPECTED: dict[str, str] = {
    "order_discovery": "blocking",
    "return_workflow": "blocking",
    "order_analysis": "best_effort",
    "return_fulfillment": "best_effort",
    "bay_assignment": "best_effort",
    "feedback_learning": "best_effort",
}


@pytest.fixture(scope="module")
def registry() -> AgentRegistry:
    return AgentRegistry.build(load_return_configuration(CONFIG).configuration)


@pytest.mark.parametrize(("agent_id", "policy"), sorted(EXPECTED.items()))
def test_each_agent_declares_its_failure_policy(
    registry: AgentRegistry, agent_id: str, policy: str
) -> None:
    assert registry.descriptor(agent_id).failure_policy == policy


def test_every_configured_agent_is_accounted_for(registry: AgentRegistry) -> None:
    """A new agent must state its policy here, not inherit a default silently."""
    declared = {descriptor.agent_id for descriptor in registry.all_descriptors()}
    assert declared == set(EXPECTED)


def test_the_two_blocking_agents_are_the_required_ones(registry: AgentRegistry) -> None:
    """Stated as a set so adding a third blocking agent is a deliberate edit.

    Every blocking agent is a way for a return to stop, and the business flow
    names exactly two.
    """
    blocking = {
        descriptor.agent_id
        for descriptor in registry.all_descriptors()
        if descriptor.failure_policy == "blocking"
    }
    assert blocking == {"order_discovery", "return_workflow"}
