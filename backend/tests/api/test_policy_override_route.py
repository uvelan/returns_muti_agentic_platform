"""`POST /api/cases/{caseId}/policy-override` -- the supervisor's decision (3A.8).

Four properties, each of which the plan names as a way this endpoint could be
written wrong:

* **The client cannot supply audit.** `actor` and the timestamp come from the
  authenticated principal and the server clock. A body that carries them is
  answered with the server's own values, never the caller's.
* **The original decision survives.** The override is appended; nothing
  overwrites `policy_decision`, so both readings stay recoverable.
* **A stale revision is a 409.** A supervisor deciding against a case that has
  since moved does not get to overwrite the newer state.
* **It is not the associate's to make.** The gate is a capability, and the route
  is scoped by tenant rather than by principal -- the person who raised the
  return is exactly the person who must not overrule the policy on it.

Over real HTTP against a stub repository, matching
`test_case_reads_are_scoped_to_the_caller.py`: what is under test is the
handler's decisions, and a real datastore would move the assertions from "the
handler refused" to "the query found nothing".
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import cases as cases_module
from return_platform.api.cases import router
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.security import roles as r
from return_platform.security.principal import Principal

TENANT = "tenant-a"
ASSOCIATE = "associate-1"
SUPERVISOR = "supervisor-1"


def _case(*, version: int = 3, tenant_id: str = TENANT) -> dict[str, Any]:
    return {
        "caseId": "case-1",
        "tenantId": tenant_id,
        "principalId": ASSOCIATE,
        "status": "AWAITING_POLICY_REVIEW",
        "version": version,
    }


def _evaluated_facts() -> list[dict[str, Any]]:
    return [
        {
            "factId": "policy_route-seed",
            "caseId": "case-1",
            "factName": "policy_route",
            "value": "STANDARD_RETURN",
            "recordedAt": "2026-08-15T11:00:00+00:00",
        },
        {
            "factId": "policy_decision-seed",
            "caseId": "case-1",
            "factName": "policy_decision",
            "value": "REVIEW_REQUIRED",
            "recordedAt": "2026-08-15T11:00:00+00:00",
        },
        {
            "factId": "policy_effective_decision-seed",
            "caseId": "case-1",
            "factName": "policy_effective_decision",
            "value": "REVIEW_REQUIRED",
            "recordedAt": "2026-08-15T11:00:00+00:00",
        },
    ]


class StubRepository:
    """Enough of the case repository for one override, and it records everything."""

    def __init__(
        self,
        *,
        stored: dict[str, Any] | None = None,
        facts: list[dict[str, Any]] | None = None,
    ) -> None:
        self._stored = stored
        self.facts: list[dict[str, Any]] = list(facts or [])
        self.appended: list[dict[str, Any]] = []

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        return self._stored

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for document in self.facts:
            latest[str(document["factName"])] = document
        return latest

    async def update_case(
        self, case_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        assert self._stored is not None
        if expected_version != self._stored["version"]:
            raise ConcurrencyConflictError(case_id)
        self._stored.update(updates)
        self._stored["version"] = expected_version + 1
        return self._stored

    async def append_case_fact(self, **fact: Any) -> dict[str, Any]:
        self.appended.append(fact)
        document = {
            "factId": fact["fact_id"],
            "caseId": fact["case_id"],
            "factName": fact["fact_name"],
            "value": fact["value"],
            "recordedAt": "2026-08-15T12:00:00+00:00",
        }
        self.facts.append(document)
        return document

    def appended_value(self, name: str) -> Any:
        for fact in self.appended:
            if fact["fact_name"] == name:
                return fact["value"]
        return None


class _Handle:
    def __init__(self, sink: list[tuple[str, Any]], *, fail: bool = False) -> None:
        self._sink = sink
        self._fail = fail

    async def signal(self, name: str, notice: Any) -> None:
        if self._fail:
            raise RuntimeError("Temporal is unavailable")
        self._sink.append((name, notice))


class _TemporalClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.signals: list[tuple[str, Any]] = []
        self.workflow_ids: list[str] = []
        self._fail = fail

    def get_workflow_handle(self, workflow_id: str) -> _Handle:
        self.workflow_ids.append(workflow_id)
        return _Handle(self.signals, fail=self._fail)


class _Resources:
    def __init__(self, temporal: _TemporalClient | None) -> None:
        self.temporal = temporal


@pytest.fixture(autouse=True)
def _stub_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cases_module,
        "resolve_operational_repository",
        lambda request: request.app.state.stub_repository,
    )


def _client(
    repository: StubRepository,
    *,
    roles: frozenset[str] = frozenset({r.CONSOLE_ADMIN}),
    subject: str = SUPERVISOR,
    tenant_id: str = TENANT,
    temporal: _TemporalClient | None = None,
) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject=subject, roles=roles)
        request.state.tenant_id = tenant_id
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.stub_repository = repository
    app.state.resources = _Resources(temporal)
    with TestClient(app) as client:
        yield client


def _body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "expectedRevision": 3,
        "overrideDecision": "APPROVE",
        "reasonCode": "SUPERVISOR_JUDGEMENT",
        "reason": "Customer produced the packing slip.",
        "idempotencyKey": "override-key-1",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------


def test_a_supervisor_override_is_recorded_and_signalled() -> None:
    repository = StubRepository(stored=_case(), facts=_evaluated_facts())
    temporal = _TemporalClient()
    for client in _client(repository, temporal=temporal):
        response = client.post("/api/cases/case-1/policy-override", json=_body())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["originalDecision"] == "REVIEW_REQUIRED"
    assert data["effectiveDecision"] == "APPROVE"
    assert data["signalDelivered"] is True
    # The revision advanced, because the projection changed.
    assert data["revision"] == 4
    assert temporal.workflow_ids == ["return-case-case-1"]
    signal_name, notice = temporal.signals[0]
    assert signal_name == "policy_override"
    assert notice.override_decision == "APPROVE"
    assert notice.actor == SUPERVISOR


def test_the_actor_and_timestamp_are_server_derived() -> None:
    """A body carrying its own audit fields is answered with the server's.

    Dropped rather than refused: refusing would tell the caller the field exists
    and is merely mis-typed, which invites the next attempt to guess the right
    spelling. The values it tried to set appear nowhere in the response or on the
    log.
    """
    repository = StubRepository(stored=_case(), facts=_evaluated_facts())
    for client in _client(repository):
        response = client.post(
            "/api/cases/case-1/policy-override",
            json=_body(
                actor="someone-else",
                overriddenAt="1999-01-01T00:00:00+00:00",
                originalDecision="APPROVE",
                tenantId="tenant-b",
            ),
        )

    assert response.status_code == 200, response.text
    override = response.json()["data"]["override"]
    assert override["actor"] == SUPERVISOR
    assert override["overriddenAt"].startswith("20")
    assert override["overriddenAt"] != "1999-01-01T00:00:00+00:00"
    assert repository.appended_value("policy_override_actor") == SUPERVISOR
    assert repository.appended_value("policy_override_original_decision") == "REVIEW_REQUIRED"
    # The claimed original never reached the log.
    assert response.json()["data"]["originalDecision"] == "REVIEW_REQUIRED"


def test_the_original_decision_is_never_overwritten() -> None:
    """Append-only. `policy_decision` is not among the facts the override writes."""
    repository = StubRepository(stored=_case(), facts=_evaluated_facts())
    for client in _client(repository):
        response = client.post("/api/cases/case-1/policy-override", json=_body())

    assert response.status_code == 200, response.text
    written = {fact["fact_name"] for fact in repository.appended}
    assert "policy_decision" not in written
    assert "policy_override_decision" in written
    # The effective reading moves; the evaluator's own answer stays put.
    assert repository.appended_value("policy_effective_decision") == "APPROVE"
    original = [fact for fact in repository.facts if fact["factName"] == "policy_decision"]
    assert [fact["value"] for fact in original] == ["REVIEW_REQUIRED"]


def test_a_stale_expected_revision_is_a_conflict() -> None:
    repository = StubRepository(stored=_case(version=5), facts=_evaluated_facts())
    temporal = _TemporalClient()
    for client in _client(repository, temporal=temporal):
        response = client.post("/api/cases/case-1/policy-override", json=_body(expectedRevision=3))

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CASE_REVISION_CONFLICT"
    # Nothing was written and nothing was signalled.
    assert repository.appended == []
    assert temporal.signals == []


def test_a_repeated_override_is_answered_from_what_was_written() -> None:
    """The same idempotency key twice is one decision, not two.

    Answered before the revision check on purpose: the first attempt already
    bumped the revision, so a retry carrying the original `expectedRevision`
    would otherwise read as a conflict.
    """
    repository = StubRepository(stored=_case(), facts=_evaluated_facts())
    for client in _client(repository):
        first = client.post("/api/cases/case-1/policy-override", json=_body())
        written = len(repository.appended)
        second = client.post("/api/cases/case-1/policy-override", json=_body())

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(repository.appended) == written, "a retry wrote a second override"
    assert second.json()["data"]["effectiveDecision"] == "APPROVE"
    assert second.json()["data"]["originalDecision"] == "REVIEW_REQUIRED"


def test_a_case_with_no_evaluation_has_nothing_to_override() -> None:
    repository = StubRepository(stored=_case(), facts=[])
    for client in _client(repository):
        response = client.post("/api/cases/case-1/policy-override", json=_body())

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "POLICY_NOT_EVALUATED"


def test_a_routed_case_cannot_be_overridden() -> None:
    """Warranty and delivery claims are verified by Support, not overruled.

    An override here would be a supervisor approving the very claim the
    verification exists to test, and `PolicyEvaluationProjection` refuses the
    same combination on the read side.
    """
    facts = [fact for fact in _evaluated_facts() if fact["factName"] != "policy_route"]
    facts.append(
        {
            "factId": "policy_route-seed",
            "caseId": "case-1",
            "factName": "policy_route",
            "value": "WARRANTY",
            "recordedAt": "2026-08-15T11:00:00+00:00",
        }
    )
    repository = StubRepository(stored=_case(), facts=facts)
    for client in _client(repository):
        response = client.post("/api/cases/case-1/policy-override", json=_body())

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "POLICY_ROUTE_NOT_OVERRIDABLE"


def test_review_required_is_not_an_override_decision() -> None:
    """It is the state the case is already in, so it overrides nothing."""
    repository = StubRepository(stored=_case(), facts=_evaluated_facts())
    for client in _client(repository):
        response = client.post(
            "/api/cases/case-1/policy-override", json=_body(overrideDecision="REVIEW_REQUIRED")
        )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "roles",
    [
        frozenset({r.RETURN_ASSOCIATE}),
        frozenset({r.RETURN_SUPPORT}),
        frozenset({r.RETURN_PLATFORM_SERVICE}),
        frozenset({r.CONSOLE_VIEWER}),
    ],
)
def test_only_a_supervisor_may_override(roles: frozenset[str]) -> None:
    """Including the service account: a machine must not answer for a human.

    The associate who raised the return is the most important refusal here --
    they hold write access to the case and must still not be able to overrule
    the rule set on it.
    """
    repository = StubRepository(stored=_case(), facts=_evaluated_facts())
    for client in _client(repository, roles=roles, subject=ASSOCIATE):
        response = client.post("/api/cases/case-1/policy-override", json=_body())

    assert response.status_code == 403, response.text
    assert repository.appended == []


def test_another_tenants_case_reads_as_absent() -> None:
    repository = StubRepository(stored=_case(tenant_id="tenant-b"), facts=_evaluated_facts())
    for client in _client(repository):
        response = client.post("/api/cases/case-1/policy-override", json=_body())

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"


def test_an_undelivered_signal_still_leaves_an_audited_override() -> None:
    """Temporal is down. The decision is recorded and the caller is told.

    Recording before signalling is the ordering that survives the outage: the
    other way round leaves a case that moved with nothing written down.
    """
    repository = StubRepository(stored=_case(), facts=_evaluated_facts())
    for client in _client(repository, temporal=_TemporalClient(fail=True)):
        response = client.post("/api/cases/case-1/policy-override", json=_body())

    assert response.status_code == 200, response.text
    assert response.json()["data"]["signalDelivered"] is False
    assert repository.appended_value("policy_override_decision") == "APPROVE"
