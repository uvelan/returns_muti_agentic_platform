"""API tests for versioned graph-backed runtime configuration."""

from __future__ import annotations

import asyncio
import contextlib
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
from return_platform.configuration.deployment_settings import deployment_payload_from_settings
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
    # CFG-6: mirrors `main.py`'s wiring, but deliberately `{}` here rather than
    # `deployment_payload_from_settings(test_settings)` -- most of this
    # fixture's tests build their "active release" by cloning
    # `app.state.return_configuration` (the RAW packaged baseline, exactly
    # like `_active_or_baseline_domains` does when there is no active release)
    # and would otherwise see an incidental `deployment.ai` divergence from
    # `test_settings`'s own env-derived model pools -- noise unrelated to
    # whatever carry-forward behaviour each test actually exercises.
    # `test_adopt_packaged_api_matches_a_cli_run` (the one test that needs the
    # real overlay, to compare against a real CLI run on the same settings)
    # sets this attribute itself before calling the route.
    app.state.packaged_deployment_defaults = {}
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


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        (("a", "b"), "a.b"),
        (("a", 2, "b"), "a[2].b"),
        ((0, "a"), "[0].a"),
        (("a", 1, 2), "a[1][2]"),
    ],
)
def test_dotted_error_path_maps_list_indices_as_brackets(
    loc: tuple[int | str, ...], expected: str
) -> None:
    """RV CFG-3a round 1 F7: the brief specifies list indices as `[n]`
    explicitly, and only the dotted-string case was exercised by the
    integration test below. `_dotted_error_path` is the function the brief's
    example (`return_policy.return_method_derivation.default_method`) and
    every validate response's `path` field go through."""
    from return_platform.configuration.api.releases import _dotted_error_path

    assert _dotted_error_path(loc) == expected


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
    # RV CFG-3a round 1 F2: the same five records the four-call path (create,
    # patch, promote x2) plus this call's own summary would leave -- not one.
    assert data["audit_ids"] and all(isinstance(i, str) for i in data["audit_ids"])
    assert len(data["audit_ids"]) == 5
    assert len(set(data["audit_ids"])) == 5  # every id distinct

    records = client.app.state.audit_records
    published_records = [
        r for r in records if r["target"] in ("published-v1", "published-v1/RETURN_PLATFORM")
    ]
    assert [r["action"] for r in published_records] == [
        "CONFIGURATION_RELEASE_CREATED",
        "CONFIGURATION_DOMAIN_PATCHED",
        "CONFIGURATION_RELEASE_PROMOTED",
        "CONFIGURATION_RELEASE_PROMOTED",
        "CONFIGURATION_RELEASE_PUBLISHED",
    ]
    patch_record = published_records[1]
    assert patch_record["details"]["patchKeys"] == ["policy_evaluation"]
    assert "policy_evaluation.enabled" in patch_record["details"]["changedPaths"]
    assert [r["details"]["status"] for r in published_records[2:4]] == ["VALIDATED", "RELEASED"]

    active = client.get("/api/config/runtime")
    assert active.status_code == 200
    assert active.json()["data"]["release_id"] == "published-v1"


