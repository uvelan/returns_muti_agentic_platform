"""`GET /api/config/packaged/{domain_key}` and `POST /api/config/policy/preview` (CFG-8).

The Policy screen's two read-only backend routes: item B answers "what does the
packaged file say" for a domain (the "Reset to packaged default" action); item A
answers "what would this **draft** policy decide" against a fabricated sample,
never a real case. Neither route persists anything -- there is no release, no
patch and no promote anywhere in either handler -- which
`test_policy_preview_never_touches_the_active_release` below proves the same
way `test_validate_a_patch_against_the_active_release`
(`tests/test_configuration_api.py`) proves it for `/validate/{domain_key}`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.configuration.api.router import router
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import Settings
from return_platform.security.principal import Principal

_ROLES = frozenset({"console_admin"})


def _app(*, settings: Settings | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    if settings is not None:
        app.state.settings = settings
        app.state.packaged_deployment_defaults = {}

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=_ROLES)
        request.state.correlation_id = "policy-preview-test"
        return await call_next(request)

    return app


@pytest.fixture
def packaged_client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(_app(settings=test_settings)) as client:
        yield client


@pytest.fixture
def preview_client() -> Iterator[TestClient]:
    """No `app.state.settings` at all: `POST /policy/preview` reads nothing off
    the app -- the submitted blocks and the sample are its entire input -- so a
    test proving that needs the route reachable with no packaged file wired up."""
    with TestClient(_app()) as client:
        yield client


def _packaged_return_eligibility_policy() -> dict[str, Any]:
    """The real packaged `return_eligibility_policy`, as the wire shape a
    client submits -- not a hand-built toy, for the reason
    `test_packaged_adoption.py` gives: a toy dict fails validation before the
    assertion under test is reached."""
    loaded = load_return_configuration(Path("config/returns"))
    return loaded.configuration.return_eligibility_policy.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# GET /api/config/packaged/{domain_key}
# --------------------------------------------------------------------------- #


def test_get_packaged_domain_returns_the_packaged_return_platform_document(
    packaged_client: TestClient,
) -> None:
    response = packaged_client.get("/api/config/packaged/RETURN_PLATFORM")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["return_eligibility_policy"]["standard_stock_return"]["purchase_window"]["days"] == 30
    assert data["policy_evaluation"]["enabled"] is False


def test_get_packaged_domain_404s_naming_the_valid_keys(packaged_client: TestClient) -> None:
    response = packaged_client.get("/api/config/packaged/NOT_A_DOMAIN")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "RETURN_PLATFORM" in detail
    assert "AI_GATEWAY" in detail


def test_get_packaged_domain_serves_the_other_two_domains(packaged_client: TestClient) -> None:
    ai_gateway = packaged_client.get("/api/config/packaged/AI_GATEWAY")
    dependency_simulation = packaged_client.get("/api/config/packaged/DEPENDENCY_SIMULATION")

    assert ai_gateway.status_code == 200, ai_gateway.text
    assert dependency_simulation.status_code == 200, dependency_simulation.text


def test_get_packaged_domain_requires_read_roles() -> None:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.correlation_id = "policy-preview-test"
        return await call_next(request)

    with TestClient(app) as client:
        response = client.get("/api/config/packaged/RETURN_PLATFORM")

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# POST /api/config/policy/preview
# --------------------------------------------------------------------------- #


def test_preview_within_window_approves_with_the_restocking_fee_condition(
    preview_client: TestClient,
) -> None:
    """The brief's own example: the packaged policy at 10 days -> APPROVE with
    the restocking-fee condition. The packaged file's own
    `unstated_condition_facts: NOT_EVALUATED` means an all-"not stated" sample
    (the default `facts: {}`) never falls back to REVIEW_REQUIRED -- exactly
    the operator decision `return_policy.yaml` documents."""
    response = preview_client.post(
        "/api/config/policy/preview",
        json={
            "return_eligibility_policy": _packaged_return_eligibility_policy(),
            "policy_evaluation": {"enabled": True},
            "sample": {"days_since_purchase": 10, "stock_classification": "STANDARD_STOCK"},
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["evaluation_enabled"] is True
    assert data["decision"] == "APPROVE"
    assert "RESTOCKING_FEE_APPLIES" in data["conditions"]
    assert "CONDITION_FACTS_NOT_EVALUATED" in data["applied_rules"]
    assert set(data["unanswered_checks"]) == {
        "new",
        "suitable_for_resale",
        "original_packaging",
        "packaging_undamaged",
        "all_original_parts",
        "used",
        "installed",
        "modified",
        "rebuilt",
        "reconditioned",
        "repaired",
        "altered",
        "damaged",
    }


def test_preview_outside_window_reaches_the_outside_window_decision(
    preview_client: TestClient,
) -> None:
    """At 40 days (past the packaged 30-day window) the packaged file's own
    `outside_standard_window.decision` (REVIEW_REQUIRED) answers, not APPROVE
    and not REJECT."""
    response = preview_client.post(
        "/api/config/policy/preview",
        json={
            "return_eligibility_policy": _packaged_return_eligibility_policy(),
            "policy_evaluation": {"enabled": True},
            "sample": {"days_since_purchase": 40, "stock_classification": "STANDARD_STOCK"},
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["decision"] == "REVIEW_REQUIRED"
    assert "OUTSIDE_STANDARD_WINDOW" in data["applied_rules"]
    assert "OUTSIDE_STANDARD_RETURN_WINDOW" in data["reason_codes"]


def test_preview_a_stated_failing_condition_still_rejects_ahead_of_the_window(
    preview_client: TestClient,
) -> None:
    """A *stated* failure still rejects, and still ahead of the window --
    `unstated_condition_facts: NOT_EVALUATED` only weakens silence, never a
    fact somebody actually gave."""
    response = preview_client.post(
        "/api/config/policy/preview",
        json={
            "return_eligibility_policy": _packaged_return_eligibility_policy(),
            "policy_evaluation": {"enabled": True},
            "sample": {
                "days_since_purchase": 10,
                "stock_classification": "STANDARD_STOCK",
                "facts": {"installed": "TRUE"},
            },
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["decision"] == "REJECT"
    assert data["unanswered_checks"] == []


def test_preview_when_evaluation_is_disabled_returns_the_not_evaluated_outcome(
    preview_client: TestClient,
) -> None:
    """Off answers exactly what `evaluate_case_eligibility`
    (`workflows/return_case_activities.py`) records: `SKIPPED_BY_CONFIGURATION`,
    no route, no decision -- never `CONDITION_FACTS_NOT_EVALUATED`, which is a
    different mechanism entirely (an *enabled* gate over unstated facts)."""
    response = preview_client.post(
        "/api/config/policy/preview",
        json={
            "return_eligibility_policy": _packaged_return_eligibility_policy(),
            "policy_evaluation": {"enabled": False, "disabled_reason": "Suspended for a walkthrough."},
            "sample": {"days_since_purchase": 10},
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["evaluation_enabled"] is False
    assert data["decision"] is None
    assert data["route"] is None
    assert data["applied_rules"] == []
    assert data["policy_evaluation_state"] == "SKIPPED_BY_CONFIGURATION"
    assert data["policy_evaluation_skip_reason"] == "Suspended for a walkthrough."


def test_preview_disabled_with_no_stated_reason_422s(preview_client: TestClient) -> None:
    """`policy_evaluation.enabled=false` with no reason does not validate --
    `PolicyEvaluationConfiguration`'s own rule, "a gate disabled without a
    recorded reason cannot be audited" -- so this is refused exactly like
    `/validate/{domain_key}` would refuse the same draft, before any
    evaluation runs. `evaluate_policy_preview`'s `"UNSPECIFIED"` fallback
    guards a state the validated model itself cannot actually produce."""
    response = preview_client.post(
        "/api/config/policy/preview",
        json={
            "return_eligibility_policy": _packaged_return_eligibility_policy(),
            "policy_evaluation": {"enabled": False},
            "sample": {},
        },
    )

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert any(error["path"] == "policy_evaluation" for error in errors)


def test_preview_an_invalid_block_422s_with_field_paths(preview_client: TestClient) -> None:
    """A satisfied standard return configured to REJECT is the exact malformed
    release `StandardStockReturnConfiguration.validate_rule` refuses -- section
    20's own example of a release that must not activate."""
    policy = _packaged_return_eligibility_policy()
    policy["standard_stock_return"]["decision_when_satisfied"] = "REJECT"

    response = preview_client.post(
        "/api/config/policy/preview",
        json={
            "return_eligibility_policy": policy,
            "policy_evaluation": {"enabled": True},
            "sample": {},
        },
    )

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert any(error["path"].startswith("return_eligibility_policy") for error in errors)


