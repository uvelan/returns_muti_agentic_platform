"""SECV-01: the isolation guards exercised as an attacker reaches them.

The guards were already implemented and already believed. `_belongs_to` sits at
`api/cases.py:89`, `require_capability` at `security/authorization.py:69`, and
both had tests -- of the *function*. `tests/security/test_capability_model.py`
calls the dependency with a stub request; `tests/operations/
test_case_aggregate_real_infra.py` calls the repository with an explicit
`tenant_id=`. Neither sends a request.

That gap is the whole of this file, and it is not a formality. The repository
test proves `get_case_by_conversation` filters in its query. The route under
test here does not use that method: `get_case(case_id)` is unscoped by design
and the *route* applies `_belongs_to` afterwards. So the one path an attacker
actually has -- guess an id, ask for it over HTTP -- was the one path nothing
had ever driven. `tests/api/test_case_detail_multi_rma.py` says so in its own
docstring: "shaped as a projection test over the real repository rather than an
HTTP test".

Real routers, real ASGI transport, real Mongo. The only thing substituted is
the identity the auth middleware would have established, which is precisely
what a probe has to vary.

**Deliberately not a role-permission matrix.** This deployment has no non-admin
users; every principal is `console_admin`, so enumerating which role may call
which endpoint would be proving a table against itself. What still bites, and
what is probed here, is isolation *between* principals and tenants that hold
the same role -- an associate reading another associate's case is an ordinary
IDOR and no role check would ever catch it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from pymongo import AsyncMongoClient

from return_platform.api.cases import router as cases_router
from return_platform.api.proposals import resolve_proposal_kernel
from return_platform.api.proposals import router as proposals_router
from return_platform.configuration.settings import Settings
from return_platform.operations.repository import OperationalRepository
from return_platform.platform.governance.errors import UnknownProposal
from return_platform.resources import RuntimeResources
from return_platform.security import capabilities as caps
from return_platform.security import roles as r
from return_platform.security.capabilities import capabilities_for_roles
from return_platform.security.principal import Principal

#: Module-scoped, and the loop with it. `ensure_indexes` creates the operational
#: schema -- some sixty indexes -- which cost 32 seconds of setup *per test*
#: when the fixture was function-scoped, and none of these probes mutates
#: anything another one reads.
pytestmark = pytest.mark.asyncio(loop_scope="module")

OWNER_TENANT = "tenant-owner"
OWNER_PRINCIPAL = "associate-owner"
OTHER_TENANT = "tenant-intruder"
OTHER_PRINCIPAL = "associate-intruder"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- see `tests/conftest.py::test_settings`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/return_platform"
        "?authSource=admin&directConnection=true"
    )


class _Identity:
    """The identity the auth middleware would establish, made steerable.

    A mutable holder rather than a per-app constant so one composed application
    can answer as several callers. Rebuilding the app per probe would let a
    difference in composition explain a difference in outcome, which is exactly
    what an isolation test must not leave open.

    `roles=None` means *no principal at all* -- the unauthenticated request --
    rather than a principal holding nothing. `Principal` refuses an empty role
    set at construction, so the two are genuinely different states and only one
    of them can be expressed by a `Principal` instance.
    """

    def __init__(self) -> None:
        self.tenant_id = OWNER_TENANT
        self.principal_id = OWNER_PRINCIPAL
        self.roles: frozenset[str] | None = frozenset({r.RETURN_ASSOCIATE})

    def become(self, *, tenant_id: str, principal_id: str, roles: frozenset[str] | None) -> None:
        self.tenant_id = tenant_id
        self.principal_id = principal_id
        self.roles = roles


class _EmptyKernel:
    """Stands in for `ProposalKernel`: it holds nothing, and says so.

    Wired because `resolve_proposal_kernel` is declared *ahead* of
    `require_capability` on every governance route, so without a kernel the
    request is refused 503 before authorization is consulted and a probe would
    be asserting nothing about the gate. Substituting the service leaves the
    authorization real, which is the half under test.

    A granted caller therefore reaches a kernel that answers honestly -- an
    empty inbox, and `UnknownProposal` for an id that does not exist -- rather
    than one that raises and turns a passed gate into a failed test.
    """

    async def list(self, **kwargs: Any) -> tuple[Any, ...]:
        del kwargs
        return ()

    async def _unknown(self, proposal_id: str, **kwargs: Any) -> Any:
        del kwargs
        raise UnknownProposal(proposal_id)

    get = approve = reject = activate = _unknown


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def probe() -> AsyncIterator[tuple[httpx.AsyncClient, _Identity, OperationalRepository]]:
    """The real routers, over a real ASGI transport, against real Mongo.

    An isolated database per run: these probes assert on *absence*, and a stray
    case left by another suite would make "you cannot see it" pass for the wrong
    reason.
    """
    database = f"secv01_probe_{uuid.uuid4().hex[:12]}"
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    settings = Settings(
        environment="test",
        mongo_dsn=_mongo_dsn(),  # type: ignore[arg-type]
        mongo_database=database,
        source_mongo_database=database,
    )
    repository = OperationalRepository(mongo, settings)
    await repository.ensure_indexes()

    identity = _Identity()
    app = FastAPI()

    @app.middleware("http")
    async def _authenticate(request: Request, call_next: Any) -> Any:
        request.state.principal = (
            None
            if identity.roles is None
            else Principal(subject=identity.principal_id, roles=identity.roles)
        )
        request.state.tenant_id = identity.tenant_id
        request.state.branch_ids = ("CHARLOTTE",)
        request.state.correlation_id = "corr-secv01"
        return await call_next(request)

    app.include_router(cases_router)
    app.include_router(proposals_router)
    app.dependency_overrides[resolve_proposal_kernel] = lambda: _EmptyKernel()
    app.state.settings = settings
    app.state.resources = RuntimeResources(
        settings=settings,
        catalog=None,  # type: ignore[arg-type] - unused by these routes
        mongo=mongo,
        source_mongo=mongo,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://probe", timeout=30.0
    ) as http:
        try:
            yield http, identity, repository
        finally:
            await mongo.drop_database(database)
            await mongo.close()


async def _owned_case(repository: OperationalRepository, *, conversation_id: str) -> str:
    case = await repository.create_case(
        case_id=str(uuid.uuid4()),
        tenant_id=OWNER_TENANT,
        principal_id=OWNER_PRINCIPAL,
        channel_a_conversation_id=conversation_id,
        confirmed_order_reference="CW273354",
        confirmation_key=f"{OWNER_TENANT}|{conversation_id}|CW273354|",
    )
    return str(case["caseId"])


# --- Cross-tenant and cross-principal case access (IDOR) ----------------------


async def test_the_owner_can_read_their_own_case(
    probe: tuple[httpx.AsyncClient, _Identity, OperationalRepository],
) -> None:
    """The control.

    Without it every refusal below is satisfied by a route that returns 404 to
    everyone, which is a broken endpoint rather than an enforced boundary.
    """
    http, identity, repository = probe
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    case_id = await _owned_case(repository, conversation_id=conversation_id)

    identity.become(
        tenant_id=OWNER_TENANT,
        principal_id=OWNER_PRINCIPAL,
        roles=frozenset({r.RETURN_ASSOCIATE}),
    )
    response = await http.get(f"/api/cases/{case_id}")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["case"]["caseId"] == case_id


async def test_another_tenant_cannot_read_a_case_by_guessing_its_id(
    probe: tuple[httpx.AsyncClient, _Identity, OperationalRepository],
) -> None:
    """SECV-01's cross-tenant IDOR, driven over HTTP for the first time.

    The intruder carries the *same principal id* as well as a different tenant
    in the second half, because that is the failure `_belongs_to` documents
    itself as existing for: a principal id repeated in a second tenant would
    read across the boundary if either half of the check were dropped.
    """
    http, identity, repository = probe
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    case_id = await _owned_case(repository, conversation_id=conversation_id)

    for tenant_id, principal_id, description in (
        (OTHER_TENANT, OTHER_PRINCIPAL, "a different tenant and principal"),
        (OTHER_TENANT, OWNER_PRINCIPAL, "the same principal id in another tenant"),
        (OWNER_TENANT, OTHER_PRINCIPAL, "another principal in the same tenant"),
    ):
        identity.become(
            tenant_id=tenant_id,
            principal_id=principal_id,
            # A full console admin, deliberately. If the refusal depended on the
            # role rather than on ownership, this is where it would show.
            roles=frozenset({r.CONSOLE_ADMIN}),
        )
        response = await http.get(f"/api/cases/{case_id}")
        assert response.status_code == 404, (
            f"{description} read a case that is not theirs: {response.status_code} {response.text}"
        )
        # Absent, not forbidden: a 403 on a guessed id confirms the id exists.
        assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"
        assert case_id not in response.text.replace(f"Case {case_id} does not exist.", "")


async def test_the_case_list_never_leaks_another_owners_case(
    probe: tuple[httpx.AsyncClient, _Identity, OperationalRepository],
) -> None:
    """The list surface, which is scoped in the query rather than after it.

    Both forms are probed. The unfiltered list is the obvious one; the
    `conversationId` narrowing is the one worth probing, because a conversation
    id is guessable and a lookup that resolved it *before* checking ownership
    would have already read the case by the time it decided not to return it.
    """
    http, identity, repository = probe
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    case_id = await _owned_case(repository, conversation_id=conversation_id)

    identity.become(
        tenant_id=OWNER_TENANT,
        principal_id=OWNER_PRINCIPAL,
        roles=frozenset({r.RETURN_ASSOCIATE}),
    )
    mine = await http.get("/api/cases")
    assert mine.status_code == 200, mine.text
    # Membership rather than equality: the fixture is module-scoped, so the
    # owner legitimately accumulates cases from the other probes in this file.
    assert case_id in [row["caseId"] for row in mine.json()["data"]]
    narrowed = await http.get("/api/cases", params={"conversationId": conversation_id})
    assert [row["caseId"] for row in narrowed.json()["data"]] == [case_id]

    for tenant_id, principal_id in (
        (OTHER_TENANT, OTHER_PRINCIPAL),
        (OTHER_TENANT, OWNER_PRINCIPAL),
        (OWNER_TENANT, OTHER_PRINCIPAL),
    ):
        identity.become(
            tenant_id=tenant_id, principal_id=principal_id, roles=frozenset({r.CONSOLE_ADMIN})
        )
        listed = await http.get("/api/cases")
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"] == [], f"{tenant_id}/{principal_id} listed another's case"

        by_conversation = await http.get("/api/cases", params={"conversationId": conversation_id})
        assert by_conversation.status_code == 200, by_conversation.text
        assert by_conversation.json()["data"] == [], (
            f"{tenant_id}/{principal_id} reached another owner's case through its conversation id"
        )


# --- Unauthorized proposal approval ------------------------------------------


async def test_approving_a_proposal_without_the_capability_is_refused_at_the_route(
    probe: tuple[httpx.AsyncClient, _Identity, OperationalRepository],
) -> None:
    """`require_capability` as a request reaches it, not as a function call.

    `test_capability_model.py` proves the dependency refuses a principal that
    lacks the grant. What it cannot prove is that the dependency is *mounted* on
    the route -- a governance endpoint that forgot its `Depends` would satisfy
    every one of those tests and approve anything.

    The 403 arrives before the handler needs a proposal service, which is the
    point: refusal is the first thing that happens, not something the handler
    decides after loading the proposal.
    """
    http, identity, _ = probe
    proposal_id = str(uuid.uuid4())

    without_grant = frozenset({r.RETURN_ASSOCIATE})
    assert caps.GOVERNANCE_PROPOSAL_APPROVE not in capabilities_for_roles(without_grant), (
        "the fixture role would have to be changed; this probe needs a role without the grant"
    )
    identity.become(tenant_id=OWNER_TENANT, principal_id=OWNER_PRINCIPAL, roles=without_grant)

    for path in (
        f"/api/proposals/{proposal_id}/approve",
        f"/api/proposals/{proposal_id}/reject",
        f"/api/proposals/{proposal_id}/activate",
    ):
        response = await http.post(path, json={})
        assert response.status_code == 403, (
            f"{path} was not gated by its capability: {response.status_code} {response.text}"
        )

    # Reading is a separate grant, and it is enforced too.
    listed = await http.get("/api/proposals")
    assert listed.status_code == 403, listed.text


async def test_a_granted_principal_is_not_refused_by_the_capability_gate(
    probe: tuple[httpx.AsyncClient, _Identity, OperationalRepository],
) -> None:
    """The control for the refusal above.

    Only that the *gate* lets it through -- whatever the handler then does with
    a proposal id that does not exist is the handler's business and not this
    probe's. Without this, a route that refused everyone unconditionally would
    pass the test above.
    """
    http, identity, _ = probe
    proposal_id = str(uuid.uuid4())

    identity.become(
        tenant_id=OWNER_TENANT, principal_id=OWNER_PRINCIPAL, roles=frozenset({r.CONSOLE_ADMIN})
    )
    response = await http.post(f"/api/proposals/{proposal_id}/approve", json={})
    assert response.status_code not in (401, 403), (
        f"a principal holding {caps.GOVERNANCE_PROPOSAL_APPROVE} was refused: "
        f"{response.status_code} {response.text}"
    )


async def test_an_unauthenticated_request_is_401_and_an_ungranted_one_is_403(
    probe: tuple[httpx.AsyncClient, _Identity, OperationalRepository],
) -> None:
    """Unauthenticated and unauthorized stay distinguishable at the route.

    An endpoint that answered 404 to an anonymous caller would be safe and
    undebuggable; one that answered 403 would be claiming the caller is known.
    `require_roles` and `require_capability` agree on 401 for an absent
    principal and 403 for a known one lacking the grant, and both routers must
    keep the distinction that the dependencies draw.
    """
    http, identity, repository = probe
    case_id = await _owned_case(repository, conversation_id=f"conv-{uuid.uuid4().hex[:12]}")

    identity.become(tenant_id=OWNER_TENANT, principal_id=OWNER_PRINCIPAL, roles=None)
    assert (await http.get(f"/api/cases/{case_id}")).status_code == 401
    assert (await http.get("/api/proposals")).status_code == 401

    # Authenticated, and holding a role that grants neither surface.
    ungranted = frozenset({r.WAREHOUSE_ASSOCIATE})
    assert caps.GOVERNANCE_PROPOSAL_READ not in capabilities_for_roles(ungranted)
    identity.become(tenant_id=OWNER_TENANT, principal_id=OWNER_PRINCIPAL, roles=ungranted)
    assert (await http.get("/api/proposals")).status_code == 403


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SECV-ORDER-01, reported to the Orchestrator. Every governance route declares "
        "`kernel: _Kernel` ahead of `Depends(require_capability(...))`, so FastAPI "
        "resolves `resolve_proposal_kernel` first and its 503 GOVERNANCE_UNAVAILABLE "
        "pre-empts the 403. An unauthorized caller therefore learns whether the "
        "proposal kernel is loaded in the process before authorization is consulted. "
        "Low severity -- it discloses composition state, not data, and does not arise "
        "when the kernel is wired -- but authorization should be the first gate. "
        "Application defect; Track I does not fix application logic. Remove this "
        "marker when the capability dependency is declared first."
    ),
)
async def test_authorization_is_refused_before_service_availability(
    probe: tuple[httpx.AsyncClient, _Identity, OperationalRepository],
) -> None:
    """An unauthorized caller must learn nothing about the process's composition.

    The kernel override is removed for this probe alone, which reproduces the
    deployment state the ordering actually matters in: a process where
    governance is not composed. The caller holds no governance grant, so the
    only correct answer is 403.
    """
    http, identity, _ = probe
    app = http._transport.app  # type: ignore[attr-defined]
    identity.become(
        tenant_id=OWNER_TENANT,
        principal_id=OWNER_PRINCIPAL,
        roles=frozenset({r.RETURN_ASSOCIATE}),
    )
    override = app.dependency_overrides.pop(resolve_proposal_kernel)
    try:
        response = await http.post(f"/api/proposals/{uuid.uuid4()}/approve", json={})
        assert response.status_code == 403, (
            f"authorization was decided after service availability: "
            f"{response.status_code} {response.text}"
        )
    finally:
        app.dependency_overrides[resolve_proposal_kernel] = override


# --- Credential-to-response sweep --------------------------------------------


async def test_no_route_response_carries_credential_material(
    probe: tuple[httpx.AsyncClient, _Identity, OperationalRepository],
) -> None:
    """The server-side half of the credential sweep.

    Track H scanned the rendered Data Sources screen for secrets in the DOM.
    This asks the complementary question at the other end of the wire: does any
    response *body* carry the material in the first place. A screen that happens
    not to render a secret it was sent is one component change away from
    rendering it, so the boundary worth holding is the response.

    The values are read from the live `Settings` rather than matched by pattern,
    so this cannot pass by the secret simply not looking like a secret.
    """
    http, identity, repository = probe
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    case_id = await _owned_case(repository, conversation_id=conversation_id)
    identity.become(
        tenant_id=OWNER_TENANT, principal_id=OWNER_PRINCIPAL, roles=frozenset({r.CONSOLE_ADMIN})
    )

    secrets = {
        name: value
        for name, value in (
            ("MONGO_ROOT_PASSWORD", os.getenv("MONGO_ROOT_PASSWORD")),
            ("GRAPH_PASSWORD", os.getenv("GRAPH_PASSWORD")),
            ("VALKEY_PASSWORD", os.getenv("VALKEY_PASSWORD")),
            ("MSSQL_SA_PASSWORD", os.getenv("MSSQL_SA_PASSWORD")),
            ("NVIDIA_API_KEY", os.getenv("NVIDIA_API_KEY")),
            ("GOOGLE_API_KEY", os.getenv("GOOGLE_API_KEY")),
        )
        # A placeholder is not a secret, and asserting on one would make the
        # sweep pass or fail on how the runner was invoked.
        if value and len(value) >= 8 and not value.startswith("placeholder")
    }
    assert secrets, "no real credential was available to sweep for"

    responses = {
        "GET /api/cases": await http.get("/api/cases"),
        f"GET /api/cases/{case_id}": await http.get(f"/api/cases/{case_id}"),
        "GET /api/cases?conversationId": await http.get(
            "/api/cases", params={"conversationId": conversation_id}
        ),
        "GET /api/proposals": await http.get("/api/proposals"),
    }

    for label, response in responses.items():
        body = response.text
        for name, value in secrets.items():
            assert value not in body, f"{label} returned the value of {name}"
        # The DSN carries the password inline, so its presence is the same leak
        # under a different shape.
        assert "mongodb://" not in body, f"{label} returned a Mongo connection string"
        assert "authSource=admin" not in body, f"{label} returned Mongo connection parameters"
