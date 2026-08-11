"""Frozen modules may keep the callers they have. They may not gain new ones.

Two complete Order Discovery implementations exist in this codebase, and the
console reaches only one of them. Until the losing side is removed -- which waits
on consumer analysis and the functional cutover gate, because "no caller found"
is not "no caller exists" -- the risk is not that the old code runs. It is that
new work is built against it by accident, because nothing says which one is
canonical at the point where an import is written.

So this test pins the *exact* set of allowed importers. Adding a caller fails
here with the canonical replacement named in the message. Removing one is a
migration, and the expected set shrinks with it -- deliberately not a `<=`
assertion, because a silently shrinking allowlist hides a deletion nobody
reviewed.

This is an architecture rule, not a coverage test. It reads source text rather
than importing anything, so it stays fast and cannot be defeated by a module
that fails to import.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

# module -> (canonical replacement, exact set of modules permitted to import it)
FROZEN: dict[str, tuple[str, frozenset[str]]] = {
    "return_platform.operations.associate_flow": (
        "return_platform.dynamic_knowledge.order_agent (behind /api/v2/order-agent)",
        frozenset(
            {
                "return_platform/api/associate_returns.py",
                "return_platform/operations/associate_service_factory.py",
            }
        ),
    ),
    "return_platform.api.associate_returns": (
        "return_platform.dynamic_knowledge.api.order_agent",
        frozenset({"return_platform/main.py"}),
    ),
    "return_platform.api.return_agents": (
        "the durable Temporal workflows and /api/v2/order-agent",
        frozenset({"return_platform/main.py"}),
    ),
    "return_platform.agents.order_discovery": (
        "return_platform.dynamic_knowledge.order_agent",
        frozenset({"return_platform/agents/registry/registry.py"}),
    ),
    "return_platform.data_platform.graph.interim_active_schema": (
        "config/dynamic_knowledge/active-schema.return-order.yaml via load_active_schema",
        frozenset(),
    ),
}

# Frozen HTTP surfaces. No frontend module may call these.
FROZEN_ROUTE_PREFIXES: tuple[str, ...] = (
    "/api/v1/associate-returns",
    "/api/v1/return-agents",
)


def _module_key(path: Path) -> str:
    return path.relative_to(BACKEND_SRC).as_posix()


def _own_file(module: str) -> str:
    return f"{module.replace('.', '/')}.py"


def _importers(module: str) -> set[str]:
    """Files under backend/src that import `module`, excluding the module itself.

    Matches `from x.y import`, `import x.y`, and `from x.y.z import` for
    submodules, but not a longer sibling name that merely starts with the same
    text -- `\\b` alone would let `associate_flow_v2` pass as `associate_flow`.
    """
    pattern = re.compile(
        rf"^\s*(?:from\s+{re.escape(module)}(?:\.\w+)*\s+import\b|import\s+{re.escape(module)}(?:\.\w+)*(?:\s|$))",
        re.MULTILINE,
    )
    own = _own_file(module)
    found: set[str] = set()
    for path in BACKEND_SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        key = _module_key(path)
        if key == own:
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            found.add(key)
    return found


@pytest.mark.parametrize(
    ("module", "replacement", "expected"),
    [(module, replacement, expected) for module, (replacement, expected) in FROZEN.items()],
)
def test_frozen_module_has_exactly_its_known_importers(
    module: str, replacement: str, expected: frozenset[str]
) -> None:
    actual = _importers(module)

    added = sorted(actual - expected)
    assert not added, (
        f"{module} is FROZEN and gained a new importer: {added}. "
        f"Build against {replacement} instead. If this import is genuinely "
        f"required for a migration step, add it to FROZEN in this file with a "
        f"note saying why."
    )

    removed = sorted(expected - actual)
    assert not removed, (
        f"{module} lost importers {removed} -- if that was a deliberate "
        f"migration, shrink the expected set here in the same change so the "
        f"removal is reviewed rather than absorbed silently."
    )


@pytest.mark.parametrize("prefix", FROZEN_ROUTE_PREFIXES)
def test_no_frontend_module_calls_a_frozen_route(prefix: str) -> None:
    if not FRONTEND_SRC.is_dir():  # pragma: no cover - backend-only checkouts
        pytest.skip("frontend sources are not present in this checkout")

    callers = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in FRONTEND_SRC.rglob("*.ts*")
        # `src/api/generated/` is derived from the served contract, so it names
        # every mounted path by construction -- including the frozen ones, which
        # stay mounted deliberately so an unknown consumer is not broken without
        # warning. Naming a type is not calling a route; only hand-written code
        # counts here.
        if "generated" not in path.parts
        and prefix in path.read_text(encoding="utf-8", errors="replace")
    )
    assert not callers, (
        f"{prefix} is a frozen HTTP surface and is now called from {callers}. "
        "The console must reach discovery through /api/v2/order-agent."
    )
