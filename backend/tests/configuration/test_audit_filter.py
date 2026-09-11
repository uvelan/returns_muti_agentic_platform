"""CFG-3a scope item 6: `GET /api/config/audit?actions=...&target=...`.

`AuditService.list_logs` used to run `find({})` unconditionally -- every
caller paged through the whole platform-wide `audit` collection (AI gateway,
governance kernel and configuration releases all write through the same
`append_audit`) to find one release's trail. These tests pin the query the
filter now builds, and that omitting both parameters is still the exact
`find({})` it always was.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.configuration.api.audit import AuditService, _action_pattern

# --- _action_pattern, in isolation --------------------------------------------


def test_a_bare_action_matches_only_itself() -> None:
    import re

    pattern = re.compile(_action_pattern(["CONFIGURATION_RELEASE_PROMOTED"]))
    assert pattern.match("CONFIGURATION_RELEASE_PROMOTED")
    assert not pattern.match("CONFIGURATION_RELEASE_PROMOTED_EXTRA")
    assert not pattern.match("CONFIGURATION_DOMAIN_PATCHED")


def test_a_trailing_star_matches_as_a_prefix() -> None:
    import re

    pattern = re.compile(_action_pattern(["CONFIGURATION_*"]))
    assert pattern.match("CONFIGURATION_RELEASE_PROMOTED")
    assert pattern.match("CONFIGURATION_DOMAIN_PATCHED")
    assert not pattern.match("AI_ROUTE_REFRESHED")


def test_multiple_entries_are_ored_together() -> None:
    import re

    pattern = re.compile(_action_pattern(["AI_ROUTE_REFRESHED", "CONFIGURATION_*"]))
    assert pattern.match("AI_ROUTE_REFRESHED")
    assert pattern.match("CONFIGURATION_RELEASE_PROMOTED")
    assert not pattern.match("GOVERNANCE_PROPOSAL_APPROVED")


def test_special_regex_characters_in_an_action_name_are_escaped() -> None:
    """An action name is not attacker input here, but a name containing a
    character that means something to a regex must still match literally,
    not be interpreted."""
    import re

    pattern = re.compile(_action_pattern(["A.B"]))
    assert pattern.match("A.B")
    assert not pattern.match("AxB")


# --- AuditService.list_logs builds the query it claims to ---------------------


class _FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, *_args: object, **_kwargs: object) -> _FakeCursor:
        return self

    def limit(self, *_args: object, **_kwargs: object) -> _FakeCursor:
        return self

    def __aiter__(self) -> Any:
        return self._iter()

    async def _iter(self) -> Any:
        for document in self._documents:
            yield document


class _FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self.queries: list[dict[str, Any]] = []

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        self.queries.append(query)
        matched = [doc for doc in self._documents if _matches(doc, query)]
        return _FakeCursor(matched)


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    """Just enough of Mongo's query language for these tests: exact-match
    keys, `{"$regex": pattern}` and `{"$in": [...]}` for `action`."""
    import re

    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict) and "$regex" in expected:
            if not re.match(expected["$regex"], str(actual)):
                return False
        elif isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


def _service_with(documents: list[dict[str, Any]]) -> tuple[AuditService, _FakeCollection]:
    collection = _FakeCollection(documents)

    class _FakeDB:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return collection

    class _FakeClient:
        def __getitem__(self, _name: str) -> _FakeDB:
            return _FakeDB()

    service = AuditService(_FakeClient(), "test")  # type: ignore[arg-type]
    return service, collection


def _record(action: str, target: str = "release-1") -> dict[str, Any]:
    return {
        "_id": f"{action}-{target}",
        "action": action,
        "actor": "operator",
        "target": target,
        "timestamp": datetime.now(UTC),
        "details": {},
    }


@pytest.mark.asyncio
async def test_no_filter_is_the_unfiltered_read_it_always_was() -> None:
    service, collection = _service_with(
        [_record("CONFIGURATION_RELEASE_PROMOTED"), _record("AI_ROUTE_REFRESHED")]
    )

    results = await service.list_logs()

    assert collection.queries == [{}]
    assert {log.action for log in results} == {
        "CONFIGURATION_RELEASE_PROMOTED",
        "AI_ROUTE_REFRESHED",
    }


@pytest.mark.asyncio
async def test_actions_filters_server_side() -> None:
    service, collection = _service_with(
        [
            _record("CONFIGURATION_RELEASE_PROMOTED"),
            _record("CONFIGURATION_DOMAIN_PATCHED"),
            _record("AI_ROUTE_REFRESHED"),
        ]
    )

    results = await service.list_logs(actions=["CONFIGURATION_*"])

    assert "action" in collection.queries[0]
    assert {log.action for log in results} == {
        "CONFIGURATION_RELEASE_PROMOTED",
        "CONFIGURATION_DOMAIN_PATCHED",
    }


@pytest.mark.asyncio
async def test_target_filters_to_an_exact_match() -> None:
    service, _collection = _service_with(
        [
            _record("CONFIGURATION_RELEASE_PROMOTED", target="release-1"),
            _record("CONFIGURATION_RELEASE_PROMOTED", target="release-2"),
        ]
    )

    results = await service.list_logs(target="release-1")

    assert [log.target for log in results] == ["release-1"]


@pytest.mark.asyncio
async def test_actions_and_target_combine() -> None:
    service, _collection = _service_with(
        [
            _record("CONFIGURATION_RELEASE_PROMOTED", target="release-1"),
            _record("CONFIGURATION_DOMAIN_PATCHED", target="release-2"),
            _record("AI_ROUTE_REFRESHED", target="release-1"),
        ]
    )

    results = await service.list_logs(actions=["CONFIGURATION_*"], target="release-1")

    assert len(results) == 1
    assert results[0].action == "CONFIGURATION_RELEASE_PROMOTED"
    assert results[0].target == "release-1"


# --- RV CFG-3a round 1 F9 -----------------------------------------------------


@pytest.mark.asyncio
async def test_all_exact_actions_use_in_not_regex() -> None:
    """No entry ends in `*` -- the whole list is exact matches, which can use
    Mongo's `$in` (and its index) instead of an unindexed `$regex` scan."""
    service, collection = _service_with(
        [
            _record("CONFIGURATION_RELEASE_PROMOTED"),
            _record("AI_ROUTE_REFRESHED"),
            _record("GOVERNANCE_PROPOSAL_APPROVED"),
        ]
    )

    results = await service.list_logs(
        actions=["CONFIGURATION_RELEASE_PROMOTED", "AI_ROUTE_REFRESHED"]
    )

    assert collection.queries[0]["action"] == {
        "$in": ["CONFIGURATION_RELEASE_PROMOTED", "AI_ROUTE_REFRESHED"]
    }
    assert {log.action for log in results} == {
        "CONFIGURATION_RELEASE_PROMOTED",
        "AI_ROUTE_REFRESHED",
    }


