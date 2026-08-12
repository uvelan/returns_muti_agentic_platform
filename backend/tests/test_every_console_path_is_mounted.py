"""Every path the console calls must be served by the app.

`/api/agents` was written, unit-tested against MSW handlers, called by the
Configuration screen's Agents section, and never passed to `include_router`.
The suite was green because the frontend tests answered themselves and the
backend tests never asked whether the router was mounted. A 404 shipped.

This test closes the class, not the instance: it asserts the *mounted* contract
against the paths the console is known to call. A new screen calling a new path
adds a line here; a router that stops being mounted fails here.

Deliberately asserted against `app.openapi()` rather than a route-object walk --
the generated contract is what the frontend's typed client is built from, so it
is the thing that must contain the path.
"""

from __future__ import annotations

import re

import pytest

from return_platform.main import create_app

# Paths the console calls, from `frontend/src/api/*.ts`. Templated segments are
# written the way FastAPI declares them.
CONSOLE_PATHS: tuple[str, ...] = (
    "/api/source-bindings",
    "/api/source-bindings/{dataset}",
    "/api/runtime-config",
    "/api/principal",
    # Configuration domain
    "/api/config/runtime",
    "/api/config/releases",
    "/api/config/releases/{release_id}",
    "/api/config/releases/{release_id}/promote",
    "/api/config/sources",
    "/api/config/sources/{source_id}",
    "/api/config/audit",
    "/api/config/audit/{audit_id}",
    # Agent configuration -- the one this test exists for.
    "/api/agents",
    "/api/agents/{manifest_id}",
    # Returns domain
    "/api/returns",
    "/api/returns/{session_id}",
    "/api/returns/{session_id}/timeline",
    "/api/returns/{session_id}/support",
    "/api/returns/{session_id}/conversation",
    "/api/returns/{session_id}/events",
    # The case read surface. Called from `frontend/src/api/cases.ts` since the
    # copilot stopped joining an order reference in the browser, and missing
    # from this list until the return-history read was added beside it.
    "/api/cases",
    "/api/cases/{case_id}",
    # Returns against an order or a customer, read from the graph rather than
    # from one case document -- the copilot's "has this customer returned this
    # before" panel.
    "/api/return-history",
    # Order Discovery
    "/api/v2/order-agent/conversations",
    "/api/v2/order-agent/conversations/{conversation_id}/turns",
    "/api/v2/order-agent/conversations/{conversation_id}/transcript",
    # AI Control Center
    "/api/ai/metrics",
    "/api/ai/metrics/summary",
    "/api/ai/routes",
    "/api/ai/tasks",
    "/api/ai/interceptions",
    "/api/ai/interceptions/{interception_id}/request",
    "/api/ai/interceptions/{interception_id}/answer",
    "/api/ai/interceptions/{interception_id}/cancel",
    # Source Sync (S6)
    "/api/graph-sync/runs",
    "/api/graph-sync/runs/{run_id}",
    # Graph Schema Analyzer
    "/api/graph-schema/analyses",
    "/api/graph-schema/analyses/{analysis_id}",
    "/api/graph-schema/drafts/{draft_id}",
    "/api/graph-schema/drafts/{draft_id}/shape",
    "/api/graph-schema/drafts/{draft_id}/revisions",
    "/api/graph-schema/drafts/{draft_id}/approve",
    "/api/graph-schema/drafts/{draft_id}/reanalysis",
    # The Drift tab accepts a proposed re-analysis by posting its commands here
    # -- the same call a hand-written edit makes, deliberately, so that there is
    # no second write path into a draft.
    "/api/graph-schema/drafts/{draft_id}/mutations",
    # Published graph-schema releases and the migration between two of them.
    "/api/schema-releases",
    "/api/schema-releases/{release_id}/migration-plan",
    "/api/schema-releases/{release_id}/activate",
)


@pytest.fixture(scope="module")
def served_paths() -> frozenset[str]:
    return frozenset(create_app().openapi()["paths"])


def test_every_path_the_console_calls_is_mounted(served_paths: frozenset[str]) -> None:
    missing = sorted(path for path in CONSOLE_PATHS if path not in served_paths)
    assert not missing, (
        "the console calls these paths and the app does not serve them; "
        f"a router is almost certainly missing from create_app(): {missing}"
    )


def test_agent_configuration_routes_are_mounted(served_paths: frozenset[str]) -> None:
    """Named separately so a regression reads as itself, not as a list diff."""
    assert "/api/agents" in served_paths
    assert "/api/agents/{manifest_id}" in served_paths


def test_no_versioned_data_console_path_is_mounted(served_paths: frozenset[str]) -> None:
    """Wave F1 unmounted `/data-console/v1/*` deliberately. Keep it that way.

    The modules still export handler functions that `/api/config` imports
    directly, so an accidental `include_router` would silently republish
    eighteen routes nothing is meant to serve.
    """
    republished = sorted(p for p in served_paths if p.startswith("/data-console"))
    assert not republished, republished


def test_every_mounted_path_is_declared_under_a_known_prefix(
    served_paths: frozenset[str],
) -> None:
    """A path outside these prefixes is a surface nobody decided to publish."""
    allowed = re.compile(r"^/(api|health)(/|$)")
    stray = sorted(p for p in served_paths if not allowed.match(p))
    assert not stray, stray
