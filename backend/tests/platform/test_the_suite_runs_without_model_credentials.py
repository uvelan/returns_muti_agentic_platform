"""No test may demand a model-provider key it never spends.

`test_settings` is the shared settings fixture and it used to obtain
NVIDIA_API_KEY and GOOGLE_API_KEY through `_required_environment_variable`,
which raises. Because the AI-gateway fields sit on the same `Settings` object as
Mongo, Neo4j and the catalog path, that requirement reached every test taking
the fixture -- index drift, catalog loading, health probes, the system-store
bootstrap -- none of which reach a provider. On a machine with no model
credentials they errored during setup, which is indistinguishable from the code
being broken.

The damage was not only the noise. Three modules responded by copying the
fixture's environment helper rather than using it, so settings construction was
growing a second implementation inside the test tree; a fourth copy was the
default outcome of the next test that needed real infrastructure.

These pin the two halves of the rule. A test that genuinely dispatches to a
provider takes `live_ai_credentials` and is skipped without real keys; every
other test constructs settings and runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parents[1]
_CONFTEST = _TESTS_ROOT / "conftest.py"
_PROVIDER_KEY_NAMES = ("NVIDIA_API_KEY", "GOOGLE_API_KEY")


def _required_environment_variable_arguments(module: ast.Module) -> set[str]:
    """Every literal passed to the helper that raises on a missing variable."""
    return {
        argument.value
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_required_environment_variable"
        for argument in node.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    }


def test_the_shared_settings_fixture_does_not_demand_a_provider_key() -> None:
    """Asserted against the source rather than by running the fixture.

    Running it proves nothing on a machine that happens to have the keys
    exported -- which is every machine where the regression would be introduced.
    """
    module = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    demanded = _required_environment_variable_arguments(module)

    assert demanded.isdisjoint(_PROVIDER_KEY_NAMES), (
        f"{sorted(demanded & set(_PROVIDER_KEY_NAMES))} is required by every test taking "
        "`test_settings`; use `_provider_key` for the placeholder and let a test that "
        "actually calls a provider take `live_ai_credentials`"
    )


def test_a_test_that_needs_a_real_provider_key_has_a_way_to_ask_for_one() -> None:
    """The placeholder is only safe while the explicit opt-in exists.

    Without it the next test that needs a live key has no sanctioned route, and
    the cheapest thing to do is put the requirement back on the shared fixture --
    which is how this started.
    """
    module = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    fixtures = {
        node.name
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(decorator, ast.Attribute) and decorator.attr == "fixture"
            for decorator in node.decorator_list
        )
    }

    assert "live_ai_credentials" in fixtures