@pytest.mark.asyncio
async def test_one_prefix_entry_falls_back_to_regex_for_the_whole_list() -> None:
    """A single `*` entry among otherwise-exact ones still needs the
    alternation -- `$in` cannot express a prefix match."""
    service, collection = _service_with([_record("CONFIGURATION_RELEASE_PROMOTED")])

    await service.list_logs(actions=["AI_ROUTE_REFRESHED", "CONFIGURATION_*"])

    assert "$regex" in collection.queries[0]["action"]


@pytest.mark.asyncio
async def test_more_than_max_actions_is_refused() -> None:
    from return_platform.configuration.api.audit import MAX_ACTIONS

    service, _collection = _service_with([])

    with pytest.raises(ValueError, match="at most"):
        await service.list_logs(actions=[f"ACTION_{i}" for i in range(MAX_ACTIONS + 1)])


def test_max_actions_is_enforced_as_a_query_parameter_bound() -> None:
    """The HTTP-facing half of F9: the router refuses an over-long `actions`
    list with a 422 before `list_logs` is ever called, via FastAPI's own
    `Query(max_length=...)` -- not by relying on the service-level check
    alone."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from return_platform.configuration.api.audit import MAX_ACTIONS
    from return_platform.configuration.api.router import router
    from return_platform.security.principal import Principal

    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=frozenset({"console_admin"}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)

    too_many = [("actions", f"ACTION_{i}") for i in range(MAX_ACTIONS + 1)]
    response = client.get("/api/config/audit", params=too_many)

    assert response.status_code == 422, response.text


def test_special_regex_characters_in_an_action_name_are_still_escaped_in_the_alternation() -> None:
    """Redundant with `test_special_regex_characters_in_an_action_name_are_escaped`
    above at the `_action_pattern` level, pinned again here because F9's
    review specifically checked `re.escape` was applied -- a regression that
    dropped it would otherwise only be caught by that one earlier test."""
    import re

    from return_platform.configuration.api.audit import _action_pattern

    pattern = _action_pattern(["A.B*"])
    assert re.match(pattern, "A.B_ANYTHING")
    assert not re.match(pattern, "AxB_ANYTHING")
