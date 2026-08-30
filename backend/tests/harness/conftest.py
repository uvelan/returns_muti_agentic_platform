"""Fixtures for tests under `tests/harness/`.

Re-export, not redefinition. `pytest_plugins` may only be declared in the
rootdir conftest, and `tests/conftest.py` belongs to the whole suite rather than
to this track -- so the harness fixtures are made available the ordinary way, by
importing them where they are wanted. An acceptance module or its own conftest
does exactly this line.
"""

from __future__ import annotations

from tests.harness.business_calendars import (  # noqa: F401 -- imported to register as fixtures
    business_hours_calendar,
    business_hours_calendar_configuration,
)
