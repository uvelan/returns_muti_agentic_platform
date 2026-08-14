"""Two SQL return writers coexist by design. Neither may spread into the other.

The audit records "SQL return persistence" as a duplicate with no canonical path.
That is no longer true, and the resolution is a *partition* rather than a
deletion:

* `persist_case_return_records` (migration 005) is the canonical multi-RMA
  writer. It persists one case and all of its RMAs in one idempotent
  transaction, and it is what `ReturnCaseWorkflow` reaches.
* `persist_support_result` is the legacy **single-session** writer. It is still
  live: `operations/return_support/providers/external.py` calls it,
  `providers/factory.py` builds that provider, and `operations/orchestrator.py`
  imports the factory -- and `ReturnOrchestrator` is what
  `scripts/run_return_orchestrator.py` runs as the `return-orchestrator`
  service, which is in `REQUIRED_PROCESS_CLASSES`. It is reachable from a
  deployed, required process class, so it is not dead and cannot be deleted.

Both facts rested on nothing. The danger is not that the legacy writer runs --
it must, for the sessions it owns -- it is that new work is written against it
because nothing says which one is canonical at the point an import is made. C3
(`Case -> N return records -> N items`) is what the legacy writer cannot express:
it flattens a session into one result, so a caller that reached for it from the
case path would silently collapse a multi-RMA case into a single record.

`SandboxReturnSupportProvider` was the second caller of the legacy writer and had
no caller of its own -- not in `backend/src`, not in `tests`, not in `scripts`,
not in `compose.yaml`, and not selectable by any `support_ticket_mode` value
(`build_return_support_provider` raises for anything but `EXTERNAL_AUTHORITY`,
and returns the external provider for that). It is deleted, and the caller set
below is what keeps its slot from being refilled.

Reads source text via AST rather than importing, for the same reason
`test_frozen_modules_gain_no_new_callers.py` does: it stays fast and cannot be
defeated by a module that fails to import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
_PACKAGE = _SRC / "return_platform"

#: The exact modules permitted to call the legacy single-session SQL writer.
#: `contracts.py` declares it on the repository protocol and `sql_business_state.py`
#: implements it; `external.py` is the one production caller.
_LEGACY_WRITER_CALLERS: frozenset[str] = frozenset(
    {
        "return_platform/operations/return_support/providers/external.py",
    }
)

#: Where the legacy writer is *declared*, which is not a call. Excluded from the
#: caller scan so a declaration site does not read as a caller.
_LEGACY_WRITER_DECLARATIONS: frozenset[str] = frozenset(
    {
        "return_platform/operations/return_support/providers/contracts.py",
        "return_platform/operations/sql_business_state.py",
    }
)

_LEGACY_WRITER = "persist_support_result"
_CANONICAL_WRITER = "persist_case_return_records"


def _modules() -> list[Path]:
    return [path for path in _PACKAGE.rglob("*.py") if "__pycache__" not in path.parts]


def _key(path: Path) -> str:
    return path.relative_to(_SRC).as_posix()


def _awaited_attribute_calls(path: Path, name: str) -> bool:
    """True if this module *calls* `.<name>(...)` on anything.

    Attribute calls only. A module that declares the method (an `async def` on a
    Protocol or on the repository) is not calling it, and counting the definition
    would make every declaration site look like a caller.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == name:
                return True
    return False


