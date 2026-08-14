"""CFG-03: creating a return reads the operator's vocabulary, not a regex.

`ReturnCreateRequest.shippingPathExpectation` carried a fourteen-value
`pattern=` while `return_policy.normalized_return_methods` already declared the
vocabulary in configuration. The consequence was not cosmetic: an operator could
add a return method through the Control Centre, watch the release activate, and
still be told 422 by the API -- with the running configuration saying the value
was fine.

The tests below enter through the real routes with a real
`LoadedReturnConfiguration` on app state, because the claim is specifically
about *where the value is resolved from on the request path*. A test that called
the resolver directly would pass whether or not either route used it, which is
the realistic way this regresses -- there are two create endpoints and only one
of them has to be forgotten.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import canonical_returns
from return_platform.api import returns as legacy_returns
from return_platform.api.canonical_returns import router as canonical_router
from return_platform.api.returns import router as legacy_router
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    load_return_configuration,
)
from return_platform.operations.models import ReturnSessionView
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_LOADED = load_return_configuration(Path("config/returns/production.yaml"))
_CONFIGURED_METHODS = _LOADED.configuration.return_policy.normalized_return_methods
#: A method the shipped configuration does not declare, standing in for one an
#: operator adds tomorrow. Asserted absent so this test cannot quietly become a
#: tautology if the catalogue grows to include it.
_NEW_METHOD = "OFFSITE_FREIGHT"

_VALID = {
    "customerReference": "cust-1",
    "orderReference": "order-1",
    "itemReferences": ["item-1"],
    "reasonCode": "DAMAGED",
}


class _Repository:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def create_return(
        self, payload: Any, *, correlation_id: str, actor_id: str
    ) -> ReturnSessionView:
        del actor_id
        self.created.append(payload)
        now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        return ReturnSessionView(
            id="ret-new",
            correlationId=correlation_id,
            customerReference=payload.customerReference,
            orderReference=payload.orderReference,
            itemReferences=payload.itemReferences,
            productReferences=payload.productReferences,
            reasonCode=payload.reasonCode,
            returnQuantity=payload.returnQuantity,
            packageCount=payload.packageCount,
            shippingPathExpectation=payload.shippingPathExpectation,
            assumptionSetVersion=payload.assumptionSetVersion,
            channel=payload.channel,
            status="QUEUED",
            currentStage="INTAKE",
            progressPercentage=0,
            version=0,
            createdAt=now,
            updatedAt=now,
        )


def _configuration_with(*methods: str) -> LoadedReturnConfiguration:
    """The shipped configuration with one field replaced.

    Built by copying the real snapshot rather than by assembling a fixture, so
    the thing under test is a policy the loader accepts.
    """
    policy = _LOADED.configuration.return_policy.model_copy(
        update={"normalized_return_methods": methods}
    )
    configuration = _LOADED.configuration.model_copy(update={"return_policy": policy})
    return _LOADED.model_copy(update={"configuration": configuration})


def _client(
    module: Any,
    router: Any,
    repository: _Repository,
    *,
    configuration: LoadedReturnConfiguration | None = _LOADED,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="operator", roles=frozenset({r.CONSOLE_ADMIN}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    monkeypatch.setattr(module, "resolve_operational_repository", lambda request: repository)
    app.include_router(router)
    if configuration is not None:
        app.state.return_configuration = configuration
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


#: Both create routes, run through every case. There are two, they are meant to
#: enforce the same rule, and "the canonical one was updated and the legacy one
#: was not" is exactly the shape of the defect this is fixing.
_ROUTES = [
    pytest.param(canonical_returns, canonical_router, "/api/returns", id="canonical"),
    pytest.param(legacy_returns, legacy_router, "/api/v1/returns", id="legacy"),
]


def test_the_new_method_is_genuinely_not_in_the_shipped_catalogue() -> None:
    assert _NEW_METHOD not in _CONFIGURED_METHODS


@pytest.mark.parametrize(("module", "router", "path"), _ROUTES)
def test_a_configured_method_is_accepted(
    module: Any, router: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _Repository()
    for client in _client(module, router, repository, monkeypatch=monkeypatch):
        response = client.post(path, json={**_VALID, "shippingPathExpectation": "BRANCH_LTL"})

    assert response.status_code == 201, response.text
    assert repository.created[0].shippingPathExpectation == "BRANCH_LTL"


@pytest.mark.parametrize(("module", "router", "path"), _ROUTES)
def test_a_method_the_operator_adds_is_accepted_without_a_release_of_this_code(
    module: Any, router: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect, stated as a passing test.

    Nothing in Python changes between this and the case above -- only the active
    snapshot -- and that is the whole property CFG-03 is about.
    """
    repository = _Repository()
    configuration = _configuration_with(*_CONFIGURED_METHODS, _NEW_METHOD)
    for client in _client(
        module, router, repository, configuration=configuration, monkeypatch=monkeypatch
    ):
        response = client.post(path, json={**_VALID, "shippingPathExpectation": _NEW_METHOD})

    assert response.status_code == 201, response.text
    assert repository.created[0].shippingPathExpectation == _NEW_METHOD


