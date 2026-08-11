"""`/api/runtime-config` -- the shell's bootstrap payload.

The endpoint had no test at all while it served `/api/v1/runtime-config`, which
is part of why it stayed on a versioned path through three deletion waves: the
only thing describing it was a README note calling it a known leftover.

No datastore participates. The payload is settings plus two vocabulary lists, so
routing, the response shape, and -- the part worth guarding -- whether those
lists still agree with the definitions that actually enforce them, are the whole
surface.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import get_args

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.bootstrap.api import router
from return_platform.configuration.bootstrap_runtime_integrations import HOSTED_AI_PROVIDERS
from return_platform.configuration.runtime_validation import DataSourceValidateAndStageRequest


class _Settings:
    environment = "test"
    dynamic_order_agent_enabled = True


class _Snapshot:
    release_id = "release-under-test"


def _app(*, snapshot: object | None) -> FastAPI:
    app = FastAPI()
    app.state.settings = _Settings()
    app.state.return_configuration_snapshot = snapshot

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_app(snapshot=_Snapshot())) as test_client:
        yield test_client


def test_serves_the_canonical_versionless_path(client: TestClient) -> None:
    assert client.get("/api/runtime-config").status_code == 200


def test_the_advertised_base_path_is_the_one_the_shell_should_use(client: TestClient) -> None:
    """It advertised `/api/v1` while itself living there.

    Wave F made the versionless surface canonical, which turned this field into
    a wrong answer that nothing checked.
    """
    assert client.get("/api/runtime-config").json()["data"]["apiBasePath"] == "/api"


def test_reports_the_adopted_release(client: TestClient) -> None:
    assert client.get("/api/runtime-config").json()["data"]["releaseId"] == "release-under-test"


def test_release_is_unknown_rather_than_absent_before_a_snapshot_exists() -> None:
    """The shell fetches this during boot, which can precede configuration
    adoption. A missing key would be a client-side crash; "unknown" is a value
    the client can render."""
    with TestClient(_app(snapshot=None)) as client:
        assert client.get("/api/runtime-config").json()["data"]["releaseId"] == "unknown"


def test_source_types_match_what_the_request_model_will_actually_accept(
    client: TestClient,
) -> None:
    """The list was a hand-written literal in the endpoint, duplicating one in
    the model that validates submissions. A client is checked against the model
    and never against the advertisement, so drift between them is invisible
    until someone submits the type this endpoint offered."""
    advertised = client.get("/api/runtime-config").json()["data"]["capabilities"][
        "availableSourceTypes"
    ]
    accepted = get_args(DataSourceValidateAndStageRequest.model_fields["sourceType"].annotation)

    assert tuple(advertised) == accepted


def test_model_providers_match_the_set_bootstrap_validates(client: TestClient) -> None:
    advertised = client.get("/api/runtime-config").json()["data"]["capabilities"][
        "availableModelProviders"
    ]

    assert tuple(advertised) == HOSTED_AI_PROVIDERS


def test_order_discovery_flag_follows_the_setting_rather_than_being_hardcoded() -> None:
    """It was a literal `True`, alongside a second flag naming a feature Wave F
    deleted."""
    app = _app(snapshot=_Snapshot())
    app.state.settings.dynamic_order_agent_enabled = False

    with TestClient(app) as client:
        features = client.get("/api/runtime-config").json()["data"]["features"]

    assert features == {"orderDiscoveryCopilot": False}
