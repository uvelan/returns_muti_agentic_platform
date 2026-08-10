"""Wave F3: `main.py` is a composition root, not a place things get done.

The plan's F3 says the final `main.py` contains only settings, core platform
bootstrap, SystemStore bootstrap, canonical configuration, module activation,
router mounting and lifespan -- and explicitly "no business logic / provider
selection / source-specific logic".

**Measured, that criterion already holds**, which is why this file is tests
rather than a refactor. What makes `main.py` long is 42 router imports and their
mounts, plus the bootstrap sequence; the mounts are the thing F3's own list says
belongs here, and the count shrinks when F1/F2/F5 delete the routers they are
mounting. Restructuring the bootstrap now would be churn that those waves undo.

So the useful work is holding the criterion. Each test below is a property that
was true when checked and would be silently easy to break: a handler added here
"just for now", a query run directly against a datastore during bootstrap, or a
branch on which kind of source a deployment uses.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MAIN = Path(__file__).resolve().parents[2] / "src" / "return_platform" / "main.py"
_TREE = ast.parse(_MAIN.read_text(encoding="utf-8"), filename=str(_MAIN))


def _decorated_routes() -> list[tuple[str, str]]:
    """`@fastapi_app.get("/x")` and friends, as (method, path)."""
    routes: list[tuple[str, str]] = []
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                continue
            method = decorator.func.attr
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                routes.append((method, str(decorator.args[0].value)))
    return routes


def test_the_only_endpoints_defined_here_are_the_health_probes() -> None:
    """Everything else is mounted, not written.

    A handler defined in the composition root is one that no module owns, that
    no domain test covers, and that Wave F's "prove zero consumers before
    deleting" scan will not find by looking at routers.
    """
    assert sorted(_decorated_routes()) == [
        ("get", "/health/live"),
        ("get", "/health/ready"),
    ]


def test_no_datastore_query_runs_in_the_composition_root() -> None:
    """Bootstrap connects to datastores; it must not read or write business data.

    A `find_one` here would be business logic that runs before the application
    is assembled, in a file with no domain owner -- and it would run on every
    process start, including the workers that import this module's helpers.
    """
    query_methods = {
        "find",
        "find_one",
        "find_one_and_update",
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "replace_one",
        "delete_one",
        "delete_many",
        "aggregate",
    }
    offenders = sorted(
        {
            f"{node.func.attr} (line {node.lineno})"
            for node in ast.walk(_TREE)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in query_methods
        }
    )
    assert offenders == [], f"main.py queries a datastore directly: {offenders}"


def test_nothing_branches_on_which_kind_of_source_a_deployment_uses() -> None:
    """ "No source-specific logic."

    The composition root wires whatever the configuration names. A comparison
    against `"MONGODB"` or `"SQLSERVER"` here would mean a deployment's source
    technology had leaked into startup, which is the thing the connector
    framework and the system-store manifest exist to keep out. Checked as string
    *constants* rather than by grepping, so the explanatory comments about the
    MANUAL provider's bootstrap ordering do not trip it.
    """
    source_kinds = {"MONGODB", "SQLSERVER", "SQL_SERVER", "POSTGRES", "NEO4J_SOURCE"}
    offenders = sorted(
        {
            f"{node.value!r} (line {node.lineno})"
            for node in ast.walk(_TREE)
            if isinstance(node, ast.Constant) and node.value in source_kinds
        }
    )
    assert offenders == [], f"main.py branches on a source technology: {offenders}"


def test_router_mounting_is_the_bulk_of_create_app_and_that_is_allowed() -> None:
    """Recorded so the size of this file is not mistaken for a violation.

    F3's target list names router mounting as something that belongs here. The
    count is the honest measure of how much of Wave F is left: every router F1,
    F2 and F5 delete comes off this number, and when it stops shrinking the
    cutover is done.
    """
    mounts = [
        node
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
    ]
    # Not a target, a reading. Update it when Wave F deletes routers -- the
    # update is the evidence that a deletion actually reached the composition
    # root rather than leaving a dangling mount.
    assert len(mounts) == 42, (
        f"{len(mounts)} routers are mounted, expected 42; if Wave F deleted one, "
        "update this number in the same commit"
    )
