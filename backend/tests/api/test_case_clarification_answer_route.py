"""V3: the clarification answer endpoint, over real HTTP (contracts.md sect. 9).

The distinctions that matter to a caller, and that a careless implementation
collapses:

    202  recorded            -- a command on file and a delivery row queued
    202  duplicate           -- the same answer twice is one command
    409  answered otherwise  -- the first answer stands
    422  `map` with no record -- a loose artifact never creates a record
    404  someone else's case -- never 403, which would confirm it exists

and the one thing the response must *not* claim: that anything was relayed. The
relay happens in an activity after the signal lands.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import case_clarifications as module
from return_platform.api.case_clarifications import router
from return_platform.configuration.settings import Settings
from return_platform.operations.case_commands import (
    DurableCaseCommandStore,
    ensure_case_command_indexes,
)
from return_platform.operations.integrations.outbox import (
    ensure_integration_outbox_indexes,
)
from return_platform.security import roles as r
from return_platform.security.principal import Principal
from tests.operations.mongo_double import FakeClient

TENANT = "tenant-a"
CASE_ID = "case-4242"
CLARIFICATION_ID = "clar-1"
ACTOR = "associate-7"


SUPPORT_EVENT_ID = "evt-9"
QUESTION = "Which return is this tracking number for?"


def _clarification_fact(clarification_id: str = CLARIFICATION_ID) -> dict[str, Any]:
    """The `support_clarification_requested` fact the endpoint reads back.

    The endpoint takes the support event and the question from **this**, never
    from the request body: an answer whose caller chose which question it was
    answering is an answer to whatever the caller preferred.
    """
    return {
        "factId": f"support_clarification_requested-{clarification_id}",
        "factName": "support_clarification_requested",
        "value": {
            "clarificationId": clarification_id,
            "supportEventId": SUPPORT_EVENT_ID,
            "verbatimQuestion": QUESTION,
        },
    }


class _StubRepository:
    def __init__(
        self, case: dict[str, Any] | None, facts: list[dict[str, Any]] | None = None
    ) -> None:
        self._case = case
        self._facts = [_clarification_fact()] if facts is None else facts

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        del case_id
        return self._case

    async def list_case_facts(self, case_id: str) -> list[dict[str, Any]]:
        del case_id
        return list(self._facts)


#: Distinguishes "the test did not override the case" from "the case is absent".
#: `case=None` cannot mean the second, because it already means the first.
_MISSING: Any = object()


def _case_document(tenant_id: str = TENANT) -> dict[str, Any]:
    return {"caseId": CASE_ID, "tenantId": tenant_id, "status": "AWAITING_SUPPORT"}


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest_asyncio.fixture
async def store(mongo: FakeClient, test_settings: Settings) -> DurableCaseCommandStore:
    built = DurableCaseCommandStore(cast(Any, mongo), test_settings)
    await ensure_case_command_indexes(mongo[test_settings.mongo_database])
    await ensure_integration_outbox_indexes(mongo[test_settings.mongo_database])
    return built


def _client(
    monkeypatch: pytest.MonkeyPatch,
    store: DurableCaseCommandStore,
    *,
    case: dict[str, Any] | None = None,
    roles: frozenset[str] = frozenset({r.RETURN_SUPPORT}),
    tenant_id: str = TENANT,
    facts: list[dict[str, Any]] | None = None,
) -> Iterator[TestClient]:
    monkeypatch.setattr(
        module,
        "_repository",
        lambda request: _StubRepository(
            _case_document() if case is None else (None if case is _MISSING else case),
            facts,
        ),
    )
    monkeypatch.setattr(module, "_command_store", lambda request: store)

    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject=ACTOR, roles=roles)
        request.state.tenant_id = tenant_id
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        yield client


def _post(client: TestClient, **overrides: Any) -> Any:
    payload: dict[str, Any] = {"answerText": "It belongs to RMA-4471."}
    payload.update(overrides)
    return client.post(
        f"/api/v1/cases/{CASE_ID}/clarifications/{CLARIFICATION_ID}/answer", json=payload
    )


def test_an_answer_is_recorded_and_queued_for_delivery(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    for client in _client(monkeypatch, store):
        response = _post(client)
        assert response.status_code == 202, response.text
        body = response.json()["data"]
        assert body["caseId"] == CASE_ID
        assert body["clarificationId"] == CLARIFICATION_ID
        assert body["signalId"] == f"clarification-answered:{CASE_ID}:{CLARIFICATION_ID}"
        assert body["outboxCommandId"]
        assert body["duplicate"] is False
        # The response says what committed and nothing about the relay.
        assert set(body) == {
            "caseId",
            "clarificationId",
            "commandId",
            "signalId",
            "outboxCommandId",
            "duplicate",
        }
        return
    raise AssertionError("the client fixture yielded nothing")


def test_the_same_answer_twice_is_one_command(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """A double-submitted form must not signal the workflow twice. The signal
    id is derived from the clarification, so the second call recognises the
    first rather than trusting the client to resend an id."""
    for client in _client(monkeypatch, store):
        first = _post(client).json()["data"]
        second = _post(client)
        assert second.status_code == 202
        body = second.json()["data"]
        assert body["duplicate"] is True
        assert body["commandId"] == first["commandId"]
        assert body["outboxCommandId"] == first["outboxCommandId"]
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_different_answer_to_the_same_clarification_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """409, not a silent overwrite. The first answer has already been relayed to
    Support; a second one replacing it would leave the case and the thread
    saying different things."""
    for client in _client(monkeypatch, store):
        assert _post(client).status_code == 202
        conflicting = _post(client, answerText="Actually, RMA-9999.")
        assert conflicting.status_code == 409
        assert conflicting.json()["detail"]["code"] == "CLARIFICATION_ALREADY_ANSWERED"
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_map_answer_without_a_record_is_refused(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """Sect. 4: a loose artifact never creates a record. "Map this to nothing"
    is not a decision the associate can have meant, and resolving it later is
    exactly the create-from-loose-artifact behaviour the contract forbids."""
    for client in _client(monkeypatch, store):
        response = _post(client, resolutionChoice="map")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "CLARIFICATION_MAP_WITHOUT_RECORD"
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_map_answer_naming_a_record_is_accepted(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """The other half of the rule above -- without it, an endpoint that refused
    every `map` would pass the refusal test."""
    for client in _client(monkeypatch, store):
        response = _post(client, resolutionChoice="map", returnRecordId="rec-9")
        assert response.status_code == 202, response.text
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_reject_answer_needs_no_record(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    for client in _client(monkeypatch, store):
        assert _post(client, resolutionChoice="reject").status_code == 202
        return
    raise AssertionError("the client fixture yielded nothing")


def test_an_unknown_resolution_choice_is_refused_by_the_schema(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """A closed set on the request model, so an unrecognised choice never
    reaches a branch that has to decide what it means."""
    for client in _client(monkeypatch, store):
        assert _post(client, resolutionChoice="maybe").status_code == 422
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_body_field_the_endpoint_does_not_know_is_refused(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """`extra="forbid"`. In particular, a body must not be able to name the
    actor: the actor comes from the capability check."""
    for client in _client(monkeypatch, store):
        assert _post(client, actorId="somebody-else").status_code == 422
        return
    raise AssertionError("the client fixture yielded nothing")


def test_another_tenants_case_is_a_404_not_a_403(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    for client in _client(monkeypatch, store, case=_case_document(tenant_id="tenant-b")):
        response = _post(client)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "CASE_CLARIFICATION_NOT_FOUND"
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_missing_case_is_the_same_404(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """The same body as the wrong-tenant case. Telling the two apart tells a
    caller the case exists."""
    for client in _client(monkeypatch, store, case=_MISSING):
        response = _post(client)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "CASE_CLARIFICATION_NOT_FOUND"
        return
    raise AssertionError("the client fixture yielded nothing")


def test_a_principal_without_the_capability_is_refused(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """`RETURNS_SUPPORT_ACT`, and the check runs before the case is read -- a
    principal with no capability must not learn whether the case exists.

    A *real* role that lacks the capability, not an empty role set: an empty one
    is refused by `Principal` itself, so the test would be measuring the value
    object rather than the route's authorization.
    """
    for client in _client(monkeypatch, store, roles=frozenset({r.WAREHOUSE_ASSOCIATE})):
        assert _post(client).status_code in (401, 403)
        return
    raise AssertionError("the client fixture yielded nothing")


def test_an_empty_answer_is_refused(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    for client in _client(monkeypatch, store):
        assert _post(client, answerText="   ").status_code == 422
        return
    raise AssertionError("the client fixture yielded nothing")


def test_an_oversized_answer_is_refused_rather_than_truncated(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
) -> None:
    """The cut half of a truncated answer may be the part that identified the
    record -- the same argument `support_ingress` makes about message bodies."""
    for client in _client(monkeypatch, store):
        assert _post(client, answerText="x" * (module.MAX_ANSWER_CHARACTERS + 1)).status_code == 422
        return
    raise AssertionError("the client fixture yielded nothing")


def test_the_recorded_command_carries_the_server_stamped_actor(
    monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore, mongo: FakeClient
) -> None:
    """Sect. 4: command-originated facts carry a server-stamped `actorId`.

    Asserted on the stored command document, not on the response -- the response
    does not carry the actor, and a handler that stamped the response while
    writing something else would pass a weaker test.
    """
    for client in _client(monkeypatch, store):
        assert _post(client).status_code == 202
        break
    import asyncio

    document = asyncio.run(_only_command(mongo))
    assert document["actorId"] == ACTOR
    assert document["kind"] == "clarification_answered"
    # `actor` and `answer`, not `answered_by` and `answer_text`: the payload is
    # the signal body and its keys are `ClarificationAnsweredNotice`'s field
    # names. The old spelling could not have deserialized into the notice the
    # workflow declares, so the round-trip would have dead-lettered on the first
    # real answer -- see `TestTheSignalBodyIsTheNoticeTheWorkflowDeclares`.
    assert document["payload"]["actor"] == ACTOR
    assert document["payload"]["answer"] == "It belongs to RMA-4471."


class TestTheSignalBodyIsTheNoticeTheWorkflowDeclares:
    """The payload this endpoint stores *is* the signal Temporal delivers.

    `DurableCaseCommandStore` copies it verbatim into the outbox row, and
    `CaseCommandSignalDispatcher` hands it to `handle.signal(...)` unchanged --
    so a key this endpoint spells differently from
    `ClarificationAnsweredNotice` is a signal that cannot be deserialized, on a
    path that had no end-to-end test to notice. It did not have one: the payload
    shipped as `answer_text`/`answered_by`/`case_id` with no `signal_id` at all,
    and every test passed.
    """

    def test_every_stored_key_is_a_field_of_the_notice(
        self, monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore, mongo: FakeClient
    ) -> None:
        import asyncio
        from dataclasses import fields

        from return_platform.workflows.return_case_workflow import ClarificationAnsweredNotice

        for client in _client(monkeypatch, store):
            assert _post(client).status_code == 202
            break
        payload = asyncio.run(_only_command(mongo))["payload"]
        declared = {field.name for field in fields(ClarificationAnsweredNotice)}
        assert set(payload) <= declared, set(payload) - declared

    def test_the_stored_payload_constructs_the_notice(
        self, monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore, mongo: FakeClient
    ) -> None:
        """Constructed, not merely key-checked.

        A subset assertion alone would pass for a payload missing a *required*
        field -- which is precisely how the old shape failed, having no
        `signal_id`.
        """
        import asyncio

        from return_platform.workflows.return_case_workflow import ClarificationAnsweredNotice

        for client in _client(monkeypatch, store):
            assert _post(client).status_code == 202
            break
        payload = asyncio.run(_only_command(mongo))["payload"]
        notice = ClarificationAnsweredNotice(**payload)
        assert notice.actor == ACTOR
        assert notice.support_event_id == SUPPORT_EVENT_ID
        assert notice.verbatim_question == QUESTION
        assert notice.signal_id

    def test_an_answer_to_a_clarification_this_case_never_asked_is_a_404(
        self, monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore
    ) -> None:
        """The same 404 as a case the principal cannot see.

        Accepting it would record a command, queue a delivery, and relay a
        stranger's words to Support under an id nobody recognises.
        """
        for client in _client(monkeypatch, store, facts=[]):
            response = _post(client)
            assert response.status_code == 404
            assert response.json()["detail"]["code"] == "CASE_CLARIFICATION_NOT_FOUND"
            break

    def test_the_question_comes_from_the_case_and_never_from_the_body(
        self, monkeypatch: pytest.MonkeyPatch, store: DurableCaseCommandStore, mongo: FakeClient
    ) -> None:
        """The request model has no field for it, and the stored one is the fact's."""
        import asyncio

        from return_platform.api.case_clarifications import ClarificationAnswerRequest

        assert "verbatimQuestion" not in ClarificationAnswerRequest.model_fields
        for client in _client(monkeypatch, store):
            assert _post(client).status_code == 202
            break
        payload = asyncio.run(_only_command(mongo))["payload"]
        assert payload["verbatim_question"] == QUESTION


