"""A test that opens a real datastore must say so, and the default run must skip it.

The failure this prevents has already happened. `tests/test_order_agent_rest.py`
entered the application lifespan -- MongoDB, Neo4j, Valkey and Temporal, for
real -- and nothing in the tree said so, so it was collected into the ordinary
run. It blocked, the run was killed at a per-test timeout, and 2,700 tests that
needed no infrastructure at all reported nothing. The cost of one unclassified
test is the entire suite's signal, which is why this is asserted rather than
left to reviewers.

Both halves are checked, because either alone is worthless: classification with
no deselection still runs the live tests in the normal suite, and deselection
with no classification deselects nothing.

Read from the source rather than by running anything. The regression is
introduced on a machine where the datastores happen to be up, which is exactly
where running the tests would fail to notice.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_ROOT = _TESTS_ROOT.parent
_PYPROJECT = _BACKEND_ROOT / "pyproject.toml"

#: Suffixes `tests/conftest.py` reads as "live" without needing a marker.
_LIVE_INFRA_MODULE_SUFFIXES = ("_real_infra.py", "_docker.py")

#: Calls that open, or are one await away from opening, a connection to a real
#: service. Matched on the callee's trailing name so that an aliased import
#: (`from temporalio.client import Client as TemporalClient`) is still caught.
_DRIVER_CONSTRUCTORS = frozenset(
    {
        "AsyncMongoClient",
        "MongoClient",
        "WorkflowEnvironment",
    }
)

#: Attribute calls, matched on the last two segments for the same reason.
_DRIVER_METHODS = frozenset(
    {
        "pymssql.connect",
        "psycopg.connect",
        "GraphDatabase.driver",
        "AsyncGraphDatabase.driver",
        "Client.connect",
        "TemporalClient.connect",
        "WorkflowEnvironment.start_local",
        "WorkflowEnvironment.start_time_skipping",
    }
)


def _dotted_name(node: ast.expr) -> str:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _opens_a_real_connection(tree: ast.Module) -> set[str]:
    """Every driver-opening call in the module, by name."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if not name:
            continue
        segments = name.split(".")
        if segments[-1] in _DRIVER_CONSTRUCTORS:
            found.add(name)
        elif len(segments) >= 2 and ".".join(segments[-2:]) in _DRIVER_METHODS:
            found.add(name)
    return found


def _declares_live_infra(tree: ast.Module) -> bool:
    """Whether the module carries the marker at module or function level."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "live_infra":
            return True
    return False


def _test_modules() -> list[Path]:
    return sorted(
        path
        for path in _TESTS_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and path.name != "conftest.py"
    )


def test_every_module_that_opens_a_datastore_is_in_the_live_suite() -> None:
    """Nothing reaches real infrastructure from the normal run."""
    unclassified: dict[str, list[str]] = {}

    for path in _test_modules():
        if path.name.endswith(_LIVE_INFRA_MODULE_SUFFIXES):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        opened = _opens_a_real_connection(tree)
        if opened and not _declares_live_infra(tree):
            unclassified[path.relative_to(_TESTS_ROOT).as_posix()] = sorted(opened)

    assert not unclassified, (
        "these modules open a real connection and are not in the live suite, so a "
        "datastore that is slow or down takes the whole run with it -- name the file "
        "`*_real_infra.py` or declare `pytestmark = pytest.mark.live_infra`: "
        f"{unclassified}"
    )


def test_the_default_run_deselects_the_live_and_browser_suites() -> None:
    """Classification is only worth something while the default run acts on it."""
    configuration = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    addopts = configuration["tool"]["pytest"]["ini_options"]["addopts"]

    assert "-m" in addopts, (
        "`addopts` no longer carries a `-m` expression, so the default run collects "
        "the live-infrastructure suite again"
    )

    expression = addopts[addopts.index("-m") + 1]

    for suite in ("live_infra", "browser"):
        assert f"not {suite}" in expression, (
            f"the default `-m` expression does not exclude `{suite}`: {expression!r}"
        )


def test_the_four_suites_are_registered_markers() -> None:
    """`--strict-markers` is on, so an unregistered suite name is a hard error.

    Pinned here as well because the registration and the classification live in
    two files, and a marker removed from one of them fails in a way that names
    the marker rather than the separation.
    """
    configuration = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    ini_options = configuration["tool"]["pytest"]["ini_options"]
    registered = {entry.split(":", 1)[0] for entry in ini_options["markers"]}

    assert {"unit", "integration", "live_infra", "browser"} <= registered

    assert "--strict-markers" in ini_options["addopts"], (
        "without `--strict-markers` a typo in a suite marker is silently a new suite "
        "that nothing runs"
    )


def test_live_infra_wins_when_a_module_is_more_than_one_thing() -> None:
    """A live test that is also an integration test is still a live test.

    `_SUITE_MARKERS` is the precedence order `_suite_of` walks, so its first
    element is the answer to "which suite claims a module that qualifies for
    two". Pinned rather than re-derived: a copy of the classifier here would
    keep passing while the real one drifted.
    """
    suites = sys.modules["tests.conftest"]._SUITE_MARKERS

    assert suites == ("live_infra", "browser", "integration", "unit")


def test_every_collected_test_carries_exactly_one_suite(request) -> None:  # type: ignore[no-untyped-def]
    """The four markers partition the run rather than overlapping it.

    Asserted against the live session, which is the only place the answer is
    real: classification depends on fixture closures, and no static read of the
    source can resolve those.
    """
    suites = set(sys.modules["tests.conftest"]._SUITE_MARKERS)
    overlapping: dict[str, list[str]] = {}
    unclassified: list[str] = []

    for item in request.session.items:
        carried = sorted(suites & {mark.name for mark in item.iter_markers()})
        if len(carried) > 1:
            overlapping[item.nodeid] = carried
        elif not carried:
            unclassified.append(item.nodeid)

    assert not overlapping, f"tests claimed by two suites at once: {overlapping}"
    assert not unclassified, f"tests in no suite at all: {unclassified[:20]}"
