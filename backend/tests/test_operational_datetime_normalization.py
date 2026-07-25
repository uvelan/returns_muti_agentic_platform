from datetime import UTC, datetime, timedelta, timezone

from return_platform.operations.models import normalize_utc_datetime


def test_naive_persistence_timestamp_is_interpreted_as_utc() -> None:
    value = datetime(2026, 7, 25, 7, 37, 29, 163000)

    assert normalize_utc_datetime(value) == datetime(
        2026, 7, 25, 7, 37, 29, 163000, tzinfo=UTC
    )


def test_aware_timestamp_is_converted_to_utc() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    value = datetime(2026, 7, 25, 13, 7, 29, 163000, tzinfo=india)

    assert normalize_utc_datetime(value) == datetime(
        2026, 7, 25, 7, 37, 29, 163000, tzinfo=UTC
    )
