"""`/api/config`'s domain coverage, including the domains that need no endpoint.

Wave D3's last item. The plan lists six configuration domains for this surface:
sources, integrations, business config, modules, security and audit. Checked
against what actually backs each, only two needed building.

That conclusion is the fragile part. "Business config is already served by
`/runtime`" is true right now and stops being true the moment someone narrows
what `/runtime` returns — at which point a reader of the plan finds a domain
listed as covered with nothing covering it. So the already-served ones are
pinned here, and the deliberately-absent ones are recorded with their reason,
rather than left as a silence that looks like an oversight.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.configuration.api.router import router
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.snapshot import PinnedConfigurationSnapshot
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_ROUTER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "return_platform"
    / "configuration"
    / "api"
    / "router.py"
)


def _snapshot() -> PinnedConfigurationSnapshot:
    loaded = load_return_configuration(Path("config/returns/production.yaml"))
    return PinnedConfigurationSnapshot(
        release_id="r-1",
        head_revision=1,
        checksum_sha256="a" * 64,
        source="test",
        configuration=loaded.configuration,
    )


def _client(*, with_snapshot: bool = True) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=frozenset({r.CONSOLE_VIEWER}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    if with_snapshot:
        app.state.return_configuration_snapshot = _snapshot()
    with TestClient(app) as client:
        yield client


def _route_paths() -> set[str]:
    tree = ast.parse(_ROUTER_SOURCE.read_text(encoding="utf-8"), filename=str(_ROUTER_SOURCE))
    return {
        decorator.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "router"
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
    }


# ---------------------------------------------------------------------------
# The domains that needed an endpoint
# ---------------------------------------------------------------------------


def test_sources_and_audit_are_canonical() -> None:
    paths = _route_paths()

    assert {"/sources", "/sources/{source_id}", "/audit", "/audit/{audit_id}"} <= paths


def test_the_new_reads_delegate_rather_than_reimplement() -> None:
    """A canonical *surface* over one implementation, not a second one. If a
    handler stops calling the console function and grows its own probe fan-out
    or Mongo query, there are two implementations to keep correct -- which is
    the thing this whole wave exists to remove."""
    source = _ROUTER_SOURCE.read_text(encoding="utf-8")

    for delegate in (
        "console_get_sources",
        "console_get_source",
        "console_list_audit_logs",
        "console_get_audit_log",
    ):
        assert f"await {delegate}(" in source, f"{delegate} is no longer delegated to"


# ---------------------------------------------------------------------------
# The domains already served by /runtime
# ---------------------------------------------------------------------------


def test_runtime_carries_the_business_configuration() -> None:
    """ "Business config is already served" is only true while `/runtime` returns
    the whole snapshot. Narrowing it would leave a planned domain uncovered with
    nothing failing."""
    for client in _client():
        data = client.get("/api/config/runtime").json()["data"]

    assert "configuration" in data
    assert "agents" in data["configuration"]
    assert "return_policy" in data["configuration"]


def test_runtime_carries_the_integrations_configuration() -> None:
    """Both of them -- the business integrations block and the runtime one.
    A separate `/integrations` endpoint would have re-sliced this."""
    for client in _client():
        configuration = client.get("/api/config/runtime").json()["data"]["configuration"]

    assert "integrations" in configuration
    assert "runtime_integrations" in configuration


def test_runtime_is_503_when_no_snapshot_is_loaded() -> None:
    """Not an empty object. A process with no configuration loaded is not a
    process running empty configuration."""
    for client in _client(with_snapshot=False):
        assert client.get("/api/config/runtime").status_code == 503


# ---------------------------------------------------------------------------
# The domains deliberately absent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("absent", ["/modules", "/security"])
def test_the_unbacked_domains_have_no_shell_endpoint(absent: str) -> None:
    """`modules` would return `[]` forever -- `_kernel_module_registry` is empty
    by design -- and `security` is not configuration: the role model is code, and
    publishing the role-to-capability table would let a UI reimplement
    authorization locally, which the capability layer exists to prevent.

    An endpoint that always answers nothing is worse than an honest absence,
    because it reads as "this domain is covered".
    """
    assert absent not in _route_paths()


def test_the_reasoning_for_each_planned_domain_is_recorded() -> None:
    """The decision above is only useful if the next reader finds it. This fails
    if the explanatory block is deleted, which is the realistic way that
    reasoning gets lost."""
    source = _ROUTER_SOURCE.read_text(encoding="utf-8")

    for domain in ("business config", "integrations", "modules", "security", "sources", "audit"):
        assert domain in source, f"no recorded reasoning for the {domain!r} domain"
