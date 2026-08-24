"""The analyzer's sync surface must not be a second engine.

Two systems wrote the system graph. `GraphSyncService` builds a candidate
generation and cuts over to it, and every discovery read is pinned to
`ActiveRuntimeSnapshot`. The analyzer carried its own: it read source rows and
MERGEd nodes with a generated Cypher query, with no generation at all -- so a
sync started from the analyzer workspace wrote into whatever generation was
being served, outside the invariant the readers depend on.

These are about the seam, not the pipeline: that the analyzer's surface
delegates, that scope is validated rather than trusted, and that claiming a run
is visible to the guard that stops a second one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncRunView,
    GraphSyncScope,
)
from return_platform.graph_analyzer.api import _sync_run_of


def _view(**overrides: object) -> GraphSyncRunView:
    base: dict[str, object] = {
        "id": "run-1",
        "mode": "FULL",
        "status": "RUNNING",
        "schemaVersion": "2026.08.04",
        "sourceCounts": {"orders": 12, "customers": 3},
        "nodeWrites": 40,
        "relationshipWrites": 7,
        "constraintsApplied": [],
        "configurationDigest": "abc",
        "startedBy": "operator",
        "startedAt": "2026-08-24T10:00:00Z",
    }
    return GraphSyncRunView.model_validate(base | overrides)


def test_a_run_reports_what_it_is_doing_while_it_does_it() -> None:
    """The ledger recorded only totals, which are written at the end.

    A rebuild in progress therefore reported zeros for its whole duration, and
    an operator could not tell a working run from a wedged one.
    """
    view = _view(currentSource="orders", currentActivity="Reading orders", itemsRead=12)
    assert view.currentSource == "orders"
    assert view.itemsRead == 12


def test_scope_is_carried_on_the_request() -> None:
    request = GraphSyncRequest(mode=GraphSyncScope.FULL, scope=["orders", "customers"])
    assert request.scope == ["orders", "customers"]


def test_scope_defaults_to_everything_the_mode_selects() -> None:
    """Every caller from before per-object scope existed must be unaffected."""
    assert GraphSyncRequest(mode=GraphSyncScope.FULL).scope == []


def test_a_claimed_run_is_a_legal_status() -> None:
    """`begin` writes PREPARING before the work starts, and the view must hold it."""
    assert _view(status="PREPARING").status == "PREPARING"


def test_an_unknown_status_is_still_refused() -> None:
    """Widening the vocabulary must not turn it into a free-text field."""
    with pytest.raises(ValidationError):
        _view(status="NEARLY")


class TestTheAnalyzerView:
    """`_sync_run_of` is the translation that let one engine serve both UIs."""

    def test_a_scoped_run_reads_as_partial(self) -> None:
        assert _sync_run_of(_view(scope=["orders"])).mode == "PARTIAL"

    def test_an_unscoped_run_reads_as_full(self) -> None:
        assert _sync_run_of(_view()).mode == "FULL"

    def test_a_dead_run_is_reported_as_failed_not_completed(self) -> None:
        """The analyzer vocabulary has no STALLED, and FAILED is the honest neighbour.

        Mapping it to COMPLETED would tell an operator a rebuild finished when
        its process was killed partway through.
        """
        assert _sync_run_of(_view(status="STALLED")).status == "FAILED"

    def test_items_processed_totals_every_source(self) -> None:
        assert _sync_run_of(_view()).itemsProcessed == 15

    def test_the_failure_reason_is_preferred_over_the_error_code(self) -> None:
        """The type says what kind; the reason says why."""
        run = _sync_run_of(
            _view(status="FAILED", errorCode="VALUEERROR", failureReason="orders had no key")
        )
        assert run.error == "orders had no key"
