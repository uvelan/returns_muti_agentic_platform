"""Architecture test: no langchain provider integration package is a dependency.

D11.6: a LangGraph node calling a provider directly would create a second AI routing
path bypassing failover, rate limits, circuit breakers, interception, replay, safety,
and metrics. Asserted at the dependency level, not just by code review.
"""

from __future__ import annotations

from pathlib import Path

FORBIDDEN_PACKAGE_PREFIXES = (
    "langchain-openai",
    "langchain-anthropic",
    "langchain-google",
    "langchain-community",
    "langchain-aws",
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_uv_lock_has_no_langchain_provider_packages() -> None:
    lock_text = (BACKEND_ROOT / "uv.lock").read_text(encoding="utf-8").lower()
    violations = [
        prefix for prefix in FORBIDDEN_PACKAGE_PREFIXES if f'name = "{prefix}"' in lock_text
    ]
    assert not violations, f"forbidden langchain provider package(s) locked: {violations}"


def test_pyproject_dependencies_have_no_langchain_provider_packages() -> None:
    pyproject_text = (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    violations = [prefix for prefix in FORBIDDEN_PACKAGE_PREFIXES if prefix in pyproject_text]
    assert not violations, f"forbidden langchain provider package(s) declared: {violations}"
