"""W4.2: an agent configuration edit is a release, not a file write.

The properties that matter are the four the step's Why names: the packaged YAML
is not written, the edit survives a redeploy because it lives in a release, every
replica sees it because every replica reads that release, and the change is in
the audit trail. Each is asserted directly rather than inferred from a status
code.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.ai.routing.tasks import load_ai_gateway_configuration
from return_platform.bootstrap.adapters.governance_agent_configuration import (
    AgentConfigurationProposalActivator,
)
from return_platform.configuration.api.agents import router
from return_platform.configuration.application.agent_configuration import (
    AgentConfigurationService,
)
from return_platform.configuration.graph_repository import (
    InMemoryConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import BACKEND_ROOT, Settings
from return_platform.configuration.snapshot import (
    AGENT_MODULES_DOMAIN_KEY,
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.dependency_simulation.configuration import (
    load_dependency_simulation_configuration,
)
from return_platform.platform.governance.errors import ActivationRefused, ForbiddenProposalKey
from return_platform.platform.governance.proposal import ProposalStatus, ProposalType
from return_platform.resources import RuntimeResources
from return_platform.security.principal import Principal
from tests.governance_doubles import build_test_kernel

CONFIG_DIR = BACKEND_ROOT / "config"
AGENT_ID = "agent.learning"
AGENT_PATH = CONFIG_DIR / "agents" / "learning.yaml"
NOW = datetime(2026, 8, 13, 11, 0, tzinfo=UTC)


@pytest.fixture
def service() -> AgentConfigurationService:
    return AgentConfigurationService(CONFIG_DIR)


def _edited(service: AgentConfigurationService) -> dict[str, Any]:
    current = service.read(AGENT_ID)
    assert current is not None
    document = copy.deepcopy(current.document)
    document["payload"]["enabled"] = False
    return document


@pytest.fixture
def client(service: AgentConfigurationService) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.agent_configuration = service
    kernel, store, audit = build_test_kernel()
    app.state.proposal_kernel = kernel
    app.state.governance_store = store
    app.state.governance_audit = audit

    @app.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            subject="configuration-admin", roles=frozenset({"console_admin"})
        )
        request.state.correlation_id = "agent-configuration-test"
        return await call_next(request)

    return TestClient(app)


# --- the sink ----------------------------------------------------------------


def test_an_edit_does_not_write_the_packaged_file(
    client: TestClient, service: AgentConfigurationService
) -> None:
    """Section 8 forbids a runtime change writing packaged configuration, and
    the previous implementation did exactly that."""
    before = AGENT_PATH.read_bytes()
    response = client.put(f"/api/agents/{AGENT_ID}", json={"document": _edited(service)})
    assert response.status_code == 202, response.text
    assert AGENT_PATH.read_bytes() == before


def test_an_edit_becomes_a_proposal_awaiting_review(
    client: TestClient, service: AgentConfigurationService
) -> None:
    response = client.put(f"/api/agents/{AGENT_ID}", json={"document": _edited(service)})
    data = response.json()["data"]
    assert data["status"] == ProposalStatus.REVIEW_PENDING
    assert data["affectedKeys"] == ["agent.payload.enabled"]
    assert data["proposedBy"] == "configuration-admin"

    stored = client.app.state.governance_store.proposals[data["proposalId"]]  # type: ignore[attr-defined]
    assert stored.proposal_type is ProposalType.CONFIGURATION
    assert stored.subject_id == AGENT_ID
    # Validate-by-reload survived the change of sink, and says what it validated.
    assert stored.validation_receipt is not None
    assert stored.validation_receipt.startswith("agent-module-reload:")


def test_the_edit_reaches_the_audit_trail(
    client: TestClient, service: AgentConfigurationService
) -> None:
    """ "Absent from the audit trail" was one of the three defects W4.2 names."""
    client.put(f"/api/agents/{AGENT_ID}", json={"document": _edited(service)})
    audit = client.app.state.governance_audit  # type: ignore[attr-defined]
    assert audit.actions() == [
        "PROPOSAL_SUBMITTED",
        "PROPOSAL_VALIDATED",
        "PROPOSAL_REVIEW_REQUESTED",
    ]
    assert audit.entries[0]["actor"] == "configuration-admin"
    assert audit.entries[0]["details"]["subject_id"] == AGENT_ID


def test_a_refused_document_names_the_field_and_proposes_nothing(
    client: TestClient, service: AgentConfigurationService
) -> None:
    document = _edited(service)
    document["payload"]["not_a_field"] = True
    response = client.put(f"/api/agents/{AGENT_ID}", json={"document": document})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "AGENT_CONFIGURATION_REJECTED"
    assert "not_a_field" in detail["message"]
    assert client.app.state.governance_store.proposals == {}  # type: ignore[attr-defined]


def test_a_document_the_loader_refuses_is_refused_here(
    client: TestClient, service: AgentConfigurationService
) -> None:
    """The loader is the single definition of a valid module, and an edit goes
    through the same one the platform boots from."""
    document = _edited(service)
    document["module_id"] = "agent.something_else"
    response = client.put(f"/api/agents/{AGENT_ID}", json={"document": document})
    assert response.status_code == 422
    assert "module_id" in response.json()["detail"]["message"]


def test_an_unknown_agent_is_a_404(client: TestClient) -> None:
    response = client.put("/api/agents/agent.nope", json={"document": {}})
    assert response.status_code == 404


def test_the_edit_is_refused_when_governance_is_absent(
    service: AgentConfigurationService,
) -> None:
    """Never applied ungoverned. Before W4.2 an edit with nowhere to record it
    simply took effect."""
    app = FastAPI()
    app.include_router(router)
    app.state.agent_configuration = service

    @app.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            subject="configuration-admin", roles=frozenset({"console_admin"})
        )
        return await call_next(request)

    response = TestClient(app).put(f"/api/agents/{AGENT_ID}", json={"document": _edited(service)})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "GOVERNANCE_UNAVAILABLE"


# --- reads follow the release ------------------------------------------------


def test_a_release_document_is_what_a_read_returns() -> None:
    """The redeploy half: the file is seed, the release is truth."""
    released = {AGENT_ID: {"module_id": AGENT_ID, "module_type": "AGENT", "payload": {}}}
    released[AGENT_ID]["payload"] = {"name": "Learning Agent", "enabled": False}
    service = AgentConfigurationService(CONFIG_DIR, overlay=lambda: released)

    view = service.read(AGENT_ID)
    assert view is not None
    assert view.source == "RELEASE"
    assert view.document["payload"]["enabled"] is False

    summary = next(item for item in service.list_agents() if item.manifestId == AGENT_ID)
    assert summary.enabled is False
    assert summary.source == "RELEASE"


def test_an_agent_the_release_has_never_carried_reads_its_packaged_seed(
    service: AgentConfigurationService,
) -> None:
    view = service.read(AGENT_ID)
    assert view is not None
    assert view.source == "PACKAGED_BASELINE"


def test_the_release_domain_carries_every_agent_not_only_the_edited_one(
    service: AgentConfigurationService,
) -> None:
    """A release is an immutable document set; publishing one module would drop
    the other seven."""
    documents = service.released_documents()
    assert set(documents) == {summary.manifestId for summary in service.list_agents()}
    assert len(documents) >= 8


# --- the forbidden set still bites -------------------------------------------


@pytest.mark.asyncio
async def test_a_credentials_block_in_an_agent_document_is_refused(
    service: AgentConfigurationService,
) -> None:
    """Plan section 7's forbidden set is matched at every segment offset, so one
    level of nesting does not defeat it.

    `retry_policy` is the hole this closes: `AgentConfigNode` types it as a free
    mapping, so the schema validation above accepts anything inside it. Without
    the key policy, `retry_policy.credentials.token` would be a valid agent
    module document carrying a secret.
    """
    kernel, _, _ = build_test_kernel()
    document = _edited(service)
    document["payload"]["retry_policy"] = {"credentials": {"token": "shhh"}}
    service.validate_candidate(AGENT_ID, document)  # the schema permits it
    with pytest.raises(ForbiddenProposalKey):
        await service.propose(AGENT_ID, document, kernel=kernel, actor="operator", occurred_at=NOW)


# --- activation publishes a release ------------------------------------------


class _RecordingRuntimeActivator:
    """Stands in for `RuntimeConfigurationActivator` without a live process.

    The activator's own refresh is proven by `test_configuration_api.py`; what
    this suite has to show is that publishing an agent release *calls* it, since
    a release nothing activates is the redeploy problem again with more steps.
    """

    def __init__(self) -> None:
        self.refreshes = 0

    async def refresh(self, *, force: bool = False) -> None:
        del force
        self.refreshes += 1
        return None


@pytest.fixture
def repository(
    test_settings: Settings,
) -> InMemoryConfigurationGraphRepository:
    return InMemoryConfigurationGraphRepository()


async def _seed_active_release(
    repository: InMemoryConfigurationGraphRepository, settings: Settings
) -> None:
    domains = {
        RETURN_PLATFORM_DOMAIN_KEY: load_return_configuration(
            settings.return_configuration_path
        ).configuration.model_dump(mode="json"),
        AI_GATEWAY_DOMAIN_KEY: load_ai_gateway_configuration(
            settings.ai_gateway_configuration_path
        ).configuration.model_dump(mode="json"),
        DEPENDENCY_SIMULATION_DOMAIN_KEY: load_dependency_simulation_configuration(
            settings.dependency_simulation_configuration_path
        ).configuration.model_dump(mode="json"),
    }
    for key, payload in domains.items():
        await repository.save_draft_domain("baseline", key, payload, actor_id="seed")
    await repository.promote_release("baseline", "VALIDATED", actor_id="seed")
    await repository.promote_release(
        "baseline", "RELEASED", actor_id="seed", expected_head_revision=0
    )


@pytest.mark.asyncio
async def test_activating_an_agent_proposal_publishes_a_release(
    service: AgentConfigurationService,
    repository: InMemoryConfigurationGraphRepository,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def accept_receipts(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "return_platform.configuration.application.release_promotion"
        ".verify_runtime_validation_receipts",
        accept_receipts,
    )
    packaged_before = AGENT_PATH.read_bytes()
    await _seed_active_release(repository, test_settings)

    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    resources.mongo = cast(Any, object())
    runtime = _RecordingRuntimeActivator()
    activator = AgentConfigurationProposalActivator(
        agents=service,
        repository=repository,
        resources=resources,
        activator=cast(Any, runtime),
    )
    kernel, _, audit = build_test_kernel({ProposalType.CONFIGURATION: activator})

    document = _edited(service)
    proposal = await service.propose(
        AGENT_ID, document, kernel=kernel, actor="operator", occurred_at=NOW
    )
    await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=NOW)
    activated, receipt = await kernel.activate(
        proposal.proposal_id, actor="reviewer", occurred_at=NOW
    )

    assert activated.status is ProposalStatus.ACTIVATED
    published = await repository.get_release(receipt.reference)
    assert published is not None
    assert published.status == "RELEASED"

    modules = await repository.get_domain_config(receipt.reference, AGENT_MODULES_DOMAIN_KEY)
    assert modules is not None
    assert modules[AGENT_ID]["payload"]["enabled"] is False
    # Every other agent travelled with it.
    assert set(modules) == set(service.released_documents())
    # The three behaviour domains were carried forward, not dropped.
    assert RETURN_PLATFORM_DOMAIN_KEY in await repository.get_all_domain_configs(receipt.reference)
    assert runtime.refreshes >= 1
    assert "PROPOSAL_ACTIVATED" in audit.actions()
    assert AGENT_PATH.read_bytes() == packaged_before


@pytest.mark.asyncio
async def test_activation_without_an_active_release_is_refused(
    service: AgentConfigurationService,
    repository: InMemoryConfigurationGraphRepository,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> None:
    """A release carrying only AGENT_MODULES would fail its own validation and,
    if it did not, would take the return configuration down with it."""
    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    activator = AgentConfigurationProposalActivator(
        agents=service,
        repository=repository,
        resources=resources,
        activator=cast(Any, _RecordingRuntimeActivator()),
    )
    kernel, _, _ = build_test_kernel({ProposalType.CONFIGURATION: activator})
    proposal = await service.propose(
        AGENT_ID, _edited(service), kernel=kernel, actor="operator", occurred_at=NOW
    )
    await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=NOW)
    with pytest.raises(ActivationRefused):
        await kernel.activate(proposal.proposal_id, actor="reviewer", occurred_at=NOW)