async def _command_count(mongo: FakeClient) -> int:
    total = 0
    for database_name in mongo.databases:  # type: ignore[attr-defined]
        collection = mongo[database_name]["case_command_records"]
        total += len([document async for document in collection.find({})])
    return total


@pytest.mark.parametrize(
    ("label", "case", "facts"),
    [
        pytest.param("another_tenant", _case_document(tenant_id="tenant-b"), None, id="another_tenant"),
        pytest.param("missing_case", _MISSING, None, id="missing_case"),
        pytest.param("never_asked", None, [], id="never_asked"),
    ],
)
def test_a_refused_answer_records_no_command(
    monkeypatch: pytest.MonkeyPatch,
    store: DurableCaseCommandStore,
    mongo: FakeClient,
    label: str,
    case: dict[str, Any] | None,
    facts: list[dict[str, Any]] | None,
) -> None:
    """A 404 must leave nothing behind (contracts.md sect. 9, items 11-12).

    The sibling tests assert the *status* of each refusal. None of them asserts
    the half that matters durably: that the answer did not reach
    `case_command_records` on its way to being refused. A handler that recorded
    the command and then refused would return exactly the same 404 with exactly
    the same body, while a stranger's words sat on file against a case they
    cannot see -- queued for delivery to Support behind a clarification id this
    principal was never shown.

    (ACC3 category-B audit: INJ-B20 deferred the refusal until after
    `record_command` and left all 5,237 backend tests green.)
    """
    import asyncio

    kwargs: dict[str, Any] = {}
    if case is not _MISSING:
        kwargs["case"] = case
    else:
        kwargs["case"] = _MISSING
    if facts is not None:
        kwargs["facts"] = facts

    for client in _client(monkeypatch, store, **kwargs):
        response = _post(client)
        assert response.status_code == 404, label
        assert response.json()["detail"]["code"] == "CASE_CLARIFICATION_NOT_FOUND"
        break
    else:  # pragma: no cover - the fixture always yields
        raise AssertionError("the client fixture yielded nothing")

    assert asyncio.run(_command_count(mongo)) == 0, (
        f"{label}: the refusal left a command on file"
    )


async def _only_command(mongo: FakeClient) -> dict[str, Any]:
    from return_platform.configuration.settings import Settings as _Settings

    del _Settings
    for database_name in mongo.databases:  # type: ignore[attr-defined]
        collection = mongo[database_name]["case_command_records"]
        found = await collection.find_one({})
        if found is not None:
            return dict(found)
    raise AssertionError("no command was recorded")
