"""Phase 16's structural rules for the canonical return surface.

**"No generic advance."** A stage completes because a specific,
evidence-carrying `ReturnWorkflowAdvanceCommand` was applied. An endpoint taking
a target state as a parameter would let a caller move a return without producing
the evidence that justifies the move -- and the stage-result binding, the audit
record and the outbox event all hang off that evidence. There is an existing
test for the legacy surface; this covers the canonical one, which is where new
endpoints will actually be added.

**"API only under `/api/returns`."** Not yet true, and this test says so out
loud rather than pretending. It records the nine routers currently serving the
return domain so the number cannot silently grow while consolidation is in
progress -- adding a tenth fails here and makes someone justify it.

(The count started as eight in an earlier draft of this file. The test caught
`return_agents.py`, which is precisely the kind of surface a hand-written
inventory misses -- and the reason the inventory is executable rather than
prose.)
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"
_CANONICAL = _SRC / "api" / "canonical_returns.py"

#: Path fragments that would mean "move this return to a state I name".
_GENERIC_ADVANCE = ("advance", "transition", "set-state", "set_status", "force-stage")

#: The return-domain routers as of Phase 16 slice 1, with the prefix each
#: serves. Consolidating them is Wave F's deletion work; this snapshot exists so
#: the set cannot grow unnoticed in the meantime.
_KNOWN_RETURN_ROUTERS = {
    "returns.py": "/api/v1/returns",
    "physical_operations.py": "/api/v1/returns",
    "return_artifacts.py": "/api/v1/returns",
    "return_support.py": "/api/v1/return-support",
    "production_workflow.py": "/api/v1/production-returns",
    "warehouse_placement.py": "/api/v1/warehouse/returns",
    "integration_outbox.py": "/api/v1/integration-outbox",
    "associate_returns.py": "/api/v1/associate-returns",
    "return_agents.py": "/api/v1/return-agents",
    "canonical_returns.py": "/api/returns",
}


def _route_paths(path: Path) -> list[str]:
    """Decorator paths on `@router.<method>(...)`, by AST rather than regex so a
    string that merely looks like a route is not counted."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    paths: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id != "router":
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                value = decorator.args[0].value
                if isinstance(value, str):
                    paths.append(value)
    return paths


def test_the_canonical_surface_has_no_generic_advance() -> None:
    offenders = [
        route
        for route in _route_paths(_CANONICAL)
        if any(fragment in route.lower() for fragment in _GENERIC_ADVANCE)
    ]
    assert offenders == [], (
        "a generic advance endpoint lets a caller move a return without the "
        f"evidence that justifies the move: {offenders}"
    )


def test_the_canonical_surface_is_read_only_while_duplicates_are_unresolved() -> None:
    """The plan says resolve duplicate implementations *before* deleting
    anything. Publishing canonical writes first would add a ninth way to mutate
    a return rather than replacing eight."""
    tree = ast.parse(_CANONICAL.read_text(encoding="utf-8"), filename=str(_CANONICAL))
    methods = {
        decorator.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "router"
    }
    assert methods <= {"get"}, f"canonical returns API is read-only for now, found {methods}"


def test_the_number_of_return_routers_has_not_grown() -> None:
    """Consolidation means this set shrinks. If it grows, something added a new
    surface instead of using the canonical one -- which is the failure this
    whole phase exists to stop."""
    found: dict[str, str] = {}
    for path in (_SRC / "api").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "return" not in path.name and "warehouse" not in path.name:
            continue
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "APIRouter":
                continue
            for keyword in node.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    found[path.name] = str(keyword.value.value)

    unexpected = {
        name: prefix for name, prefix in found.items() if name not in _KNOWN_RETURN_ROUTERS
    }
    assert unexpected == {}, (
        "a new return-domain router appeared; new endpoints belong on the canonical "
        f"/api/returns surface: {unexpected}"
    )


def test_three_routers_still_share_the_legacy_returns_prefix() -> None:
    """Recorded as a fact, not aspiration. `returns.py`, `physical_operations.py`
    and `return_artifacts.py` all mount `/api/v1/returns`, so the owning module
    for a legacy path is not derivable from the path. When Wave F fixes that,
    this test should fail and be deleted -- which is the point of writing it."""
    sharing = sorted(
        name for name, prefix in _KNOWN_RETURN_ROUTERS.items() if prefix == "/api/v1/returns"
    )
    assert sharing == ["physical_operations.py", "return_artifacts.py", "returns.py"]
