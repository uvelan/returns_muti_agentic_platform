"""Wave G1's Compose contract, asserted rather than described.

The plan names three groups and what belongs in each. Profiles are easy to get
wrong in a way nothing notices: a service in the default profile starts on every
`docker compose up`, and one behind a profile silently does not start for anyone
who forgets the flag. Neither shows up in a test suite.

**The `containerized-app` list here is deliberately longer than the plan's.**
The plan names backend, return-workflow-worker, return-orchestrator,
outbox-publisher and frontend. Three more exist and run:
`order-discovery-worker` (built in Wave C3, after the plan was written),
`data-job-worker` and `integration-outbox-worker`. All three have real
entrypoints. Deleting working services to match a list that predates them is the
mistake this programme has caught repeatedly; they are listed here instead, so
the divergence from the plan is recorded rather than accidental.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_COMPOSE = Path(__file__).resolve().parents[3] / "compose.yaml"

# The real-infra container is given `src`, `tests` and `config` only -- the
# repository root, and therefore `compose.yaml`, is not copied in. Skipping is
# right rather than failing: these assertions are about a file that genuinely is
# not there, and the same tests run on the host where it is. The worker-script
# AST test skips for the same reason.
pytestmark = pytest.mark.skipif(
    not _COMPOSE.is_file(), reason="compose.yaml is not present in the test container"
)

_SERVICES: dict[str, dict] = (
    yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"] if _COMPOSE.is_file() else {}
)


def _in_profile(name: str) -> set[str]:
    if name == "(default)":
        return {n for n, s in _SERVICES.items() if not s.get("profiles")}
    return {n for n, s in _SERVICES.items() if name in (s.get("profiles") or [])}


def test_the_default_profile_is_infrastructure_and_bootstrap_only() -> None:
    """Everything here starts on a bare `docker compose up`.

    `runtime-configuration-init` belongs with the datastores, not behind the app
    profile: it seeds the graph configuration the backend and every worker read
    at startup, so an app-profile-only bootstrap would leave a default-profile
    stack that cannot serve configuration to anything.
    """
    assert _in_profile("(default)") == {
        "vault",
        "mongodb",
        "mongodb-rs-init",
        "neo4j",
        "valkey",
        "sqlserver",
        "sqlserver-init",
        "temporal-postgresql",
        "temporal",
        "runtime-configuration-init",
    }


def test_the_app_profile_is_the_services_that_serve_traffic() -> None:
    assert _in_profile("containerized-app") == {
        "backend",
        "return-workflow-worker",
        "return-orchestrator",
        "outbox-publisher",
        "frontend",
        # Beyond the plan's list, and real -- see the module docstring.
        "order-discovery-worker",
        "data-job-worker",
        "integration-outbox-worker",
    }


def test_dev_tools_are_opt_in() -> None:
    """A deployment that never passes `--profile dev-tools` runs without a
    Temporal UI, without test data, and without a diagnostics shell."""
    assert _in_profile("dev-tools") == {"temporal-ui", "seed-runner", "diagnostics"}


def test_no_application_service_depends_on_seed_runner() -> None:
    """The plan states this as a rule, and Phase 4b did the decoupling.

    It is worth an assertion because the failure is quiet and severe: an app
    service that waits on seed-runner cannot start in any environment that does
    not load test data, which is every real one.
    """
    offenders = sorted(
        name
        for name, service in _SERVICES.items()
        if "seed-runner" in (service.get("depends_on") or {})
    )
    assert offenders == []


def test_nothing_outside_dev_tools_depends_on_a_dev_tool() -> None:
    """The same failure one level up.

    Compose refuses to start a service whose dependency is in a profile that is
    not enabled, so a stray edge here breaks `docker compose up` for anyone who
    does not pass the flag -- and it breaks it at startup, not at test time.
    """
    dev_tools = _in_profile("dev-tools")
    offenders = sorted(
        f"{name} -> {dependency}"
        for name, service in _SERVICES.items()
        if name not in dev_tools
        for dependency in (service.get("depends_on") or {})
        if dependency in dev_tools
    )
    assert offenders == []
