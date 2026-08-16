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
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_validation import DataSourceValidateAndStageRequest
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH

#: `None` is a meaningful argument here -- "a process with no configuration
#: loaded" -- so it cannot double as "the caller said nothing".
_UNSET = object()


class _Settings:
    environment = "test"
    dynamic_order_agent_enabled = True


class _Snapshot:
    release_id = "release-under-test"


def _loaded_configuration(*, agent_id: str | None) -> LoadedReturnConfiguration:
    """The shipped configuration with one field moved.

    Built from the real file rather than a stub because the endpoint's job is to
    republish a value out of the running configuration, and a stub would let the
    two disagree about where that value lives.
    """
    shipped = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH)
    return shipped.model_copy(
        update={
            "configuration": shipped.configuration.model_copy(
                update={
                    "copilot": shipped.configuration.copilot.model_copy(
                        update={"order_discovery_agent_id": agent_id}
                    )
                }
            )
        }
    )


def _app(*, snapshot: object | None, configuration: object | None = _UNSET) -> FastAPI:
    app = FastAPI()
    app.state.settings = _Settings()
    app.state.return_configuration_snapshot = snapshot
    app.state.return_configuration = (
        _loaded_configuration(agent_id="order-discovery-agent")
        if configuration is _UNSET
        else configuration
    )

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


def test_the_copilot_agent_id_comes_from_the_running_configuration(
    client: TestClient,
) -> None:
    """The shell had this compiled in as `"order_discovery"` while the active
    schema keys the policy `order-discovery-agent`, so every turn 422'd. Serving
    it is the fix; serving the *configured* value rather than a second literal
    here is what stops the same drift reappearing on this side."""
    agents = client.get("/api/runtime-config").json()["data"]["agents"]

    assert agents == {"orderDiscovery": "order-discovery-agent"}


def test_an_unconfigured_copilot_agent_is_null_rather_than_a_guess() -> None:
    """A deployment that has not stated the mapping gets `null`, which the shell
    fails closed on. Any default here would be the literal all over again --
    reachable exactly when nobody has said what the right answer is."""
    app = _app(snapshot=_Snapshot(), configuration=_loaded_configuration(agent_id=None))

    with TestClient(app) as client:
        assert client.get("/api/runtime-config").json()["data"]["agents"] == {
            "orderDiscovery": None
        }


def test_no_loaded_configuration_still_serves_a_readable_payload() -> None:
    """This endpoint answers before authentication and can be reached before
    configuration adoption. A 500 here would take the whole shell down over a
    field only one screen reads."""
    app = _app(snapshot=None, configuration=None)

    with TestClient(app) as client:
        response = client.get("/api/runtime-config")

    assert response.status_code == 200
    assert response.json()["data"]["agents"] == {"orderDiscovery": None}


def test_the_selection_vocabulary_is_the_release_the_writer_enforces(
    client: TestClient,
) -> None:
    """The picker and the validator must be reading one catalogue.

    `POST /api/cases/{id}/selected-items` refuses a term the active release does
    not publish with `422 SELECTION_TERM_NOT_PUBLISHED`, so a console offering
    its own list would offer terms the write rejects -- and the associate would
    learn that only after choosing one. Compared against the loaded
    configuration rather than against a list retyped here, for exactly the
    reason the source-type test gives.
    """
    shipped = _loaded_configuration(agent_id="order-discovery-agent").configuration
    published = client.get("/api/runtime-config").json()["data"]["selectionVocabulary"]

    assert tuple(published["reasons"]) == shipped.selection_vocabulary.reasons
    assert tuple(published["conditions"]) == shipped.selection_vocabulary.conditions
    assert published["reasons"], "the shipped release publishes a reason catalogue"


def test_an_unconfigured_process_publishes_no_catalogue_rather_than_a_default() -> None:
    """Empty is the honest answer, and it is not the same as "reject everything".

    A release cut before `selection_vocabulary` existed refuses nothing, which
    leaves reason and condition as length-bounded free text. A default list here
    would advertise terms `_reject_unpublished_terms` has never heard of, and
    the client would build a picker out of them.
    """
    app = _app(snapshot=None, configuration=None)

    with TestClient(app) as client:
        assert client.get("/api/runtime-config").json()["data"]["selectionVocabulary"] == {
            "reasons": [],
            "conditions": [],
        }


def test_the_fact_ranking_is_the_clarification_policy_by_descending_priority(
    client: TestClient,
) -> None:
    """The console's facts panel had this order written into a TypeScript array.

    It listed the return reason near the top while `clarification_policy` ranks
    it last of eighteen, and no release could correct it. Compared against the
    loaded configuration rather than a sequence retyped here, for the reason the
    source-type test gives.
    """
    shipped = _loaded_configuration(agent_id="order-discovery-agent").configuration
    expected = [
        item.field
        for item in sorted(
            shipped.clarification_policy.fields, key=lambda item: (-item.priority, item.field)
        )
    ]

    published = client.get("/api/runtime-config").json()["data"]["factCatalogue"]

    assert published["orderedFields"] == expected
    # The two ends the operator actually complained about.
    assert published["orderedFields"][0] == "order_number"
    assert published["orderedFields"][-1] == "return_reason"


def test_the_fact_ranking_covers_every_name_a_turn_can_capture(client: TestClient) -> None:
    """`build_fact_catalogue` reads the same list, and a turn's captured facts
    can carry no name it does not define. So the ranking is total over the panel
    it orders: there is no configured fact the client has to guess a place for.
    """
    shipped = _loaded_configuration(agent_id="order-discovery-agent").configuration
    published = client.get("/api/runtime-config").json()["data"]["factCatalogue"]

    assert set(published["orderedFields"]) == {
        item.field for item in shipped.clarification_policy.fields
    }


def test_an_unconfigured_process_ranks_nothing_rather_than_shipping_a_default() -> None:
    """A default ranking here would be the client's hardcoded array moved one
    tier down -- still a list nobody configured, just further from the screen.
    Empty tells the client it has not been given an order, which it handles with
    a fallback it can defend."""
    app = _app(snapshot=None, configuration=None)

    with TestClient(app) as client:
        assert client.get("/api/runtime-config").json()["data"]["factCatalogue"] == {
            "orderedFields": []
        }