def test_preview_an_unknown_checklist_fact_422s_naming_it(preview_client: TestClient) -> None:
    response = preview_client.post(
        "/api/config/policy/preview",
        json={
            "return_eligibility_policy": _packaged_return_eligibility_policy(),
            "policy_evaluation": {"enabled": True},
            "sample": {"facts": {"not_a_real_check": "TRUE"}},
        },
    )

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert any(error["path"] == "sample.facts.not_a_real_check" for error in errors)


def test_preview_requires_read_roles() -> None:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.correlation_id = "policy-preview-test"
        return await call_next(request)

    with TestClient(app) as client:
        response = client.post(
            "/api/config/policy/preview",
            json={
                "return_eligibility_policy": _packaged_return_eligibility_policy(),
                "policy_evaluation": {"enabled": True},
                "sample": {},
            },
        )

    assert response.status_code == 401


def test_policy_preview_never_touches_the_active_release(preview_client: TestClient) -> None:
    """No release, no graph, no case: the route has no `app.state` dependency
    that could persist anything, which this pins by calling it with none
    wired up at all and getting a normal answer back rather than a 500 from a
    missing repository/resources/graph attribute."""
    response = preview_client.post(
        "/api/config/policy/preview",
        json={
            "return_eligibility_policy": _packaged_return_eligibility_policy(),
            "policy_evaluation": {"enabled": True},
            "sample": {"days_since_purchase": 10},
        },
    )

    assert response.status_code == 200, response.text
