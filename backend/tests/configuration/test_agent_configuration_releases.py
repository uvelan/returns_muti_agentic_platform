"""CFG-5b: an agent configuration edit is a release patch, not a file write.

W4.2 made an agent edit a governed release rather than a rewrite of a packaged
YAML file. CFG-5b repointed the whole thing at the live `RETURN_PLATFORM.
agents` section -- the one thing `AgentRegistry.build()` and every agent class
actually read -- and retired the manifest-driven module system nothing else
consumed. The properties that matter are unchanged from W4.2: an edit does not
touch anything until it is approved, it survives a redeploy because it lives
in a release, every replica sees it because every replica reads that release,
and the change is in the audit trail. Each is asserted directly rather than
inferred from a status code.

**Why there is no longer a "does not write the packaged file" test naming a
file.** There is no per-agent packaged file any more -- `agents` is one key of
`backend/config/returns/` (composed into `RETURN_PLATFORM`), the same as
`discovery` or `support`, with no independent existence a write could corrupt.
The invariant that matters now is the same one CFG-5b's own docstring names in
`configuration/api/agents.py`: `PUT` validates and files a proposal, and
nothing the service reads changes until that proposal is activated. See
`test_an_edit_does_not_change_what_a_read_returns` below, and
`test_activating_an_agent_proposal_publishes_a_release`'s own check that the
packaged `returns/agents.yaml` file is untouched by activation either.
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
from return_platform.configuration.settings import (
    BACKEND_ROOT,
    DEFAULT_RETURN_CONFIGURATION_PATH,
    Settings,
)
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.dependency_simulation.configuration import (
    load_dependency_simulation_configuration,
)
from return_platform.platform.governance.errors import ActivationRefused
from return_platform.platform.governance.proposal import ProposalStatus, ProposalType
from return_platform.resources import RuntimeResources
from return_platform.security.principal import Principal
from tests.governance_doubles import build_test_kernel

# The live agents section (`backend/config/returns/agents.yaml`) is keyed by
# the same names `AgentRegistry.build()` and every agent class read off
# `ReturnPlatformConfiguration.agents["<id>"]` -- no `agent.` prefix, and not
# the manifest system's own id vocabulary (`bay_allocation`, `learning`, ...),
# which CFG-5b retired because nothing at runtime read it.
AGENT_ID = "order_discovery"
AGENTS_PATH = BACKEND_ROOT / "config" / "returns" / "agents.yaml"
NOW = datetime(2026, 8, 13, 11, 0, tzinfo=UTC)


def _return_platform_document() -> dict[str, Any]:
    """The packaged `RETURN_PLATFORM` document, freshly loaded every call.

    Freshly loaded (not cached) so a fixture built from this function behaves
    the way `main.py`'s own closure does: a value read again on every access,
    never a snapshot captured once. Used directly as `service`'s `active`
    callable below, standing in for "the active release matches the packaged
    baseline" -- the same simplification `test_activating_an_agent_proposal_
    publishes_a_release`'s seeded repository makes.
    """
    return load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration.model_dump(
        mode="json"
    )


@pytest.fixture
def service() -> AgentConfigurationService:
    return AgentConfigurationService(active=_return_platform_document)


def _edited(service: AgentConfigurationService) -> dict[str, Any]:
    current = service.read(AGENT_ID)
    assert current is not None
    document = copy.deepcopy(current.document)
    document["enabled"] = False
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


def test_an_edit_does_not_change_what_a_read_returns(
    client: TestClient, service: AgentConfigurationService
) -> None:
    """`PUT` validates and files a proposal; it must change nothing a read
    answers with until that proposal is activated (see the module docstring
    for why this is no longer phrased as "does not write a packaged file")."""
    before = service.read(AGENT_ID)
    response = client.put(f"/api/agents/{AGENT_ID}", json={"document": _edited(service)})
    assert response.status_code == 202, response.text
    after = service.read(AGENT_ID)
    assert after == before


def test_an_edit_becomes_a_proposal_awaiting_review(
    client: TestClient, service: AgentConfigurationService
) -> None:
    response = client.put(f"/api/agents/{AGENT_ID}", json={"document": _edited(service)})
    data = response.json()["data"]
    assert data["status"] == ProposalStatus.REVIEW_PENDING
    # No more `agent.payload.enabled`: the document is the agent's document
    # directly now, with no envelope to nest it under.
    assert data["affectedKeys"] == ["agent.enabled"]
    assert data["proposedBy"] == "configuration-admin"

    stored = client.app.state.governance_store.proposals[data["proposalId"]]  # type: ignore[attr-defined]
    assert stored.proposal_type is ProposalType.CONFIGURATION
    assert stored.subject_id == AGENT_ID
    # Validate-by-model survived the change of sink, and says what it validated.
    assert stored.validation_receipt is not None
    assert stored.validation_receipt.startswith("agent-configuration:")


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
    document["not_a_field"] = True
    response = client.put(f"/api/agents/{AGENT_ID}", json={"document": document})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "AGENT_CONFIGURATION_REJECTED"
    assert "not_a_field" in detail["message"]
    assert client.app.state.governance_store.proposals == {}  # type: ignore[attr-defined]


def test_a_document_missing_a_required_field_is_refused_here(
    client: TestClient, service: AgentConfigurationService
) -> None:
    """`AgentConfiguration` is the single definition of a valid agent document
    now (no more manifest loader to re-implement or disagree with it)."""
    document = _edited(service)
    del document["name"]
    response = client.put(f"/api/agents/{AGENT_ID}", json={"document": document})
    assert response.status_code == 422
    assert "name" in response.json()["detail"]["message"]


def test_an_unknown_agent_is_a_404(client: TestClient) -> None:
    response = client.put("/api/agents/not_a_real_agent", json={"document": {}})
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


def test_a_read_returns_the_active_release_document() -> None:
    """The source is always `"RELEASE"` now: `agents` is a required key of
    `ReturnPlatformConfiguration`, so there is no state with no release value
    to fall back to (see the class docstring on `AgentConfigurationService`)."""
    released = {
        AGENT_ID: {
            "name": "Order Discovery Agent",
            "version": "2.0",
            "enabled": False,
            "ai_assisted": True,
        }
    }
    service = AgentConfigurationService(active=lambda: {"agents": released})

    view = service.read(AGENT_ID)
    assert view is not None
    assert view.source == "RELEASE"
    assert view.document["enabled"] is False
    assert view.path == f"RETURN_PLATFORM.agents.{AGENT_ID}"

    summary = next(item for item in service.list_agents() if item.manifestId == AGENT_ID)
    assert summary.enabled is False
    assert summary.source == "RELEASE"


def test_an_agent_absent_from_the_release_is_not_found(
    service: AgentConfigurationService,
) -> None:
    assert service.read("not_a_real_agent") is None


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
    packaged_before = AGENTS_PATH.read_bytes()
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

    return_platform = await repository.get_domain_config(
        receipt.reference, RETURN_PLATFORM_DOMAIN_KEY
    )
    assert return_platform is not None
    assert return_platform["agents"][AGENT_ID]["enabled"] is False
    # Every other agent, and every other RETURN_PLATFORM key, travelled with it.
    assert set(return_platform["agents"]) == set(_return_platform_document()["agents"])
    assert return_platform["discovery"] == _return_platform_document()["discovery"]
    # The other two behaviour domains were carried forward, not dropped.
    all_domains = await repository.get_all_domain_configs(receipt.reference)
    assert AI_GATEWAY_DOMAIN_KEY in all_domains
    assert DEPENDENCY_SIMULATION_DOMAIN_KEY in all_domains
    assert runtime.refreshes >= 1
    assert "PROPOSAL_ACTIVATED" in audit.actions()
    assert AGENTS_PATH.read_bytes() == packaged_before


@pytest.mark.asyncio
async def test_a_concurrent_release_survives_agent_activation(
    repository: InMemoryConfigurationGraphRepository,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RV round 1, F1 (BLOCKING): the activator must clone the repository's
    active release, never `AgentConfigurationService.active()` -- a closure
    over this process's own runtime snapshot, refreshed behind a debounce
    window and kept indefinitely stale on a refresh failure
    (`main.py`'s own `runtime_configuration_refresh_failed_using_last_good_
    snapshot`). The other tests in this module cannot fail on this: their
    `service` fixture and the seeded repository both read the same packaged
    file, so the two sources always agree and a bug that clones the wrong one
    is invisible. Here they deliberately disagree -- `service.active()` is
    pinned to the *old* document, while the repository's active release has
    already moved on to a concurrently-published one (`support.
    external_mirror_enabled` flipped, the way RV's own probe demonstrated) --
    and the assertion is that the concurrent value survives activation
    rather than being silently reverted to what the stale snapshot held.
    """

    async def accept_receipts(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "return_platform.configuration.application.release_promotion"
        ".verify_runtime_validation_receipts",
        accept_receipts,
    )
    await _seed_active_release(repository, test_settings)
    stale_snapshot = await repository.get_domain_config("baseline", RETURN_PLATFORM_DOMAIN_KEY)
    assert stale_snapshot is not None

    # A release published by someone else after this process's own snapshot
    # was taken. Every other domain carries forward untouched.
    concurrent_value = not stale_snapshot["support"]["external_mirror_enabled"]
    concurrent_return_platform = {
        **stale_snapshot,
        "support": {**stale_snapshot["support"], "external_mirror_enabled": concurrent_value},
    }
    for domain_key, payload in (
        (RETURN_PLATFORM_DOMAIN_KEY, concurrent_return_platform),
        (
            AI_GATEWAY_DOMAIN_KEY,
            await repository.get_domain_config("baseline", AI_GATEWAY_DOMAIN_KEY),
        ),
        (
            DEPENDENCY_SIMULATION_DOMAIN_KEY,
            await repository.get_domain_config("baseline", DEPENDENCY_SIMULATION_DOMAIN_KEY),
        ),
    ):
        await repository.save_draft_domain(
            "concurrent-release", domain_key, payload, actor_id="other-operator"
        )
    await repository.promote_release("concurrent-release", "VALIDATED", actor_id="other-operator")
    await repository.promote_release(
        "concurrent-release",
        "RELEASED",
        actor_id="other-operator",
        expected_head_revision=await repository.get_head_revision(),
    )
    active_release = await repository.get_active_release()
    assert active_release is not None
    assert active_release.release_id == "concurrent-release"

    # This process's own service is still pinned to the pre-concurrent-release
    # document -- the stale runtime snapshot the debounce window (or a failed
    # refresh) would leave it holding.
    service = AgentConfigurationService(active=lambda: stale_snapshot)
    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    resources.mongo = cast(Any, object())
    runtime = _RecordingRuntimeActivator()
    activator = AgentConfigurationProposalActivator(
        agents=service,
        repository=repository,
        resources=resources,
        activator=cast(Any, runtime),
    )
    kernel, _, _ = build_test_kernel({ProposalType.CONFIGURATION: activator})

    document = _edited(service)
    proposal = await service.propose(
        AGENT_ID, document, kernel=kernel, actor="operator", occurred_at=NOW
    )
    await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=NOW)
    _activated, receipt = await kernel.activate(
        proposal.proposal_id, actor="reviewer", occurred_at=NOW
    )

    published = await repository.get_domain_config(receipt.reference, RETURN_PLATFORM_DOMAIN_KEY)
    assert published is not None
    # The agent edit this activation was for landed...
    assert published["agents"][AGENT_ID]["enabled"] is False
    # ...and the concurrent release's own value survived, rather than being
    # reverted to what this process's stale snapshot held.
    assert published["support"]["external_mirror_enabled"] is concurrent_value


@pytest.mark.asyncio
async def test_activation_without_an_active_release_is_refused(
    service: AgentConfigurationService,
    repository: InMemoryConfigurationGraphRepository,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> None:
    """`publish_release_with_domains` refuses to cut a release with nothing to
    clone from -- there is no longer a "release carries the wrong domain"
    distinction to test, since the target and the dependency are now the same
    domain (`RETURN_PLATFORM`): either there is an active release to clone, or
    there is not."""
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
