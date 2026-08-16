"""Wave G1's Compose contract, asserted rather than described.

The plan names three groups and what belongs in each. Profiles are easy to get
wrong in a way nothing notices: a service in the default profile starts on every
`docker compose up`, and one behind a profile silently does not start for anyone
who forgets the flag. Neither shows up in a test suite.

**The `containerized-app` list here is deliberately longer than the plan's.**
The plan names backend, return-workflow-worker, return-orchestrator,
outbox-publisher and frontend. Two more exist and run:
`order-discovery-worker` (built in Wave C3, after the plan was written) and
`integration-outbox-worker`. Both have real entrypoints. Deleting working
services to match a list that predates them is the mistake this programme has
caught repeatedly; they are listed here instead, so the divergence from the plan
is recorded rather than accidental.

`data-job-worker` was a third such service and this docstring used to vouch for
it in the same breath -- "all three have real entrypoints". That was false by
the time it was written: `scripts/run_data_job_worker.py` imported
`return_platform.data_console.api.jobs`, and the whole `data_console` package
was deleted in 8a0d81a/007326f, so the container raised ImportError on every
start. Being listed here is not evidence a service works; it is evidence
somebody put it in compose. The service and its script are now gone.
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


_INFRA_SCRIPT = _COMPOSE.parent / "scripts" / "infra.sh"


def _in_profile(name: str) -> set[str]:
    if name == "(default)":
        return {n for n, s in _SERVICES.items() if not s.get("profiles")}
    return {n for n, s in _SERVICES.items() if name in (s.get("profiles") or [])}


def _infra_start_services() -> set[str]:
    """The service list `infra.sh start` brings up, read out of the script."""
    lines = _INFRA_SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "infrastructure_services=(" in line)
    end = next(i for i, line in enumerate(lines[start:], start) if line.strip() == ")")
    return {line.strip() for line in lines[start + 1 : end] if line.strip()}


@pytest.mark.skipif(not _INFRA_SCRIPT.is_file(), reason="scripts/infra.sh is not in this container")
def test_starting_infrastructure_does_not_build_the_backend_image() -> None:
    """`infra.sh start` must not need an application image to exist.

    `runtime-configuration-init` is correctly in the default profile -- the test
    below says why -- but it is built from `return-platform-backend:local`, so a
    bare `docker compose up -d` builds the whole backend image before a single
    datastore starts. On a machine whose backend runs on the host that is a
    build for nothing, and it fails outright behind a TLS-intercepting proxy.

    `infra.sh start` therefore names its services. Nothing is lost by the
    omission: `prepare_runtime_configuration.sh` runs the same SQL migrations,
    Neo4j migrations and graph-configuration bootstrap on the host -- which is
    asserted directly, because that equivalence is the whole justification.
    """
    requested = _infra_start_services()
    # PyYAML resolves `<<` merge keys, so a service inheriting `*backend-base`
    # carries its `build` and `image` here exactly as Compose would see them.
    buildable = {n for n, s in _SERVICES.items() if s.get("build")}
    image_backed = {
        n
        for n, s in _SERVICES.items()
        if str(s.get("image", "")).startswith("return-platform-backend")
    }
    assert not (requested & (buildable | image_backed))
    assert requested <= _in_profile("(default)")

    prepare = (_COMPOSE.parent / "scripts" / "prepare_runtime_configuration.sh").read_text(
        encoding="utf-8"
    )
    for script in (
        "apply_sql_migrations.py",
        "apply_neo4j_migrations.py",
        "bootstrap_graph_configuration.py",
    ):
        assert script in prepare, f"{script} runs only in the init container, not on the host"


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
        "integration-outbox-worker",
        # Also beyond the plan, and its entrypoint is asserted to be importable
        # and constructible by `tests/housekeeping/` -- which is what the
        # `data-job-worker` paragraph above says this list is not evidence of.
        "housekeeping-worker",
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


def test_every_packaged_path_default_is_overridden_in_compose() -> None:
    """`BACKEND_ROOT` is wrong inside the image, so no default may survive there.

    `BACKEND_ROOT` is `parents[3]` of the settings module. From a checkout that
    is `backend/`; inside the runtime image it is `/usr/local/lib/python3.13`,
    because the Dockerfile copies `config` and `scripts` but not `src` and
    `return_platform` therefore imports from site-packages. Every default that
    hangs off it points at a path that does not exist in any container.

    That has now shipped twice. `PLATFORM_SYSTEM_STORE_MANIFEST_PATH` was
    missing and crash-looped two workers; `configuration_directory` was not even
    a setting -- `main.py` passed `BACKEND_ROOT / "config"` to the agent
    configuration service directly -- and the Configuration screen's Agents
    section answered 500 in every container. Both suites were green throughout,
    because tests run from the checkout where `BACKEND_ROOT` is correct.

    So this asserts the class instead of the two instances: any Settings field
    whose *default* lies under `BACKEND_ROOT` must be overridden by an
    environment entry in compose. A new packaged path fails here on the commit
    that adds it rather than on the next deployment.
    """
    from return_platform.configuration.settings import BACKEND_ROOT, Settings

    environment = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["x-platform-environment"]

    packaged = {
        name
        for name, field in Settings.model_fields.items()
        if isinstance(field.default, Path) and field.default.is_relative_to(BACKEND_ROOT)
    }
    assert packaged, "no packaged-path settings found -- this test has lost its subject"

    missing = sorted(
        f"{name} (expected PLATFORM_{name.upper()})"
        for name in packaged
        if f"PLATFORM_{name.upper()}" not in environment
    )
    assert not missing, (
        "these settings default to a path under BACKEND_ROOT, which does not exist "
        f"inside the image, and compose does not override them: {missing}"
    )


def test_no_compose_path_override_points_outside_the_packaged_config_tree() -> None:
    """The override is only worth having if it names a path the image contains.

    `/app/config` and `/app/scripts` are what the runtime stage copies. An
    override pointing anywhere else is the same failure with a different path in
    the message.
    """
    environment = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["x-platform-environment"]
    stray = sorted(
        f"{key}={value}"
        for key, value in environment.items()
        if key.endswith(("_PATH", "_DIRECTORY"))
        and isinstance(value, str)
        and value.startswith("/")
        and not value.startswith(("/app/config", "/app/scripts", "/run/"))
    )
    assert stray == [], stray
