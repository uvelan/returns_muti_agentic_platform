"""Where the server was more permissive than the console in front of it.

Four surfaces guarded a write with `require_write_roles` or a read with
`require_read_roles` while the React console gated the same action on a
*capability*. The role groups are much wider than the capabilities, so each of
these was an action a principal was shown no control for and would have been
allowed to perform by calling the endpoint directly:

    config.release.promote     6 of 7 write roles admitted, none entitled
    governance.proposal.write  5 of 7 write roles admitted, none entitled
    ai.interception.read       6 of 10 read roles admitted, none entitled

A fourth surface had no guard at all: eight handlers on `/api/graph-schema`
declared no authorization dependency and the router adds none, so creating a
draft, rewriting its schema through typed mutations, reading it, diffing its
revisions, re-running analysis and validating it were reachable by any
authenticated caller. Its sibling analyzer has required the same two
capabilities for the same operations all along.

Every case is asserted through a role that genuinely lacks the capability,
derived from the capability table rather than named here -- a hardcoded role
stops testing anything the day someone grants it the capability.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.security import capabilities as caps
from return_platform.security import roles as r
from return_platform.security.principal import Principal


def _admitted_but_unentitled(group: frozenset[str], capability: str) -> list[str]:
    """Roles the old guard let through and the capability does not."""
    return sorted(
        role for role in group if capability not in caps.capabilities_for_roles(frozenset({role}))
    )


def _client(app: FastAPI, *role_names: str) -> Iterator[TestClient]:
    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="operator", roles=frozenset(role_names))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    ("group", "capability", "what"),
    [
        (r.WRITE_ROLES, caps.CONFIG_RELEASE_PROMOTE, "promoting a configuration release"),
        (r.WRITE_ROLES, caps.GOVERNANCE_PROPOSAL_WRITE, "proposing an agent configuration"),
        (r.READ_ROLES, caps.AI_INTERCEPTION_READ, "listing the interception queue"),
    ],
)
def test_the_role_group_is_wider_than_the_capability(
    group: frozenset[str], capability: str, what: str
) -> None:
    """The premise, stated so the tests below cannot pass vacuously.

    If a capability were held by every role in its group, guarding on the role
    and guarding on the capability would be the same thing and none of this
    would be worth testing. They are not the same thing.
    """
    gap = _admitted_but_unentitled(group, capability)
    assert gap, (
        f"{capability} is held by every role that {what} admitted; "
        f"this suite can no longer tell the two guards apart"
    )


def test_promoting_a_release_needs_the_promote_capability() -> None:
    """`warehouse_associate` is a write role and does not publish releases."""
    from return_platform.configuration.api.router import router

    unentitled = _admitted_but_unentitled(r.WRITE_ROLES, caps.CONFIG_RELEASE_PROMOTE)
    app = FastAPI()
    app.include_router(router)

    for client in _client(app, unentitled[0]):
        response = client.post(
            "/api/config/releases/release-1/promote",
            json={"status": "RELEASED"},
        )

    # 403 and not 503: the grant resolves before the collaborators, so an
    # unauthorized caller cannot read this process's composition state off the
    # status code.
    assert response.status_code == 403, response.text


def test_proposing_an_agent_configuration_needs_proposal_write() -> None:
    """The console disables every field without this capability. So does the API."""
    from return_platform.configuration.api.agents import router

    unentitled = _admitted_but_unentitled(r.WRITE_ROLES, caps.GOVERNANCE_PROPOSAL_WRITE)
    app = FastAPI()
    app.include_router(router)

    for client in _client(app, unentitled[0]):
        response = client.put(
            "/api/agents/agent.order_discovery",
            json={"document": {}},
        )

    assert response.status_code == 403, response.text


def test_listing_interceptions_needs_the_read_capability() -> None:
    """The hold queue is operator work, not everything a reader may see."""
    from return_platform.api.canonical_ai import router

    unentitled = _admitted_but_unentitled(r.READ_ROLES, caps.AI_INTERCEPTION_READ)
    app = FastAPI()
    app.include_router(router)

    for client in _client(app, unentitled[0]):
        response = client.get("/api/ai/interceptions")

    assert response.status_code == 403, response.text


#: Every `/api/graph-schema` draft handler that declared no guard, with a
#: request that reaches it.
#:
#: Asserted by *calling* each one rather than by reading the route table. The
#: defect was an absent dependency, so an introspection test would be the more
#: direct statement -- but this router builds its routes through a custom
#: include wrapper, and a test that walks route objects would be asserting
#: something about that wrapper's internals rather than about authorization. A
#: 403 is unambiguous.
_DRAFT_REQUESTS: tuple[tuple[str, str, dict[str, object] | None], ...] = (
    ("post", "/api/graph-schema/analyses/analysis-1/drafts", None),
    (
        "post",
        "/api/graph-schema/drafts/draft-1/mutations",
        {"mutations": [{"kind": "RENAME_ENTITY", "entityId": "e", "name": "n"}]},
    ),
    ("get", "/api/graph-schema/drafts/draft-1", None),
    ("get", "/api/graph-schema/drafts/draft-1/shape", None),
    ("get", "/api/graph-schema/drafts/draft-1/revisions", None),
    ("get", "/api/graph-schema/drafts/draft-1/revisions/1/diff", None),
    ("post", "/api/graph-schema/drafts/draft-1/reanalysis", None),
    ("post", "/api/graph-schema/drafts/draft-1/validate", None),
)


@pytest.mark.parametrize(("method", "path", "body"), _DRAFT_REQUESTS)
def test_no_schema_draft_route_answers_an_unauthorized_caller(
    method: str, path: str, body: dict[str, object] | None
) -> None:
    """None of these declared a grant, and the router adds none.

    So creating a draft, rewriting its schema through typed mutations, reading
    it, diffing its revisions, re-running analysis and validating it were each
    reachable by any authenticated caller -- while the sibling analyzer at
    `/api/graph-analyzer/v1` had required a capability for the same operations
    all along.

    The caller holds a real role that grants neither draft capability, derived
    from the table rather than named -- `Principal` refuses an empty role set,
    so "entitled to nothing" has to be expressed as a role that happens to be
    entitled to nothing *here*.
    """
    from return_platform.graph_schema_analyzer.api.router import router

    outsiders = [
        role
        for role in sorted(r.ALL_ROLES)
        if not {caps.GRAPH_SCHEMA_DRAFT_READ, caps.GRAPH_SCHEMA_DRAFT_WRITE}
        & caps.capabilities_for_roles(frozenset({role}))
    ]
    assert outsiders, "every role touches schema drafts; this test cannot distinguish"

    app = FastAPI()
    app.include_router(router)

    for client in _client(app, outsiders[0]):
        response = client.request(method.upper(), path, json=body)

    assert response.status_code == 403, (
        f"{method.upper()} {path} answered {response.status_code} to a caller "
        f"holding no capability: {response.text[:200]}"
    )


def test_reading_a_draft_needs_the_read_capability() -> None:
    from return_platform.graph_schema_analyzer.api.router import router

    unentitled = _admitted_but_unentitled(r.ALL_ROLES, caps.GRAPH_SCHEMA_DRAFT_READ)
    assert unentitled, "every role can read drafts; this test cannot distinguish"
    app = FastAPI()
    app.include_router(router)

    for client in _client(app, unentitled[0]):
        response = client.get("/api/graph-schema/drafts/draft-1")

    assert response.status_code == 403, response.text


def test_mutating_a_draft_needs_the_write_capability() -> None:
    """A reader must not rewrite the schema a release will publish."""
    from return_platform.graph_schema_analyzer.api.router import router

    readers = [
        role
        for role in sorted(r.ALL_ROLES)
        if caps.GRAPH_SCHEMA_DRAFT_READ in caps.capabilities_for_roles(frozenset({role}))
        and caps.GRAPH_SCHEMA_DRAFT_WRITE not in caps.capabilities_for_roles(frozenset({role}))
    ]
    assert readers, "no role reads drafts without writing them"
    app = FastAPI()
    app.include_router(router)

    for client in _client(app, readers[0]):
        response = client.post(
            "/api/graph-schema/drafts/draft-1/mutations",
            json={"mutations": [{"kind": "RENAME_ENTITY", "entityId": "e", "name": "n"}]},
        )

    assert response.status_code == 403, response.text
