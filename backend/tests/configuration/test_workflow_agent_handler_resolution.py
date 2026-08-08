"""Proves the Wave B3 stage/handler config engine's AGENT-handler capability
end to end: a configured workflow module resolves an agent implementation
through the canonical AgentRegistry and executes it, against the real
manifest/loader/validator pipeline and a real (if minimal) execution context.

`config/workflows/example_agent_dispatch.yaml` is a reference fixture built
for exactly this test -- it does not describe any live return-processing flow
(see that file's header comment, and `return_session.yaml`'s, for why the
real return-session workflow stays ACTIVITY-typed for now).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from return_platform.agents.contracts.context import AgentExecutionContext
from return_platform.agents.contracts.dto import (
    DiscoveryAssessmentRequest,
    DiscoveryCandidateInput,
)
from return_platform.agents.registry import AgentRegistry
from return_platform.bootstrap.context import SystemClock
from return_platform.configuration.application.compatibility import (
    LegacyCompatibilityAdapter,
)
from return_platform.configuration.application.runtime_configuration import (
    RuntimeConfigurationViewImpl,
)
from return_platform.configuration.application.snapshot import compute_checksum
from return_platform.configuration.domain.workflow import WorkflowStageHandlerType
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.platform.capabilities.registry import InMemoryCapabilityRegistry
from return_platform.security.principal import Principal

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_RETURN_CONFIG = _CONFIG_DIR / "returns" / "production.yaml"


class _RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def record(self, event: object) -> None:
        self.events.append(event)


class _AllowlistRedactor:
    def __init__(self, allowed: frozenset[str]) -> None:
        self._allowed = allowed

    def redact(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return {key: value for key, value in payload.items() if key in self._allowed}


@pytest.mark.asyncio
async def test_configured_agent_handler_resolves_and_executes_under_pinned_release() -> None:
    # 1. Build the real RuntimeSnapshot from the actual manifest/loader/validator
    #    pipeline -- config/workflows/example_agent_dispatch.yaml is a real,
    #    registered module, not a hand-built fixture.
    snapshot = LegacyCompatibilityAdapter(_CONFIG_DIR).build_canonical_snapshot()
    checksum = compute_checksum(snapshot)
    view = RuntimeConfigurationViewImpl("test-release", snapshot, checksum)

    # 2. Resolve the configured stage handler. RuntimeConfigurationView only
    #    exposes plain dicts via section() -- the typed WorkflowDefinition
    #    (with handler_for()) comes from the snapshot the view was built from.
    definition = snapshot.workflow.workflow["example_agent_dispatch"]
    handler = definition.handler_for("DISPATCH")
    assert handler is not None
    assert handler.type is WorkflowStageHandlerType.AGENT
    assert handler.agent == "order_discovery"

    # 3. Resolve and execute the real agent through AgentRegistry, under a
    #    pinned configuration release id carried on the execution context.
    return_configuration = load_return_configuration(_RETURN_CONFIG).configuration
    registry = AgentRegistry.build(return_configuration)
    agent = registry.resolve(handler.agent)

    audit = _RecordingAuditSink()
    context = AgentExecutionContext(
        configuration=view,
        capabilities=InMemoryCapabilityRegistry(),
        audit=audit,
        redactor=_AllowlistRedactor(frozenset({"orderReference"})),
        principal=Principal(subject="workflow-engine", roles=frozenset({"system"})),
        correlation_id="11111111-1111-1111-1111-111111111111",
        session_id="22222222-2222-2222-2222-222222222222",
        configuration_release_id=view.release_id,
        clock=SystemClock(),
    )

    request = DiscoveryAssessmentRequest(
        suppliedEvidence={"order_number": "WEB-12345", "source": "FERGUSONHOME_WEB"},
        candidates=(
            DiscoveryCandidateInput(
                candidateId="cand-1",
                orderReference="WEB-12345",
                customerReference="cust-1",
                evidenceReferences=("ORDER_NUMBER_MATCH",),
            ),
        ),
    )

    assessment = await agent.execute(request, context)

    assert assessment.rankedCandidates
    assert context.configuration_release_id == "test-release"
