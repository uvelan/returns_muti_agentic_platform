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
from return_platform.configuration.cli import bootstrap_graph_configuration
from return_platform.configuration.graph_repository import (
    InMemoryConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
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

    async def record_audit(_request: Request, **entry: Any) -> str:
        records.append(dict(entry))
        return f"audit-{len(records)}"

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


def test_patch_refuses_a_stale_expected_version(
    configuration_client: TestClient,
) -> None:
    """The optimistic lock: two open tabs editing the same draft, or a PATCH
    built from a read the release has already moved past. `expected_version`
    is optional, so a caller that never reads it back keeps working exactly
    as before this field existed."""
    client = configuration_client
    _create_draft(client, "versioned")

    stale = client.patch(
        "/api/config/releases/versioned/domains/RETURN_PLATFORM",
        json={
            "patch": {"policy_evaluation": {"enabled": True, "disabled_reason": None}},
            "expected_version": 999,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "CONFIGURATION_DOMAIN_VERSION_CONFLICT"
    assert stale.json()["detail"]["current_version"] == 1

    current = client.patch(
        "/api/config/releases/versioned/domains/RETURN_PLATFORM",
        json={
            "patch": {"policy_evaluation": {"enabled": True, "disabled_reason": None}},
            "expected_version": 1,
        },
    )
    assert current.status_code == 200, current.text

    # The version this domain is now at, so a second stale write against the
    # version BEFORE the one above is also refused -- the lock advances.
    now_stale = client.patch(
        "/api/config/releases/versioned/domains/RETURN_PLATFORM",
        json={
            "patch": {"policy_evaluation": {"enabled": False, "disabled_reason": "paused"}},
            "expected_version": 1,
        },
    )
    assert now_stale.status_code == 409, now_stale.text
    assert now_stale.json()["detail"]["current_version"] == 2

    no_lock = client.patch(
        "/api/config/releases/versioned/domains/RETURN_PLATFORM",
        json={"patch": {"policy_evaluation": {"enabled": False, "disabled_reason": "paused"}}},
    )
    assert no_lock.status_code == 200, no_lock.text


def test_validate_a_standalone_payload_reports_path_mapped_errors(
    configuration_client: TestClient,
) -> None:
    """No draft, no write, no release_id -- `payload` validates standalone."""
    client = configuration_client
    result = client.post("/api/config/validate/RETURN_PLATFORM", json={"payload": {}})
    assert result.status_code == 200, result.text
    body = result.json()["data"]
    assert body["valid"] is False
    assert body["errors"]
    assert all({"path", "message", "type"} <= set(error) for error in body["errors"])
    # `schema_version` is a required top-level field with no default: an
    # empty payload must name it, dotted the way the brief specifies.
    assert any(error["path"] == "schema_version" for error in body["errors"])


def test_validate_a_standalone_valid_payload_reports_no_errors(
    configuration_client: TestClient,
    test_settings: Settings,
) -> None:
    client = configuration_client
    valid_payload = load_return_configuration(
        test_settings.return_configuration_path
    ).configuration.model_dump(mode="json")

    result = client.post("/api/config/validate/RETURN_PLATFORM", json={"payload": valid_payload})

    assert result.status_code == 200, result.text
    assert result.json()["data"] == {"valid": True, "errors": []}


def test_validate_an_unknown_domain_is_404(configuration_client: TestClient) -> None:
    client = configuration_client
    result = client.post("/api/config/validate/NOT_A_DOMAIN", json={"payload": {}})
    assert result.status_code == 404, result.text


def test_validate_refuses_a_body_with_neither_or_both_shapes(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    neither = client.post("/api/config/validate/RETURN_PLATFORM", json={})
    assert neither.status_code == 422, neither.text
    both = client.post(
        "/api/config/validate/RETURN_PLATFORM",
        json={"payload": {}, "patch": {}},
    )
    assert both.status_code == 422, both.text


def test_validate_a_patch_needs_an_active_release(configuration_client: TestClient) -> None:
    """No release_id on this route: a patch validates against the ACTIVE
    release, and there is none yet in a fresh test app."""
    client = configuration_client
    result = client.post("/api/config/validate/RETURN_PLATFORM", json={"patch": {}})
    assert result.status_code == 409, result.text


def test_validate_a_patch_against_the_active_release(configuration_client: TestClient) -> None:
    client = configuration_client
    _create_draft(client, "validate-base")
    assert (
        client.post(
            "/api/config/releases/validate-base/promote",
            json={"status": "VALIDATED"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/config/releases/validate-base/promote",
            json={"status": "RELEASED", "expected_head_revision": 0},
        ).status_code
        == 200
    )

    valid = client.post(
        "/api/config/validate/RETURN_PLATFORM",
        json={"patch": {"policy_evaluation": {"enabled": True, "disabled_reason": None}}},
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["data"] == {"valid": True, "errors": []}

    # The packaged baseline ships `policy_evaluation.enabled: false` with a
    # non-null `disabled_reason` (the dev-host suspension notice). Each
    # `validate` call patches that SAME unwritten baseline -- validate never
    # writes -- so `enabled: true` alone, with `disabled_reason` left at the
    # packaged non-null string, is what `PolicyEvaluationConfiguration`'s own
    # model-level validator refuses.
    invalid = client.post(
        "/api/config/validate/RETURN_PLATFORM",
        json={"patch": {"policy_evaluation": {"enabled": True}}},
    )
    assert invalid.status_code == 200, invalid.text
    body = invalid.json()["data"]
    assert body["valid"] is False
    assert any("policy_evaluation" in error["path"] for error in body["errors"])
    # Nothing was written by either call: the release's stored payload is
    # still the packaged baseline.
    release = client.get("/api/config/releases/validate-base").json()["data"]
    assert release["domains"]["RETURN_PLATFORM"]["policy_evaluation"]["enabled"] is False


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


# --- publish (single transaction) ---------------------------------------------


def test_publish_creates_patches_and_releases_in_one_call(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    response = client.post(
        "/api/config/publish",
        json={
            "release_id": "published-v1",
            "domain_key": "RETURN_PLATFORM",
            "patch": {"policy_evaluation": {"enabled": True, "disabled_reason": None}},
            "expected_head_revision": 0,
            "note": "turning eligibility evaluation on",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["release_id"] == "published-v1"
    assert data["status"] == "RELEASED"
    assert data["head_revision"] == 1
    assert data["domains"]["RETURN_PLATFORM"]["policy_evaluation"]["enabled"] is True
    assert data["audit_ids"] and all(isinstance(i, str) for i in data["audit_ids"])

    active = client.get("/api/config/runtime")
    assert active.status_code == 200
    assert active.json()["data"]["release_id"] == "published-v1"


def test_publish_generates_a_release_id_when_none_is_given(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    response = client.post(
        "/api/config/publish",
        json={
            "domain_key": "RETURN_PLATFORM",
            "patch": {},
            "expected_head_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["release_id"].startswith("publish-")


def test_publish_refuses_an_existing_release_id(configuration_client: TestClient) -> None:
    client = configuration_client
    _create_draft(client, "taken")
    response = client.post(
        "/api/config/publish",
        json={
            "release_id": "taken",
            "domain_key": "RETURN_PLATFORM",
            "patch": {},
            "expected_head_revision": 0,
        },
    )
    assert response.status_code == 409, response.text


def test_publish_refuses_an_invalid_patch_and_leaves_nothing_behind(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    response = client.post(
        "/api/config/publish",
        json={
            "release_id": "publish-invalid",
            "domain_key": "RETURN_PLATFORM",
            "patch": {"agents": {"order_discovery": None}},
            "expected_head_revision": 0,
        },
    )
    assert response.status_code == 422, response.text

    releases = client.get("/api/config/releases").json()["data"]
    statuses = {release["status"] for release in releases}
    assert statuses <= {"ARCHIVED"}, statuses


def test_publish_refuses_a_stale_head_and_leaves_nothing_behind(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    response = client.post(
        "/api/config/publish",
        json={
            "release_id": "publish-stale",
            "domain_key": "RETURN_PLATFORM",
            "patch": {},
            "expected_head_revision": 5,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CONFIGURATION_REVISION_CONFLICT"

    releases = client.get("/api/config/releases").json()["data"]
    statuses = {release["status"] for release in releases}
    assert statuses <= {"ARCHIVED"}, statuses


def test_publish_needs_the_release_write_capability() -> None:
    from return_platform.security import roles as r
    from return_platform.security.capabilities import CONFIG_RELEASE_WRITE, capabilities_for_roles

    app = FastAPI()
    app.include_router(router)
    unentitled = next(
        role
        for role in sorted(r.ALL_ROLES)
        if CONFIG_RELEASE_WRITE not in capabilities_for_roles(frozenset({role}))
    )

    @app.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(subject="reader", roles=frozenset({unentitled}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    client = TestClient(app)
    response = client.post(
        "/api/config/publish",
        json={"domain_key": "RETURN_PLATFORM", "patch": {}, "expected_head_revision": 0},
    )
    assert response.status_code == 403, response.text


# --- packaged adoption --------------------------------------------------------


def _release_with_an_edited_discovery(client: TestClient, release_id: str) -> None:
    """A release identical to the packaged file except `discovery`, edited away
    from it -- the same divergence `test_graph_configuration_bootstrap.py`
    uses, so `discovery` is what `adopt-packaged` has something to adopt."""
    _create_draft(client, release_id)
    patched = client.patch(
        f"/api/config/releases/{release_id}/domains/RETURN_PLATFORM",
        json={"patch": {"discovery": {"identification_fields": []}}},
    )
    assert patched.status_code == 200, patched.text
    assert (
        client.post(
            f"/api/config/releases/{release_id}/promote",
            json={"status": "VALIDATED"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/config/releases/{release_id}/promote",
            json={"status": "RELEASED", "expected_head_revision": 0},
        ).status_code
        == 200
    )


def test_adopt_packaged_needs_an_active_release(configuration_client: TestClient) -> None:
    client = configuration_client
    response = client.post(
        "/api/config/adopt-packaged",
        json={"units": ["discovery"], "expected_head_revision": 0},
    )
    assert response.status_code == 409, response.text


def test_adopt_packaged_refuses_an_unknown_unit(configuration_client: TestClient) -> None:
    client = configuration_client
    _release_with_an_edited_discovery(client, "adopt-base-unknown")
    response = client.post(
        "/api/config/adopt-packaged",
        json={"units": ["no_such_key"], "expected_head_revision": 1},
    )
    assert response.status_code == 422, response.text


def test_adopt_packaged_publishes_the_requested_unit(
    configuration_client: TestClient, test_settings: Settings
) -> None:
    client = configuration_client
    _release_with_an_edited_discovery(client, "adopt-base")

    response = client.post(
        "/api/config/adopt-packaged",
        json={"units": ["discovery"], "expected_head_revision": 1},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "RELEASED"
    assert data["head_revision"] == 2
    packaged_discovery = load_return_configuration(
        test_settings.return_configuration_path
    ).configuration.discovery.model_dump(mode="json")
    assert data["domains"]["RETURN_PLATFORM"]["discovery"] == packaged_discovery
    # Nothing else was left undecided: the only divergence from packaged was
    # the one key requested, and every other key already matched.
    assert data["undecided"] == {
        "RETURN_PLATFORM": [],
        "AI_GATEWAY": [],
        "DEPENDENCY_SIMULATION": [],
    }

    records = client.app.state.audit_records
    assert records[-1]["action"] == "CONFIGURATION_PACKAGED_ADOPTED"
    assert records[-1]["details"]["units"] == ["discovery"]


def test_adopt_packaged_refuses_a_stale_head_and_leaves_nothing_behind(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    _release_with_an_edited_discovery(client, "adopt-stale-base")

    response = client.post(
        "/api/config/adopt-packaged",
        json={"units": ["discovery"], "expected_head_revision": 0},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CONFIGURATION_REVISION_CONFLICT"

    releases = client.get("/api/config/releases").json()["data"]
    # The base release is RELEASED; anything this refused call created is
    # archived, not left DRAFT or VALIDATED for a retry to trip over.
    statuses = {release["status"] for release in releases}
    assert statuses <= {"RELEASED", "ARCHIVED"}, statuses


def test_adopt_packaged_needs_the_release_write_capability() -> None:
    from return_platform.security import roles as r
    from return_platform.security.capabilities import CONFIG_RELEASE_WRITE, capabilities_for_roles

    app = FastAPI()
    app.include_router(router)
    unentitled = next(
        role
        for role in sorted(r.ALL_ROLES)
        if CONFIG_RELEASE_WRITE not in capabilities_for_roles(frozenset({role}))
    )

    @app.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(subject="reader", roles=frozenset({unentitled}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    client = TestClient(app)
    response = client.post(
        "/api/config/adopt-packaged",
        json={"units": [], "expected_head_revision": 0},
    )
    assert response.status_code == 403, response.text


def test_packaged_drift_reports_undecided_would_adopt_and_filled_leaves(
    configuration_client: TestClient,
) -> None:
    client = configuration_client
    _release_with_an_edited_discovery(client, "drift-base")

    response = client.get("/api/config/packaged-drift")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert {"RETURN_PLATFORM", "AI_GATEWAY", "DEPENDENCY_SIMULATION"} <= set(data)
    for domain in data.values():
        assert {"undecided", "would_adopt", "filled_leaves"} <= set(domain)
    # No baseline was ever recorded for this release, so the one key that
    # disagrees with the packaged file (discovery) is undecided, not silently
    # adopted.
    assert "discovery" in data["RETURN_PLATFORM"]["undecided"]
    assert "discovery" not in data["RETURN_PLATFORM"]["would_adopt"]


def test_packaged_drift_with_no_active_release_shows_everything_adoptable(
    configuration_client: TestClient,
) -> None:
    """With nothing published yet there is nothing to disagree with, so every
    packaged key is something a first publish would carry -- not undecided."""
    client = configuration_client
    response = client.get("/api/config/packaged-drift")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["RETURN_PLATFORM"]["undecided"] == []
    assert "discovery" in data["RETURN_PLATFORM"]["would_adopt"]


@pytest.mark.asyncio
async def test_adopt_packaged_api_matches_a_cli_run(
    configuration_client: TestClient,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance: adopt-packaged producing the same release a CLI run would.

    Same starting state (an active release whose `discovery` diverges from
    the packaged file with no recorded baseline) fed to
    `bootstrap_graph_configuration.main(adopt_packaged_keys=("discovery",))`
    against one in-memory repository and to `POST /adopt-packaged` against a
    second, freshly seeded copy of the same state. Both call
    `adopt_packaged_configuration`; this proves the two callers actually
    agree on its output, not just that both compile against it.
    """
    packaged = load_return_configuration(test_settings.return_configuration_path).configuration
    packaged_payload = packaged.model_dump(mode="json")
    older_payload = {
        **packaged_payload,
        "discovery": {**packaged_payload["discovery"], "identification_fields": []},
    }
    ai_gateway_payload = load_ai_gateway_configuration(
        test_settings.ai_gateway_configuration_path
    ).configuration.model_dump(mode="json")
    dependency_simulation_payload = load_dependency_simulation_configuration(
        test_settings.dependency_simulation_configuration_path
    ).configuration.model_dump(mode="json")

    async def _seed(repo: InMemoryConfigurationGraphRepository) -> None:
        for domain_key, payload in (
            (RETURN_PLATFORM_DOMAIN_KEY, older_payload),
            (AI_GATEWAY_DOMAIN_KEY, ai_gateway_payload),
            (DEPENDENCY_SIMULATION_DOMAIN_KEY, dependency_simulation_payload),
        ):
            await repo.save_draft_domain("base-release", domain_key, payload, actor_id="seed")
        await repo.promote_release("base-release", "VALIDATED", actor_id="seed")
        await repo.promote_release(
            "base-release", "RELEASED", actor_id="seed", expected_head_revision=0
        )

    # --- CLI side: main() against its own fresh in-memory repository ---
    cli_repo = InMemoryConfigurationGraphRepository()
    await _seed(cli_repo)

    class _Driver:
        async def verify_connectivity(self) -> None:
            return None

        async def close(self) -> None:
            return None

    async def resolve_settings(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return test_settings, object()

    monkeypatch.setattr(
        bootstrap_graph_configuration, "resolve_runtime_settings_from_vault", resolve_settings
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration.AsyncGraphDatabase,
        "driver",
        lambda *_args, **_kwargs: _Driver(),
    )
    monkeypatch.setattr(
        bootstrap_graph_configuration, "Neo4jConfigurationGraphRepository", lambda _driver: cli_repo
    )

    await bootstrap_graph_configuration.main(adopt_packaged_keys=("discovery",))

    cli_active = await cli_repo.get_active_release()
    assert cli_active is not None
    cli_domains = await cli_repo.get_all_domain_configs(cli_active.release_id)

    # --- API side: the identical starting state, through the route ---
    client = configuration_client
    api_repo = client.app.state.graph_configuration_repository
    await _seed(api_repo)

    response = client.post(
        "/api/config/adopt-packaged",
        json={"units": ["discovery"], "expected_head_revision": 1},
    )
    assert response.status_code == 200, response.text
    api_release_id = response.json()["data"]["release_id"]
    api_domains = await api_repo.get_all_domain_configs(api_release_id)

    assert api_domains[RETURN_PLATFORM_DOMAIN_KEY] == cli_domains[RETURN_PLATFORM_DOMAIN_KEY]
    assert api_domains[AI_GATEWAY_DOMAIN_KEY] == cli_domains[AI_GATEWAY_DOMAIN_KEY]
    assert (
        api_domains[DEPENDENCY_SIMULATION_DOMAIN_KEY]
        == cli_domains[DEPENDENCY_SIMULATION_DOMAIN_KEY]
    )
