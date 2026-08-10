"""Both processes resolve MANUAL to the same provider.

Wave D2's last wiring gap. `build_routes(settings, interception_store=...)`
decides which MANUAL implementation the route pool gets: with a store, the
durable `DurableInterceptionProvider`; without one, the filesystem
`ManualFileProvider` that writes held prompts to disk.

The order-discovery worker bootstrapped the SystemStore and *then* built routes,
so it got the durable one. The API process built routes at `main.py:582` and
bootstrapped the SystemStore ~50 lines later, so it got the filesystem one --
silently, because both are valid providers and nothing compared them. Two
processes, two different MANUAL implementations, one of them writing prompts to
local disk in a container that does not persist.

This is an ordering property, and ordering is exactly what a later edit
reintroduces without noticing. Hence a test on the source rather than only on
behaviour: behaviour here needs a live Mongo, a bootstrapped SystemStore and a
full app startup to observe something a five-line AST walk states directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "return_platform"
_MAIN = _SRC / "main.py"
_WORKER = Path(__file__).resolve().parents[1] / "scripts" / "run_order_discovery_worker.py"


def _first_line_calling(tree: ast.AST, function_name: str) -> int | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name == function_name:
            return node.lineno
    return None


def _build_routes_call(tree: ast.AST) -> ast.Call:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_routes"
        ):
            return node
    raise AssertionError("build_routes is not called")


def test_the_api_process_bootstraps_the_store_before_building_routes() -> None:
    """The regression itself. If `build_routes` moves back above
    `bootstrap_system_store`, the store argument is `None` at construction time
    and MANUAL silently reverts to the filesystem provider."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"), filename=str(_MAIN))

    bootstrap_line = _first_line_calling(tree, "bootstrap_system_store")
    routes_line = _first_line_calling(tree, "build_routes")

    assert bootstrap_line is not None, "main.py no longer bootstraps the system store"
    assert routes_line is not None, "main.py no longer builds routes"
    assert bootstrap_line < routes_line, (
        f"bootstrap_system_store (line {bootstrap_line}) must precede build_routes "
        f"(line {routes_line}), or MANUAL resolves to the filesystem provider"
    )


def test_the_api_process_passes_an_interception_store_to_build_routes() -> None:
    """Ordering alone is not enough -- the store has to actually be handed over.
    A `build_routes(settings)` after a successful bootstrap would order correctly
    and still produce the filesystem provider."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"), filename=str(_MAIN))

    call = _build_routes_call(tree)

    assert any(keyword.arg == "interception_store" for keyword in call.keywords), (
        "main.py builds routes without an interception_store; MANUAL will be the "
        "filesystem provider even though the SystemStore exists"
    )


def test_the_worker_passes_one_too() -> None:
    """The process that was already correct, pinned so the two cannot diverge
    again from the other direction.

    Skipped rather than failed when `scripts/` is absent: the real-infra runner
    copies only `src`, `tests` and `config` into the container, so this file
    exists on the host and not in the container run. Asserting on a file that is
    not there would make the container suite red for a packaging detail.
    """
    if not _WORKER.exists():
        pytest.skip(f"{_WORKER.name} is not in this run's copy of the tree")
    tree = ast.parse(_WORKER.read_text(encoding="utf-8"), filename=str(_WORKER))

    call = _build_routes_call(tree)

    assert any(keyword.arg == "interception_store" for keyword in call.keywords)


def test_the_store_is_bootstrapped_exactly_once_in_the_api_process() -> None:
    """It used to be built twice -- once for the route pool would have been new,
    and once for the analyzer. Two `SystemStoreBootstrapper` runs in one process
    means two owner ids contending for the same bootstrap lease, which is a real
    cost for something that only needs doing once."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"), filename=str(_MAIN))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "bootstrap_system_store"
    ]

    assert len(calls) == 1, f"expected one bootstrap, found {len(calls)}"
