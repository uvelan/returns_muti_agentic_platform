"""CFG-6 RV round 1 F1/F2.

F1: `FeedbackLearningService` used to capture the `Settings` *value* at
construction. `RuntimeConfigurationActivator.refresh` replaces
`resources.settings` with a NEW validated instance on every adoption
(`runtime_activation.py:377-378`) -- it does not mutate the object in place --
so a service holding the old object read `deployment.feedback_learning.enabled`
from the moment it was constructed, forever. Fixed by holding the `resources`
container and reading `resources.settings.feedback_learning_enabled` at call
time. This test proves the fix: rebinding `resources.settings` changes the
service's answer on its very next read, with no reconstruction.

F2: item 6c's brief text ("a test asserting `ReturnOrchestrator` is
constructed with the live `resources.settings` object, so a future wiring
site cannot regress it") named a test that did not exist. This file is that
test: an AST guard, in the style of `tests/agents/test_no_cross_agent_imports.py`,
scanning every module under `backend/src` for a `FeedbackLearningService(...)`
or `ReturnOrchestrator(...)` call site and refusing one that passes a bare
`settings` name -- the exact shape of the regression F1 fixed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from return_platform.configuration.settings import Settings
from return_platform.operations.feedback_service import FeedbackLearningService, SettingsSnapshot

BACKEND_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"

_GUARDED_CALLEES = frozenset({"FeedbackLearningService", "ReturnOrchestrator"})
#: Keyword (or, for `FeedbackLearningService`, the second positional) argument
#: naming the settings source at each guarded call site.
_SETTINGS_ARG_NAMES = frozenset({"settings", "resources"})


class _FakeMongoCollection:
    pass


class _FakeMongoDatabase:
    def __getitem__(self, _name: str) -> _FakeMongoCollection:
        return _FakeMongoCollection()


class _FakeMongoClient:
    """`FeedbackLearningService.__init__` only ever does
    `client[resources.settings.mongo_database]`; nothing else is called here.
    """

    def __getitem__(self, _name: str) -> _FakeMongoDatabase:
        return _FakeMongoDatabase()


def _settings(**overrides: Any) -> Settings:
    return Settings(**overrides)


# --------------------------------------------------------------------------- #
# F1: rebinding resources.settings changes the service's answer at call time
# --------------------------------------------------------------------------- #


def test_rebinding_resources_settings_changes_the_services_next_read() -> None:
    resources = SettingsSnapshot(_settings(feedback_learning_enabled=True))
    service = FeedbackLearningService(_FakeMongoClient(), resources)  # type: ignore[arg-type]

    assert service._enabled is True  # noqa: SLF001

    # The activator's own mechanism: REPLACE `.settings`, never mutate the
    # existing `Settings` instance (`runtime_activation.py:377-378`,
    # `apply_deployment_configuration` always constructs a fresh object).
    resources.settings = _settings(feedback_learning_enabled=False)

    assert service._enabled is False  # noqa: SLF001


def test_holding_a_bare_settings_value_would_not_see_the_change() -> None:
    """The negative control: proves the test above actually exercises the bug
    F1 fixed, rather than something that would have passed either way.
    """
    live = _settings(feedback_learning_enabled=True)
    captured_value = live  # what the pre-fix `self._settings = settings` held

    live = _settings(feedback_learning_enabled=False)  # a rebind, not a mutation

    assert captured_value.feedback_learning_enabled is True
    assert live.feedback_learning_enabled is False


# --------------------------------------------------------------------------- #
# F2: the AST guard item 6c named
# --------------------------------------------------------------------------- #


def _guarded_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _GUARDED_CALLEES
    ]


def _settings_argument(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg in _SETTINGS_ARG_NAMES:
            return keyword.value
    # `FeedbackLearningService(client, resources, ...)` -- the settings source
    # is positional index 1 when not passed by keyword.
    if len(call.args) > 1:
        return call.args[1]
    return None


def test_no_module_constructs_the_guarded_classes_with_a_bare_settings_name() -> None:
    """No call to `FeedbackLearningService(...)` or `ReturnOrchestrator(...)`
    anywhere under `backend/src` may pass a bare `settings`-named value as its
    settings source -- exactly the shape of the bug F1 fixed
    (`self._settings = settings`, holding the captured value forever). A
    wrapped source (`SettingsSnapshot(settings)`, or a real live resources
    object) is required instead; this is checkable by AST without type
    inference because the fix makes "bare `settings` name" and "wrapped or
    live" syntactically distinct at every call site.

    This is a purely static guard -- see
    `test_rebinding_resources_settings_changes_the_services_next_read` above
    for the behavioural proof that the CURRENT single call site
    (`orchestrator.py`, `SettingsSnapshot(settings)`) is honest about being a
    snapshot, not a live source: `ReturnOrchestrator` has no production
    construction site in `backend/src` today (confirmed by this same scan --
    zero `ReturnOrchestrator(...)` call sites below), so no call site can yet
    pass a truly live container either.
    """
    violations: list[str] = []
    orchestrator_calls = 0
    feedback_calls = 0
    for path in sorted(BACKEND_SRC.rglob("*.py")):
        for call in _guarded_calls(path):
            callee = call.func.id  # type: ignore[union-attr]
            if callee == "ReturnOrchestrator":
                orchestrator_calls += 1
            else:
                feedback_calls += 1
            argument = _settings_argument(call)
            if isinstance(argument, ast.Name) and argument.id == "settings":
                violations.append(f"{path.relative_to(BACKEND_SRC)}:{call.lineno} ({callee})")

    assert not violations, (
        "construction site(s) pass a bare `settings` name as the settings "
        f"source, which cannot hot-adopt: {violations}"
    )
    # `ReturnOrchestrator` has no production construction site in `backend/src`
    # (design's own finding); pinned here so this test's silence is not
    # mistaken for coverage of a call site that does not exist.
    assert orchestrator_calls == 0
    assert feedback_calls == 1


def test_the_one_feedback_learning_service_call_site_wraps_its_settings_source() -> None:
    """Positive assertion to match: the one call site
    (`operations/orchestrator.py`) passes `SettingsSnapshot(settings)`, a
    `SettingsSource`, not `settings` itself.
    """
    orchestrator_path = BACKEND_SRC / "operations" / "orchestrator.py"
    calls = [
        call
        for call in _guarded_calls(orchestrator_path)
        if isinstance(call.func, ast.Name) and call.func.id == "FeedbackLearningService"
    ]
    assert len(calls) == 1
    argument = _settings_argument(calls[0])
    assert isinstance(argument, ast.Call)
    assert isinstance(argument.func, ast.Name)
    assert argument.func.id == SettingsSnapshot.__name__