def test_the_legacy_single_session_writer_has_exactly_its_known_callers() -> None:
    """Adding a caller fails here, naming the canonical writer instead.

    Not a `<=` assertion: a silently shrinking set would hide a deletion nobody
    reviewed, which is the same reason the frozen-module test asserts both
    directions.
    """
    actual = {
        _key(path)
        for path in _modules()
        if _key(path) not in _LEGACY_WRITER_DECLARATIONS
        and _awaited_attribute_calls(path, _LEGACY_WRITER)
    }

    added = sorted(actual - _LEGACY_WRITER_CALLERS)
    assert not added, (
        f"{_LEGACY_WRITER} is the legacy single-session SQL writer and gained a new "
        f"caller: {added}. It flattens a session into one result and cannot express "
        f"C3 (Case -> N return records -> N items). Build against "
        f"{_CANONICAL_WRITER} instead."
    )

    removed = sorted(_LEGACY_WRITER_CALLERS - actual)
    assert not removed, (
        f"{_LEGACY_WRITER} lost callers {removed} -- if that was a deliberate "
        f"migration, shrink the expected set here in the same change so the "
        f"removal is reviewed rather than absorbed silently."
    )


def test_the_dead_sandbox_support_provider_stays_deleted() -> None:
    """It had zero callers and one call *to* the legacy writer.

    Its absence from the shipped package is the guarantee; this is what stops the
    file reappearing with the same shape, which is how a second caller of a
    legacy writer gets added without anyone deciding to add one.
    """
    assert not (_PACKAGE / "operations" / "return_support" / "providers" / "sandbox.py").exists()

    referencing = sorted(
        _key(path)
        for path in _modules()
        if "SandboxReturnSupportProvider" in path.read_text(encoding="utf-8", errors="replace")
    )
    assert referencing == []


def test_the_legacy_writer_is_still_reachable_from_a_required_process_class() -> None:
    """It is live, and that is why it was not deleted.

    `return-orchestrator` is in `REQUIRED_PROCESS_CLASSES`, so this chain runs in
    every deployment. An assertion rather than a comment because "no caller
    found" was wrong about this module once already -- the chain reaches it
    through a factory, from a script, outside its own package.
    """
    from return_platform.configuration.process_adoption import REQUIRED_PROCESS_CLASSES

    assert "return-orchestrator" in REQUIRED_PROCESS_CLASSES

    factory = _PACKAGE / "operations" / "return_support" / "providers" / "factory.py"
    external = _PACKAGE / "operations" / "return_support" / "providers" / "external.py"
    orchestrator = _PACKAGE / "operations" / "orchestrator.py"
    for path in (factory, external, orchestrator):
        assert path.is_file(), path

    assert "ExternalReturnSupportProvider" in factory.read_text(encoding="utf-8")
    assert "providers.factory" in orchestrator.read_text(encoding="utf-8")
    assert _awaited_attribute_calls(external, _LEGACY_WRITER)


#: Where each writer is declared, beyond its one implementation. Both are
#: structural ports so their consumers do not import the SQL package: the legacy
#: one is on `ReturnSupportRepository`, the canonical one on
#: `ReturnRecordStorePort`, which is how `workflows` avoids learning what a
#: connection pool is.
_WRITER_PORTS: dict[str, str] = {
    _LEGACY_WRITER: "return_platform/operations/return_support/providers/contracts.py",
    _CANONICAL_WRITER: "return_platform/workflows/return_case_activities.py",
}

_SQL_REPOSITORY = "return_platform/operations/sql_business_state.py"


@pytest.mark.parametrize("writer", [_LEGACY_WRITER, _CANONICAL_WRITER])
def test_each_writer_has_one_implementation_and_one_port(writer: str) -> None:
    """Coexistence means two methods on one repository, not two repositories.

    A second SQL repository holding either method would put the partition beyond
    the reach of the caller assertion above -- the caller set names modules, so a
    fork of the implementation would be invisible to it.
    """
    definitions = sorted(
        _key(path)
        for path in _modules()
        if any(
            isinstance(node, ast.AsyncFunctionDef) and node.name == writer
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8", errors="replace")))
        )
    )

    assert definitions == sorted([_SQL_REPOSITORY, _WRITER_PORTS[writer]]), (
        f"{writer} must have exactly one implementation ({_SQL_REPOSITORY}) and one "
        f"declaring port ({_WRITER_PORTS[writer]}). A second implementation is a third "
        "return-persistence path, which is what this partition exists to prevent."
    )
