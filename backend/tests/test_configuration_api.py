"""API tests for versioned graph-backed runtime configuration."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.ai.routing.tasks import load_ai_gateway_configuration
from return_platform.configuration.api.releases import (
    record_configuration_audit as _real_record_audit,
)
from return_platform.configuration.api.router import router
from return_platform.configuration.graph_repository import (
    InMemoryConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.dependency_simulation.configuration import (
    load_dependency_simulation_configuration,
)
from return_platform.resources import RuntimeResources
from return_platform.security.principal import Principal


@pytest.fixture
def configuration_client(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = test_settings
    app.state.graph_configuration_repository = InMemoryConfigurationGraphRepository()
    app.state.return_configuration = load_return_configuration(
        test_settings.return_configuration_path
    )
    app.state.ai_gateway_configuration = load_ai_gateway_configuration(
        test_settings.ai_gateway_configuration_path
    )
    app.state.dependency_simulation_configuration = load_dependency_simulation_configuration(
        test_settings.dependency_simulation_configuration_path
    )
    activation_resources = RuntimeResources(
        settings=test_settings.model_copy(update={"vault_enabled": False}),
        catalog=loaded_empty_catalog,
    )
    app.state.runtime_configuration_activator = RuntimeConfigurationActivator(
        app_state=app.state,
        repository=app.state.graph_configuration_repository,
        baseline_path=test_settings.return_configuration_path,
        ai_gateway_baseline_path=test_settings.ai_gateway_configuration_path,
        dependency_simulation_baseline_path=(
            test_settings.dependency_simulation_configuration_path
        ),
        resources=activation_resources,
        refresh_interval_seconds=0,
    )
    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    resources.mongo = cast(Any, object())
    app.state.resources = resources

    @app.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            subject="configuration-admin",
            roles=frozenset({"console_admin"}),
        )
        request.state.correlation_id = "configuration-api-test"
        return await call_next(request)

    async def accept_receipts(*_args: object, **_kwargs: object) -> None:
        return None

    # The receipt check moved with the promotion body in W4.2: the router now
    # delegates to `promote_configuration_release`, so this patches where the
    # call actually lives rather than where it used to be imported.
    monkeypatch.setattr(
        "return_platform.configuration.application.release_promotion"
        ".verify_runtime_validation_receipts",
        accept_receipts,
    )

    # The fixture's Mongo is a bare object, so the audit writer is replaced by
    # one that records what would have been written; tests read it back from
    # `app.state.audit_records`.
    records: list[dict[str, Any]] = []
    app.state.audit_records = records

    async def record_audit(_request: Request, **entry: Any) -> None:
        records.append(dict(entry))

    monkeypatch.setattr(
        "return_platform.configuration.api.releases.record_configuration_audit",
        record_audit,
    )
    return TestClient(app)


def test_configuration_release_lifecycle_and_revision_conflict(
    configuration_client: TestClient,
) -> None:
    client = configuration_client

    # No release has been promoted yet, so the canonical runtime read has
    # nothing loaded in `app.state` -- CFG-1 retired the fallback-build path
    # (`get_active_snapshot`) that used to answer this from the baseline
    # directly; `ConfigurationSnapshotBuilder` itself stays covered by
    # `tests/test_graph_configuration.py`.
    initial = client.get("/api/config/runtime")
    assert initial.status_code == 503

    created = client.post(
        "/api/config/releases",
        json={"release_id": "release-api-v1", "from_active": True},
    )
    assert created.status_code == 201
    assert created.json()["data"]["status"] == "DRAFT"
    assert "RETURN_PLATFORM" in created.json()["data"]["domains"]

    validated = client.post(
        "/api/config/releases/release-api-v1/promote",
        json={"status": "VALIDATED"},
    )
    assert validated.status_code == 200

    # `save_domain_config` (the full-document PUT) was retired in CFG-1 -- no
    # consumer, and the PATCH path below is the write. Immutability-after-DRAFT
    # is the same `save_draft_domain` guard either way, so a no-op PATCH proves
    # the same thing a PUT used to.
    immutable_edit = client.patch(
        "/api/config/releases/release-api-v1/domains/RETURN_PLATFORM",
        json={"patch": {}},
    )
    assert immutable_edit.status_code == 409

    published = client.post(
        "/api/config/releases/release-api-v1/promote",
        json={"status": "RELEASED", "expected_head_revision": 0},
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["status"] == "RELEASED"
    assert published.json()["data"]["head_revision"] == 1
    assert published.json()["data"]["runtime_activation"]["release_id"] == "release-api-v1"

    active = client.get("/api/config/runtime")
    assert active.status_code == 200
    assert active.json()["data"]["release_id"] == "release-api-v1"
    assert active.json()["data"]["head_revision"] == 1

    second = client.post(
        "/api/config/releases",
        json={"release_id": "release-api-v2", "from_active": True},
    )
    assert second.status_code == 201
    assert (
        client.post(
            "/api/config/releases/release-api-v2/promote",
            json={"status": "VALIDATED"},
        ).status_code
        == 200
    )

    conflict = client.post(
        "/api/config/releases/release-api-v2/promote",
        json={"status": "RELEASED", "expected_head_revision": 0},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "CONFIGURATION_REVISION_CONFLICT"


def test_partial_agent_behavior_edit_activates_without_restart(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    created = client.post(
        "/api/config/releases",
        json={"release_id": "agent-behavior-v21", "from_active": True},
    )
    assert created.status_code == 201

    patched = client.patch(
        ("/api/config/releases/agent-behavior-v21/domains/RETURN_PLATFORM"),
        json={
            "patch": {
                "agents": {
                    "order_discovery": {
                        "version": "2.1",
                        "human_confirmation_required": True,
                    }
                }
            }
        },
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["payload"]["agents"]["order_discovery"]["version"] == "2.1"

    validated = client.post(
        "/api/config/releases/agent-behavior-v21/promote",
        json={"status": "VALIDATED"},
    )
    assert validated.status_code == 200
    published = client.post(
        "/api/config/releases/agent-behavior-v21/promote",
        json={"status": "RELEASED", "expected_head_revision": 0},
    )
    assert published.status_code == 200, published.text

    test_app = cast(FastAPI, client.app)
    active_loaded = test_app.state.return_configuration
    active_snapshot = test_app.state.return_configuration_snapshot
    assert active_loaded.configuration.agents["order_discovery"].version == "2.1"
    assert active_snapshot.release_id == "agent-behavior-v21"
    assert active_snapshot.head_revision == 1


def test_partial_edit_rejects_invalid_complete_configuration(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    assert (
        client.post(
            "/api/config/releases",
            json={"release_id": "invalid-agent-behavior", "from_active": True},
        ).status_code
        == 201
    )

    invalid = client.patch(
        ("/api/config/releases/invalid-agent-behavior/domains/RETURN_PLATFORM"),
        json={"patch": {"agents": {"order_discovery": None}}},
    )

    assert invalid.status_code == 422
    detail = client.get("/api/config/releases/invalid-agent-behavior")
    assert "order_discovery" in detail.json()["data"]["domains"]["RETURN_PLATFORM"]["agents"]


def test_ai_prompts_and_simulation_behavior_activate_from_graph(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    release_id = "all-behavior-domains-v1"
    created = client.post(
        "/api/config/releases",
        json={"release_id": release_id, "from_active": True},
    )
    assert created.status_code == 201
    assert {
        "RETURN_PLATFORM",
        "AI_GATEWAY",
        "DEPENDENCY_SIMULATION",
    } <= set(created.json()["data"]["domains"])

    prompt = (
        "Evaluate eligibility using only supplied operational facts and return the required "
        "structured decision for human review."
    )
    ai_patch = client.patch(
        f"/api/config/releases/{release_id}/domains/AI_GATEWAY",
        json={
            "patch": {
                "tasks": {
                    "RETURN_ELIGIBILITY_V1": {
                        "promptVersion": "return-eligibility-runtime-v3",
                        "systemPrompt": prompt,
                    }
                }
            }
        },
    )
    assert ai_patch.status_code == 200

    banner = "SIMULATION: graph-controlled dependency behavior is active."
    simulation_patch = client.patch(
        (f"/api/config/releases/{release_id}/domains/DEPENDENCY_SIMULATION"),
        json={"patch": {"modeBanner": banner}},
    )
    assert simulation_patch.status_code == 200

    assert (
        client.post(
            f"/api/config/releases/{release_id}/promote",
            json={"status": "VALIDATED"},
        ).status_code
        == 200
    )
    published = client.post(
        f"/api/config/releases/{release_id}/promote",
        json={"status": "RELEASED", "expected_head_revision": 0},
    )
    assert published.status_code == 200, published.text

    test_app = cast(FastAPI, client.app)
    active_ai = test_app.state.ai_gateway_configuration.configuration
    active_simulation = test_app.state.dependency_simulation_configuration.configuration
    assert active_ai.tasks["RETURN_ELIGIBILITY_V1"].promptVersion == (
        "return-eligibility-runtime-v3"
    )
    assert active_ai.tasks["RETURN_ELIGIBILITY_V1"].systemPrompt == prompt
    assert active_simulation.modeBanner == banner
    assert test_app.state.ai_gateway_route_pool is not None


def _create_draft(client: TestClient, release_id: str) -> None:
    created = client.post(
        "/api/config/releases",
        json={"release_id": release_id, "from_active": True},
    )
    assert created.status_code == 201, created.text


def test_a_patched_domain_is_stored_in_the_shape_the_bootstrap_compares(
    configuration_client: TestClient,
) -> None:
    """Finding F-0084: the bootstrap republished the release on every start.

    It decides "changed?" by comparing the stored payload with `model_dump` of
    the packaged file. A merge patch used to be stored as the raw merged dict,
    so a value the patch spelled differently from the dump -- here an integer
    where the model holds one, sent as a string the model coerces -- read as a
    change forever. What is stored now is the validated model's own dump.
    """
    client = configuration_client
    _create_draft(client, "canonical-shape")
    before = client.get("/api/config/releases/canonical-shape").json()
    stored_before = before["data"]["domains"]["RETURN_PLATFORM"]

    patched = client.patch(
        "/api/config/releases/canonical-shape/domains/RETURN_PLATFORM",
        json={"patch": {"policy_evaluation": {"enabled": "false"}}},
    )
    assert patched.status_code == 200, patched.text

    after = client.get("/api/config/releases/canonical-shape").json()
    stored_after = after["data"]["domains"]["RETURN_PLATFORM"]
    assert stored_after["policy_evaluation"]["enabled"] is False
    # Same keys, same shape: only the patched leaf differs from the pre-patch dump.
    assert set(stored_after) == set(stored_before)
    assert stored_after["policy_evaluation"].keys() == stored_before["policy_evaluation"].keys()
    # CFG-0 RV finding F10: a key-set match is not a shape match. A list stored
    # as the model's tuple dump, an enum stored by value rather than name, or a
    # default the merge patch never touched all pass the checks above while
    # still not being what `bootstrap_graph_configuration.py` compares against
    # its own `model_dump` of the packaged file. Assert the actual round trip.
    assert stored_after == ReturnPlatformConfiguration.model_validate(stored_after).model_dump(
        mode="json"
    )


def test_a_domain_the_platform_does_not_read_is_refused_not_stored(
    configuration_client: TestClient,
) -> None:
    """A full-document PUT used to store any domain key with no validation; the
    checksum then covered it and every clone carried it. CFG-1 retired that PUT
    (`save_domain_config`) -- it had no consumer, and the PATCH path is the
    write -- so this now goes through PATCH: nothing was ever stored under an
    unknown domain, so there is nothing to patch, and the refusal is the same
    404 either way."""
    client = configuration_client
    _create_draft(client, "unknown-domain")

    patched = client.patch(
        "/api/config/releases/unknown-domain/domains/NOT_A_DOMAIN",
        json={"patch": {"anything": True}},
    )
    assert patched.status_code == 404, patched.text

    release = client.get("/api/config/releases/unknown-domain").json()
    assert "NOT_A_DOMAIN" not in release["data"]["domains"]


def test_a_draft_cloned_from_the_active_release_carries_its_packaged_baseline(
    configuration_client: TestClient,
) -> None:
    """The baseline the bootstrap decides with must survive an operator's publish.

    Observed 2026-09-11: a task edit published from the AI Control Center came
    out as a RELEASED release with empty metadata, one start after the bootstrap
    had recorded a baseline on the release it cloned. Every later publish cloned
    the empty one, and the deployment was undecidable again for every key.
    """
    client = configuration_client
    _create_draft(client, "with-baseline")
    for status in ("VALIDATED", "RELEASED"):
        promoted = client.post(
            "/api/config/releases/with-baseline/promote",
            json={"status": status, "expected_head_revision": 0 if status == "RELEASED" else None},
        )
        assert promoted.status_code == 200, promoted.text
    repository = client.app.state.graph_configuration_repository
    baseline = {"packaged_key_digests": {"discovery": "d1", "support": "s1"}}
    asyncio.run(repository.set_release_metadata("with-baseline", baseline))

    _create_draft(client, "cloned")

    cloned = client.get("/api/config/releases/cloned").json()["data"]
    assert cloned["metadata"] == baseline


def test_an_audit_store_outage_does_not_undo_a_completed_write(
    configuration_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RV finding F3 on CFG-0: the audit write runs after the graph write, so a
    503 from it would have reported a promote that had already moved the head
    as failed, and the operator's retry would cut a second release."""
    from return_platform.configuration.api import releases as releases_module

    client = configuration_client
    _create_draft(client, "audit-outage")
    # Put the real writer back (the fixture stubs it) with an unreachable store.
    monkeypatch.setattr(releases_module, "record_configuration_audit", _real_record_audit)

    def unavailable(_request: Request) -> Any:
        raise RuntimeError("audit store unreachable")

    monkeypatch.setattr(releases_module, "resolve_operational_repository", unavailable)
    with caplog.at_level("ERROR"):
        promoted = client.post(
            "/api/config/releases/audit-outage/promote",
            json={"status": "VALIDATED"},
        )
    assert promoted.status_code == 200, promoted.text
    assert "configuration_audit_not_recorded" in caplog.text
    assert "CONFIGURATION_RELEASE_PROMOTED" in caplog.text