def test_publish_leaves_a_per_step_audit_trail_queryable_by_target(
    configuration_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RV CFG-3a round 1 F2: `GET /api/config/audit?target=<release>` must
    actually list the per-step records `/publish` claims to write, not just
    the summary one. `configuration_client`'s fixture replaces
    `record_configuration_audit` with an in-memory recorder (there is no
    real Mongo in this suite); this test points the READ side at that same
    in-memory list, filtered by `target`, so the two sides of the claim are
    checked against a single source of truth rather than trusted separately.
    """
    import return_platform.configuration.api.router as router_module
    from return_platform.configuration.api.audit import AuditLog
    from return_platform.shared.contracts import APIResponse, ResponseMeta

    client = configuration_client
    published = client.post(
        "/api/config/publish",
        json={
            "release_id": "published-audited",
            "domain_key": "RETURN_PLATFORM",
            "patch": {"policy_evaluation": {"enabled": True, "disabled_reason": None}},
            "expected_head_revision": 0,
        },
    )
    assert published.status_code == 200, published.text
    expected_ids = set(published.json()["data"]["audit_ids"])

    records = client.app.state.audit_records

    async def fake_list_audit_logs(
        _request: Any, _user_id: Any, *, actions: Any = None, target: Any = None
    ) -> APIResponse[list[AuditLog]]:
        from datetime import UTC, datetime

        matched = [r for r in records if target is None or r["target"] == target]
        logs = [
            AuditLog(
                id=f"audit-{i}",
                action=r["action"],
                actor=r["actor"],
                target=r["target"],
                timestamp=datetime.now(UTC),
                details=r["details"],
            )
            for i, r in enumerate(matched)
        ]
        return APIResponse(data=logs, meta=ResponseMeta(request_id="test"))

    monkeypatch.setattr(router_module, "console_list_audit_logs", fake_list_audit_logs)

    listed = client.get("/api/config/audit", params={"target": "published-audited"})
    assert listed.status_code == 200, listed.text
    actions = [entry["action"] for entry in listed.json()["data"]]
    assert actions == [
        "CONFIGURATION_RELEASE_CREATED",
        "CONFIGURATION_RELEASE_PROMOTED",
        "CONFIGURATION_RELEASE_PROMOTED",
        "CONFIGURATION_RELEASE_PUBLISHED",
    ]

    listed_domain = client.get(
        "/api/config/audit", params={"target": "published-audited/RETURN_PLATFORM"}
    )
    assert listed_domain.status_code == 200, listed_domain.text
    assert [entry["action"] for entry in listed_domain.json()["data"]] == [
        "CONFIGURATION_DOMAIN_PATCHED"
    ]

    # Every record `/publish` wrote (one per id it returned) is reachable
    # through one of the two targets its own steps used -- none missing,
    # none extra.
    assert len(actions) + len(listed_domain.json()["data"]) == len(expected_ids)


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
    """RV CFG-3a round 1 F10: the existence check sits before the `try`, so
    a refused publish naming someone else's release id must not touch that
    release at all -- `_archive_draft_on_refusal` archiving a release this
    request did not create would be a new and worse bug than the one it
    exists to prevent."""
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

    taken = client.get("/api/config/releases/taken").json()["data"]
    assert taken["status"] == "DRAFT"


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


def test_publish_rolls_back_on_any_refusal_not_only_release_promotion_error(
    configuration_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RV CFG-3a F5 / CFG-6.design.md §8 (brief item 10).

    A non-`ReleasePromotionError`, non-`HTTPException` failure inside the
    guarded block -- here, a repository fault while cloning the SECOND domain
    of the new draft, well before either promotion -- must still archive the
    draft `/publish` already created the RETURN_PLATFORM domain for. Before
    this lease's `except BaseException` clause, only `HTTPException` was
    caught here and a raw exception from the repository propagated with the
    half-created DRAFT left behind.
    """
    client = configuration_client
    repo = client.app.state.graph_configuration_repository
    original_save = repo.save_draft_domain

    async def failing_save(
        release_id: str, domain_key: str, payload: dict[str, Any], *, actor_id: str
    ) -> None:
        if release_id == "publish-any-refusal" and domain_key == "AI_GATEWAY":
            raise RuntimeError("graph fault while cloning the active release")
        return await original_save(release_id, domain_key, payload, actor_id=actor_id)

    monkeypatch.setattr(repo, "save_draft_domain", failing_save)

    with pytest.raises(RuntimeError, match="graph fault while cloning the active release"):
        client.post(
            "/api/config/publish",
            json={
                "release_id": "publish-any-refusal",
                "domain_key": "RETURN_PLATFORM",
                "patch": {},
                "expected_head_revision": 0,
            },
        )

    releases = client.get("/api/config/releases").json()["data"]
    published = [r for r in releases if r["releaseId"] == "publish-any-refusal"]
    assert published, "the release this call created must still be visible, archived"
    assert published[0]["status"] == "ARCHIVED"


def test_adopt_packaged_rolls_back_on_any_refusal_not_only_release_promotion_error(
    configuration_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same widened guard, for `/adopt-packaged` (`releases.py:1032-1064`).

    The fault lands inside `publish_release_with_domains`'s own clone loop --
    before either promotion runs -- so the release this call creates is still
    DRAFT when the raw exception propagates, and must be archived rather than
    left behind.
    """
    client = configuration_client
    _release_with_an_edited_discovery(client, "adopt-any-refusal-base")

    repo = client.app.state.graph_configuration_repository
    original_save = repo.save_draft_domain

    async def failing_save(
        release_id: str, domain_key: str, payload: dict[str, Any], *, actor_id: str
    ) -> None:
        if release_id.startswith("adopt-packaged-") and domain_key == "AI_GATEWAY":
            raise RuntimeError("graph fault while cloning the active release")
        return await original_save(release_id, domain_key, payload, actor_id=actor_id)

    monkeypatch.setattr(repo, "save_draft_domain", failing_save)

    with pytest.raises(RuntimeError, match="graph fault while cloning the active release"):
        client.post(
            "/api/config/adopt-packaged",
            json={"units": ["discovery"], "expected_head_revision": 1},
        )

    releases = client.get("/api/config/releases").json()["data"]
    created = [r for r in releases if r["releaseId"].startswith("adopt-packaged-")]
    assert created, "the release adopt-packaged created must still be visible, archived"
    assert all(r["status"] == "ARCHIVED" for r in created)


def test_publish_rolls_back_when_the_failure_is_after_promote_to_validated(
    configuration_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RV round 1 A1: the other half of the `except BaseException` widening --
    a failure at the "VALIDATED" audit write, i.e. AFTER
    `promote_configuration_release(target_status="VALIDATED", ...)` already
    succeeded (`releases.py:877-890`) and BEFORE the RELEASED promotion ever
    runs. The head must stay untouched (nothing was ever promoted to
    RELEASED) and the VALIDATED node must be archived, not left behind.
    """
    client = configuration_client
    initial_head = client.get("/api/config/runtime")
    initial_head_revision = (
        initial_head.json()["data"]["head_revision"] if initial_head.status_code == 200 else None
    )

    async def failing_record_audit(_request: Request, **entry: Any) -> str:
        if (
            entry.get("action") == "CONFIGURATION_RELEASE_PROMOTED"
            and entry.get("details", {}).get("status") == "VALIDATED"
        ):
            raise RuntimeError("audit store unavailable after promote-to-VALIDATED")
        return "audit-ok"

    monkeypatch.setattr(
        "return_platform.configuration.api.releases.record_configuration_audit",
        failing_record_audit,
    )

    with pytest.raises(RuntimeError, match="audit store unavailable after promote-to-VALIDATED"):
        client.post(
            "/api/config/publish",
            json={
                "release_id": "publish-after-validated",
                "domain_key": "RETURN_PLATFORM",
                "patch": {},
                "expected_head_revision": 0,
            },
        )

    releases = client.get("/api/config/releases").json()["data"]
    published = [r for r in releases if r["releaseId"] == "publish-after-validated"]
    assert published, "the release this call created must still be visible, archived"
    assert published[0]["status"] == "ARCHIVED"

    after = client.get("/api/config/runtime")
    after_head_revision = (
        after.json()["data"]["head_revision"] if after.status_code == 200 else None
    )
    assert after_head_revision == initial_head_revision, "the head must be untouched"


def test_adopt_packaged_rolls_back_when_the_failure_is_after_promote_to_validated(
    configuration_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same case for `/adopt-packaged`. `publish_release_with_domains`
    (`release_promotion.py`) runs VALIDATED-then-RELEASED internally with no
    intervening metadata/audit write of its own to fault -- so the fault is
    injected into the shared `promote_configuration_release` primitive
    itself, raising only on the SECOND call (`target_status="RELEASED"`),
    which leaves the release genuinely sitting in VALIDATED when the
    exception reaches `adopt_packaged_release`'s guarded block.
    """
    client = configuration_client
    _release_with_an_edited_discovery(client, "adopt-after-validated-base")
    initial_head = client.get("/api/config/runtime").json()["data"]["head_revision"]

    import return_platform.configuration.application.release_promotion as release_promotion_module

    original_promote = release_promotion_module.promote_configuration_release
    calls: list[str] = []

    async def failing_promote(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("target_status", ""))
        if kwargs.get("target_status") == "RELEASED":
            raise RuntimeError("graph fault promoting VALIDATED to RELEASED")
        return await original_promote(*args, **kwargs)

    monkeypatch.setattr(release_promotion_module, "promote_configuration_release", failing_promote)

    with pytest.raises(RuntimeError, match="graph fault promoting VALIDATED to RELEASED"):
        client.post(
            "/api/config/adopt-packaged",
            json={"units": ["discovery"], "expected_head_revision": 1},
        )

    assert calls == ["VALIDATED", "RELEASED"], "the fault must land after VALIDATED succeeded"

    releases = client.get("/api/config/releases").json()["data"]
    created = [r for r in releases if r["releaseId"].startswith("adopt-packaged-")]
    assert created, "the release adopt-packaged created must still be visible, archived"
    assert all(r["status"] == "ARCHIVED" for r in created)

    after_head = client.get("/api/config/runtime").json()["data"]["head_revision"]
    assert after_head == initial_head, "the head must be untouched"


@pytest.mark.asyncio
async def test_archive_draft_on_refusal_shielded_survives_the_callers_own_cancellation() -> None:
    """RV round 1 A2, at the mechanism rather than through the ASGI stack --
    Starlette's `BaseHTTPMiddleware` (this fixture's `attach_principal`) runs
    the route inside its own `anyio` task group, and a real `CancelledError`
    raised inside the route there surfaces as `RuntimeError("No response
    returned.")` rather than the original exception, which would make an
    HTTP-level test assert Starlette's behaviour, not this handler's.

    `asyncio.CancelledError` is a `BaseException`, not an `Exception`;
    `_archive_draft_on_refusal`'s own `except Exception:` does not catch it,
    so an unshielded archive call could be cut off by a second delivery of
    the same cancellation, losing the archive along with the original
    refusal. This drives `_archive_draft_on_refusal_shielded` directly: start
    it as a task, cancel that task while the (slowed-down) archive is still
    in flight, and assert the release is ARCHIVED anyway -- the shielded
    inner task keeps running to completion regardless of the outer
    cancellation.
    """
    from return_platform.configuration.api.releases import _archive_draft_on_refusal_shielded

    repo = InMemoryConfigurationGraphRepository()
    await repo.save_draft_domain(
        "release-under-cancellation", "RETURN_PLATFORM", {}, actor_id="test"
    )
    original_promote = repo.promote_release

    async def slow_promote(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.05)
        return await original_promote(*args, **kwargs)

    repo.promote_release = slow_promote  # type: ignore[method-assign]

    task = asyncio.create_task(
        _archive_draft_on_refusal_shielded(repo, "release-under-cancellation", "test")
    )
    await asyncio.sleep(0.01)  # let it reach the slowed `promote_release` await
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    # `shield`'s inner task keeps running in the background once the OUTER
    # task (awaited above) is cancelled -- nothing here awaits it directly,
    # so give the event loop enough time to actually finish it before
    # asserting (the slowed `promote_release` still has ~40ms left at the
    # point of cancellation above).
    await asyncio.sleep(0.1)

    archived = await repo.get_release("release-under-cancellation")
    assert archived is not None
    assert archived.status == "ARCHIVED", (
        "the shielded archive must complete even though the calling task was cancelled"
    )


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


def test_packaged_drift_emits_no_warning_on_the_read_path(
    configuration_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """RV CFG-3a round 1 F4: a panel a browser polls must not turn
    `adopt_packaged_configuration`'s publish-time `packaged_configuration_
    not_adopted` warning into steady-state noise. The fixture in this test
    has an undecided key (`discovery`, no recorded baseline) -- exactly the
    condition that logs a WARNING on the write path -- so a clean caplog
    here is a real assertion, not a vacuous one."""
    client = configuration_client
    _release_with_an_edited_discovery(client, "drift-quiet")

    with caplog.at_level("WARNING"):
        response = client.get("/api/config/packaged-drift")

    assert response.status_code == 200, response.text
    assert "discovery" in response.json()["data"]["RETURN_PLATFORM"]["undecided"]
    assert "packaged_configuration_not_adopted" not in caplog.text


def test_packaged_drift_with_no_active_release_shows_nothing_undecided_or_adopted(
    configuration_client: TestClient,
) -> None:
    """With nothing published yet there is nothing to disagree with -- no
    key is undecided -- and no merge has run to say anything was actually
    taken from the file yet either (RV CFG-3a round 1 F1: `would_adopt` is
    read off the merge result now, not guessed from "not undecided", and
    there is no merge at all when there is no active release to merge
    against). A first publish still carries the whole packaged file; this
    panel just has nothing decided to report before one exists."""
    client = configuration_client
    response = client.get("/api/config/packaged-drift")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["RETURN_PLATFORM"]["undecided"] == []
    assert data["RETURN_PLATFORM"]["would_adopt"] == []


def test_packaged_domain_payloads_seeds_deployment_from_the_bootstrap_snapshot_not_app_state_settings(
    configuration_client: TestClient,
) -> None:
    """RV round 1 A3, at the real call site (`_packaged_domain_payloads`,
    `configuration/api/releases.py`) `GET /api/config/packaged-drift` and
    `POST /api/config/adopt-packaged` both go through.

    `app.state.settings` is set here to a value standing in for "already
    release-derived" (a totally different provider order than either the
    packaged file or the bootstrap snapshot), and
    `app.state.packaged_deployment_defaults` to a THIRD, distinct value
    standing in for the real bootstrap snapshot. If `_packaged_domain_payloads`
    ever read `app.state.settings` for this instead of
    `packaged_deployment_defaults`, the published `deployment.ai.provider_order`
    below would be `ANTHROPIC` (or the packaged file's own default); it must
    be `GOOGLE,NVIDIA` -- `packaged_deployment_defaults`'s value -- instead.
    """
    client = configuration_client
    _release_with_an_edited_discovery(client, "circularity-guard-base")

    # Stands in for "a release has already adopted and app.state.settings is
    # now release-derived" -- must NOT influence the packaged side of the
    # merge below.
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"ai_provider_order": "ANTHROPIC"}
    )
    # Stands in for the real bootstrap snapshot main.py takes BEFORE a
    # release ever touches `app.state.settings` (see that module's own
    # comment on `app.state.packaged_deployment_defaults`).
    client.app.state.packaged_deployment_defaults = {"ai": {"provider_order": ["GOOGLE", "NVIDIA"]}}

    response = client.post(
        "/api/config/adopt-packaged",
        json={"units": ["deployment.ai"], "expected_head_revision": 1},
    )
    assert response.status_code == 200, response.text

    published_order = response.json()["data"]["domains"]["RETURN_PLATFORM"]["deployment"]["ai"][
        "provider_order"
    ]
    assert published_order == ["GOOGLE", "NVIDIA"], (
        "the packaged side of the merge must come from packaged_deployment_defaults "
        "(the bootstrap snapshot), never from app.state.settings"
    )


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
    # The one test in this file that needs the real env overlay -- both sides
    # must seed `deployment` from the SAME `test_settings` for the comparison
    # below to mean anything (`configuration_client`'s own default is `{}`;
    # see that fixture's docstring).
    client.app.state.packaged_deployment_defaults = deployment_payload_from_settings(test_settings)
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
