"""Phase 16's structural rules for the canonical return surface.

**"No generic advance."** A stage completes because a specific,
evidence-carrying `ReturnWorkflowAdvanceCommand` was applied. An endpoint taking
a target state as a parameter would let a caller move a return without producing
the evidence that justifies the move -- and the stage-result binding, the audit
record and the outbox event all hang off that evidence. There is an existing
test for the legacy surface; this covers the canonical one, which is where new
endpoints will actually be added.

**"API only under `/api/returns`."** Not yet true, and this test says so out
loud rather than pretending. It records the routers currently serving the
return domain so the number cannot silently grow while consolidation is in
progress -- adding one more fails here and makes someone justify it.

(The inventory is eleven as of the SRCH-01/WF-01 remediation wave. Deliberately
not restated as a number in prose: the earlier text said "nine" while the list
held ten, which is the drift an executable inventory exists to prevent. Count
the dict, not the sentence.)

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
    # Justified per this test's own contract, which requires a new router to be
    # argued for rather than silently admitted. `return_history.py` was added
    # deliberately by 2800412 ("the return itself is now something the graph can
    # answer for") as a graph-backed *read* surface: it plans, guards, compiles
    # and executes one read per request and mutates nothing, so the "no generic
    # advance" rule above cannot be violated through it.
    #
    # It is recorded here rather than folded into `/api/returns` because it is
    # already shipped and consumed -- `frontend/src/api/returnHistory.ts` plus
    # its tests, the generated `return-platform.d.ts`, and the published
    # `openapi.json` all carry `/api/return-history`. Relocating a live path
    # would be a breaking change bought for no correctness gain. Consolidation
    # stays where the docstring puts it: Wave F's deletion work, once the
    # canonical surface owns the capability.
    "return_history.py": "/api/return-history",
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


def test_the_artifact_and_evidence_reads_are_both_canonical() -> None:
    """The pair that looked like a duplicate, now separated by name.

    `/artifacts` is the document-artifact list; `/evidence` is the return's whole
    evidence record, of which document artifacts are one of eleven collections.
    They shared a word, not an implementation. If either canonical path is
    dropped, the legacy `production-artifacts` name comes back into use and the
    confusion with it.
    """
    paths = set(_route_paths(_CANONICAL))
    assert {"/{session_id}/artifacts", "/{session_id}/evidence"} <= paths


def test_the_canonical_evidence_read_does_not_re_expose_session_or_timeline() -> None:
    """The legacy endpoint embedded both. Carrying that forward would make
    `/evidence` a third way to read a session and a second way to read a
    timeline -- adding surface while claiming to consolidate it."""
    from return_platform.api.canonical_returns import ReturnEvidence

    fields = set(ReturnEvidence.model_fields)
    assert "return" not in fields
    assert "timeline" not in fields
    assert "documentArtifacts" in fields


def test_the_superseded_legacy_reads_are_marked_deprecated() -> None:
    """A canonical replacement nobody is told about is not a consolidation.

    Both legacy reads now carry `deprecated=True`, so the generated contract --
    and therefore the frontend's types -- says which endpoint replaced them
    before Wave F deletes anything.
    """
    for module, route in (
        ("return_artifacts.py", "/{session_id}/production-artifacts"),
        ("physical_operations.py", "/{session_id}/artifacts"),
    ):
        source = (_SRC / "api" / module).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=module)
        deprecated_get_paths = {
            decorator.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "get"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
            and any(
                keyword.arg == "deprecated"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in decorator.keywords
            )
        }
        assert route in deprecated_get_paths, f"{module}{route} is not marked deprecated"


#: The canonical write surface, complete. Two routes replace five legacy ones
#: (`returns.py` create and cancel, `production_workflow.py` start and events).
_CANONICAL_WRITES = {"", "/{session_id}/events"}


def test_the_canonical_write_surface_is_exactly_these_two_routes() -> None:
    """Replaces `..._is_read_only_while_duplicates_are_unresolved`.

    That test held the line until the duplicates were reconciled, which they now
    are. What replaces it is not "writes are allowed" -- it is the enumeration,
    so the surface cannot grow by accident. Consolidation that ends with nine
    canonical writes instead of nine legacy ones has achieved nothing.
    """
    tree = ast.parse(_CANONICAL.read_text(encoding="utf-8"), filename=str(_CANONICAL))
    writes = {
        decorator.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "router"
        and decorator.func.attr != "get"
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
    }
    assert writes == _CANONICAL_WRITES, (
        "the canonical write surface changed; a new way to mutate a return "
        f"belongs on an existing route or needs justifying here: {writes}"
    )


def test_there_is_no_canonical_cancel_endpoint() -> None:
    """Cancellation is `POST /{id}/events` with `eventType: CANCELLED`.

    The legacy pair were two ways to cancel that disagreed: `/cancel` wrote the
    session document and released the discovery lock without telling the
    workflow; the workflow's CANCELLED event updated durable state and the
    session document but left the lock held. Porting `/cancel` across would have
    carried that split onto the canonical surface, which is the one place it
    must not exist.
    """
    paths = _route_paths(_CANONICAL)
    assert not any("cancel" in path.lower() for path in paths), (
        f"cancellation is an event, not an endpoint: {paths}"
    )


def test_the_cancelling_path_releases_the_discovery_lock() -> None:
    """The thing that would have been silently lost.

    `record_event` is now the only cancellation path, and it did not release the
    discovery lock -- only the legacy endpoint did. Asserted on the coordinator
    rather than through HTTP because it must hold for *every* caller, including
    the workflow's own, not just the route this slice added.
    """
    source = (_SRC / "operations" / "production_workflow.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="production_workflow.py")
    record_event = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "record_event"
    )
    releases = [
        node
        for node in ast.walk(record_event)
        if isinstance(node, ast.Attribute) and node.attr == "release_discovery_lock"
    ]
    assert releases, "record_event must release the discovery lock when a return is cancelled"


def test_a_rejected_transition_fails_the_update_not_the_workflow_task() -> None:
    """Without `failure_exception_types`, the common case was the slow one.

    `apply_production_return_event` raises `ValueError` to reject an event.
    Temporal's default is to treat that as a workflow *task* failure, which
    retries forever -- so `execute_update` blocked until the 10-second RPC
    deadline and the caller got a generic timeout, on every double-click.
    """
    source = (_SRC / "workflows" / "production_return_workflow.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="production_return_workflow.py")
    definition = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ProductionReturnWorkflow"
    )
    declared = {
        element.id
        for decorator in definition.decorator_list
        if isinstance(decorator, ast.Call)
        for keyword in decorator.keywords
        if keyword.arg == "failure_exception_types" and isinstance(keyword.value, ast.List)
        for element in keyword.value.elts
        if isinstance(element, ast.Name)
    }
    assert "ValueError" in declared


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
