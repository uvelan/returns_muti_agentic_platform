"""Every alert fires, and every alert clears.

The closure condition for this work is exactly that pair. An alert that cannot
fire is decoration; an alert that cannot clear is noise that trains operators to
ignore the whole surface, which leaves them worse off than the silence it
replaced.

The third property matters as much and is easier to lose: an unmeasured
condition reports `NOT_VALIDATED`, never `PASS`. The two-day housekeeping outage
happened because every surface was green -- reporting green for something nobody
looked at is how that repeats.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from return_platform.operations.alerts import (
    ALERTS,
    Alert,
    OperationalReading,
    evaluate,
)


def _reading_for(alert: Alert, value: float) -> OperationalReading:
    """A reading that makes exactly this alert see `value`.

    Written by field name rather than through `alert.read`, because a helper
    that inverted the reader would be testing the reader against itself.
    """
    field = {
        "outbox-depth": "outbox_depth",
        "stalled-sync-runs": "stalled_sync_runs",
        "queue-age": "oldest_queued_seconds",
        "serving-snapshot": "serving_snapshots",
        "replay-failures": "replay_failures",
        "return-projection-convergence": "unconverged_projections",
    }[alert.id]
    return replace(OperationalReading(), **{field: value})


def _result(alert: Alert, reading: OperationalReading) -> object:
    return next(result for result in evaluate(reading) if result.id == alert.id)


@pytest.mark.parametrize("alert", ALERTS, ids=lambda alert: alert.id)
def test_every_alert_fires(alert: Alert) -> None:
    """At the failing threshold, it says FAIL."""
    # `serving-snapshot` measures distance from one, so its failing reading is
    # zero snapshots rather than a large number.
    raw = 0.0 if alert.id == "serving-snapshot" else alert.fail_at
    result = _result(alert, _reading_for(alert, raw))

    assert result.status == "FAIL", f"{alert.id} did not fire at its threshold: {result}"
    assert result.details, f"{alert.id} fired without saying what happened"


@pytest.mark.parametrize("alert", ALERTS, ids=lambda alert: alert.id)
def test_every_alert_clears(alert: Alert) -> None:
    """Below the warning threshold, it says PASS."""
    healthy = 1.0 if alert.id == "serving-snapshot" else 0.0
    result = _result(alert, _reading_for(alert, healthy))

    assert result.status == "PASS", f"{alert.id} would not clear when healthy: {result}"


@pytest.mark.parametrize("alert", ALERTS, ids=lambda alert: alert.id)
def test_an_unmeasured_alert_is_never_passing(alert: Alert) -> None:
    """Green means checked. Nothing else may mean green."""
    result = _result(alert, OperationalReading())

    assert result.status == "NOT_VALIDATED", (
        f"{alert.id} reported {result.status} without a reading"
    )
    assert result.value is None
    assert result.details, f"{alert.id} must say why it has no reading"


@pytest.mark.parametrize(
    "alert",
    [alert for alert in ALERTS if alert.warn_at < alert.fail_at],
    ids=lambda alert: alert.id,
)
def test_an_alert_with_room_between_thresholds_warns_first(alert: Alert) -> None:
    """A degrading condition should be visible before it is broken."""
    result = _result(alert, _reading_for(alert, alert.warn_at))

    assert result.status == "WARN", f"{alert.id} skipped WARN at its warning threshold"


def test_the_alert_set_is_bounded_by_construction() -> None:
    """One alert per condition, never one per entity.

    This is what makes the surface safe to wire to a pager. A thousand stalled
    sync runs must be one firing alert carrying a count, not a thousand alerts --
    and the way that stays true is that alerts come from a fixed tuple rather
    than from iterating rows.
    """
    identifiers = [alert.id for alert in ALERTS]

    assert len(identifiers) == len(set(identifiers)), "alert ids must be unique"
    assert len(evaluate(OperationalReading())) == len(ALERTS)

    # A large reading produces the same number of results as a small one.
    busy = OperationalReading(
        outbox_depth=10_000,
        stalled_sync_runs=999,
        oldest_queued_seconds=86_400,
        serving_snapshots=7,
        replay_failures=42,
        unconverged_projections=5_000,
    )
    assert len(evaluate(busy)) == len(ALERTS)


def test_the_evaluation_order_is_stable() -> None:
    """So a report diffed against yesterday's diffs on status, not on order."""
    first = [result.id for result in evaluate(OperationalReading())]
    second = [result.id for result in evaluate(OperationalReading(outbox_depth=5))]

    assert first == second == [alert.id for alert in ALERTS]


def test_two_snapshots_are_as_wrong_as_none() -> None:
    """Zero means discovery has nothing to read; two means it is ambiguous which.

    Both are distance from one, and the audit's UIAUDIT-001 was precisely a
    disagreement about how many `ACTIVE` markers there were.
    """
    assert _result(ALERTS[3], OperationalReading(serving_snapshots=0)).status == "FAIL"
    assert _result(ALERTS[3], OperationalReading(serving_snapshots=2)).status == "FAIL"
    assert _result(ALERTS[3], OperationalReading(serving_snapshots=1)).status == "PASS"