@pytest.mark.parametrize(("module", "router", "path"), _ROUTES)
def test_a_method_the_operator_removes_is_refused(
    module: Any, router: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both directions. A check that could only widen would keep accepting a
    method the operator has withdrawn, which is the same drift facing the other
    way."""
    repository = _Repository()
    for client in _client(
        module,
        router,
        repository,
        configuration=_configuration_with("PREPAID_PARCEL"),
        monkeypatch=monkeypatch,
    ):
        response = client.post(path, json={**_VALID, "shippingPathExpectation": "BRANCH_LTL"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "UNCONFIGURED_RETURN_METHOD"
    assert repository.created == [], "a refused return was still created"


@pytest.mark.parametrize(("module", "router", "path"), _ROUTES)
def test_an_unknown_method_names_what_is_allowed(
    module: Any, router: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From the running configuration, so the message cannot be stale."""
    repository = _Repository()
    for client in _client(module, router, repository, monkeypatch=monkeypatch):
        response = client.post(path, json={**_VALID, "shippingPathExpectation": "CARRIER_PIGEON"})

    assert response.status_code == 422, response.text
    assert "BRANCH_LTL" in response.json()["detail"]["message"]


@pytest.mark.parametrize(("module", "router", "path"), _ROUTES)
def test_the_assumption_set_is_stamped_from_the_running_release(
    module: Any, router: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not from a literal written into the contract model.

    The stamped value is persisted onto the session, so a hardcoded default
    recorded the assumption set that was true when that line of Python was
    written rather than the one the process is serving.
    """
    repository = _Repository()
    for client in _client(module, router, repository, monkeypatch=monkeypatch):
        response = client.post(path, json=_VALID)

    assert response.status_code == 201, response.text
    assert (
        repository.created[0].assumptionSetVersion == _LOADED.configuration.assumption_set_version
    )


@pytest.mark.parametrize(("module", "router", "path"), _ROUTES)
def test_a_caller_supplied_assumption_set_is_left_alone(
    module: Any, router: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stamping is for callers who did not say. One that did is recording a
    deliberate claim, and overwriting it would lose it."""
    repository = _Repository()
    for client in _client(module, router, repository, monkeypatch=monkeypatch):
        client.post(path, json={**_VALID, "assumptionSetVersion": "MIGRATION-BACKFILL-7"})

    assert repository.created[0].assumptionSetVersion == "MIGRATION-BACKFILL-7"


@pytest.mark.parametrize(("module", "router", "path"), _ROUTES)
def test_a_process_with_no_configuration_answers_503_not_422(
    module: Any, router: Any, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It cannot say whether the method is valid, so it must not say it is not.

    A 422 here would tell a caller their perfectly good request was malformed
    because this process had not loaded a snapshot.
    """
    repository = _Repository()
    for client in _client(module, router, repository, configuration=None, monkeypatch=monkeypatch):
        response = client.post(path, json=_VALID)

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "RETURN_CONFIGURATION_UNAVAILABLE"


def test_the_return_method_vocabulary_is_no_longer_a_field_pattern() -> None:
    """The realistic regression: someone reinstates the regex "for the contract".

    It would refuse every method added after the day it was written, which is
    what CFG-03 removed, and the generated client would advertise the stale set
    as authoritative.
    """
    from return_platform.operations.models import ReturnCreateRequest

    field = ReturnCreateRequest.model_fields["shippingPathExpectation"]
    patterns = [item for item in field.metadata if getattr(item, "pattern", None) is not None]
    assert patterns == [], f"shippingPathExpectation is pinned by a pattern again: {patterns}"
