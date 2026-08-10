"""Phase 17 -- the role/capability model and the shim over it."""

from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest
from fastapi import HTTPException, Request

from return_platform.security import authorization
from return_platform.security import capabilities as caps
from return_platform.security import roles as r
from return_platform.security.authorization import require_capability, require_roles


class _StubState:
    def __init__(self, principal: object | None) -> None:
        self.principal = principal


class _StubRequest:
    def __init__(self, principal: object | None) -> None:
        self.state = _StubState(principal)


class _StubPrincipal:
    def __init__(self, subject: str, roles: frozenset[str], authenticated: bool = True) -> None:
        self.subject = subject
        self.roles = roles
        self.is_authenticated = authenticated


def _request_for(*role_names: str) -> Request:
    principal = _StubPrincipal("someone", frozenset(role_names))
    return _StubRequest(principal)  # type: ignore[return-value]


# --- The move preserved the model exactly ------------------------------------


def test_role_dependencies_still_annotate_request() -> None:
    """FastAPI injects `Depends(require_read_roles)` by inspecting the annotation.

    Widening or dropping it breaks injection at runtime in every importer while
    still importing and type-checking cleanly, so it is asserted rather than
    trusted.

    Retargeted in Wave F5: these lived in `data_console/api/auth.py`, a Phase 17
    shim whose docstring said Wave F would sweep it away. It did. The companion
    test that checked the shim re-exported the canonical role sets went with it
    -- there is no second definition left to diverge.
    """
    for name in (
        "require_read_roles",
        "require_write_roles",
        "require_admin_roles",
        "require_associate_roles",
        "require_support_roles",
        "require_return_collaboration_roles",
        "require_logistics_roles",
        "require_warehouse_roles",
        "require_audit_roles",
    ):
        function = getattr(authorization, name)
        assert list(inspect.signature(function).parameters) == ["request"]
        # Resolved, not compared as source text: the module uses
        # `from __future__ import annotations`, so the raw annotation is the
        # string "Request". FastAPI resolves it the same way this does, and a
        # string comparison would pass even if the name resolved to something
        # else entirely.
        assert get_type_hints(function)["request"] is Request


# --- Capability derivation ----------------------------------------------------


def test_every_mapped_role_is_a_real_role() -> None:
    assert set(caps._ROLE_CAPABILITIES) <= set(r.ALL_ROLES)


def test_every_granted_capability_is_declared() -> None:
    for role, granted in caps._ROLE_CAPABILITIES.items():
        unknown = granted - caps.ALL_CAPABILITIES
        assert not unknown, f"{role} grants undeclared capabilities: {sorted(unknown)}"


def test_every_role_grants_something() -> None:
    """A role that grants nothing is either dead or an oversight; both need a look."""
    for role in r.ALL_ROLES:
        assert caps.capabilities_for_roles([role]), f"{role} grants no capability"


def test_capabilities_union_across_roles() -> None:
    both = caps.capabilities_for_roles([r.RETURN_SUPPORT, r.WAREHOUSE_ASSOCIATE])
    assert caps.RETURNS_SUPPORT_ACT in both
    assert caps.RETURNS_WAREHOUSE_ACT in both


def test_unknown_roles_contribute_nothing_but_do_not_raise() -> None:
    granted = caps.capabilities_for_roles(["some_upstream_role", r.RETURN_ASSOCIATE])
    assert granted == caps.capabilities_for_roles([r.RETURN_ASSOCIATE])


def test_console_admin_holds_every_capability() -> None:
    assert caps.capabilities_for_roles([r.CONSOLE_ADMIN]) == caps.ALL_CAPABILITIES


def test_replay_read_is_not_bundled_into_ordinary_read_access() -> None:
    """Phase 14 gates the replay envelope separately.

    It reproduces the original request including any source rows the prompt
    carried, which is a different question from "may this person see that a
    request happened".
    """
    for role in (r.CONSOLE_VIEWER, r.WORKSPACE_VIEWER, r.RETURN_AUDITOR):
        granted = caps.capabilities_for_roles([role])
        assert caps.AI_REQUEST_READ in granted
        assert caps.AI_REPLAY_READ not in granted


def test_service_account_cannot_exercise_operator_judgment() -> None:
    """The platform's own calls must not promote, activate, or answer for a human."""
    granted = caps.capabilities_for_roles([r.RETURN_PLATFORM_SERVICE])
    assert caps.CONFIG_RELEASE_PROMOTE not in granted
    assert caps.GRAPH_SCHEMA_GENERATION_ACTIVATE not in granted
    assert caps.AI_INTERCEPTION_ACT not in granted


# --- Enforcement --------------------------------------------------------------


def test_require_capability_allows_a_granted_principal() -> None:
    dependency = require_capability(caps.RETURNS_SUPPORT_ACT)
    assert dependency(_request_for(r.RETURN_SUPPORT)) == "someone"


def test_require_capability_refuses_a_principal_without_it() -> None:
    dependency = require_capability(caps.CONFIG_RELEASE_PROMOTE)
    with pytest.raises(HTTPException) as excinfo:
        dependency(_request_for(r.RETURN_SUPPORT))
    assert excinfo.value.status_code == 403


def test_missing_principal_is_401_not_403() -> None:
    """Unauthenticated and unauthorized are different answers.

    Both `require_roles` and `require_capability` must agree, or moving a route
    from one to the other changes how refusal is reported.
    """
    anonymous = _StubRequest(None)
    for dependency in (
        require_capability(caps.RETURNS_SESSION_READ),
        require_roles(r.READ_ROLES),
    ):
        with pytest.raises(HTTPException) as excinfo:
            dependency(anonymous)  # type: ignore[arg-type]
        assert excinfo.value.status_code == 401


def test_unauthenticated_principal_is_401() -> None:
    principal = _StubPrincipal("someone", frozenset({r.CONSOLE_ADMIN}), authenticated=False)
    with pytest.raises(HTTPException) as excinfo:
        require_capability(caps.RETURNS_SESSION_READ)(_StubRequest(principal))  # type: ignore[arg-type]
    assert excinfo.value.status_code == 401