def test_every_release_change_leaves_an_audit_record(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    _create_draft(client, "audited")
    # The operator's real change of 2026-09-02 (decision D-0008): switching
    # policy evaluation on, which the model only allows with no disabled reason.
    patched = client.patch(
        "/api/config/releases/audited/domains/RETURN_PLATFORM",
        json={"patch": {"policy_evaluation": {"enabled": True, "disabled_reason": None}}},
    )
    assert patched.status_code == 200, patched.text
    promoted = client.post(
        "/api/config/releases/audited/promote",
        json={"status": "VALIDATED"},
    )
    assert promoted.status_code == 200, promoted.text

    records = client.app.state.audit_records
    actions = [record["action"] for record in records]
    assert actions == [
        "CONFIGURATION_RELEASE_CREATED",
        "CONFIGURATION_DOMAIN_PATCHED",
        "CONFIGURATION_RELEASE_PROMOTED",
    ]
    assert all(record["actor"] == "configuration-admin" for record in records)
    patch_record = records[1]
    assert patch_record["target"] == "audited/RETURN_PLATFORM"
    assert patch_record["details"]["patchKeys"] == ["policy_evaluation"]
    assert "policy_evaluation.enabled" in patch_record["details"]["changedPaths"]
    assert records[2]["details"]["status"] == "VALIDATED"
