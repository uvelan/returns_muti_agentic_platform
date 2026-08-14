"""`TRACKING_TYPES` is a mirror of a database constraint, so it must stay one.

The API refuses an unknown `trackingType` at the request boundary rather than
letting `CK_return_tracking_type` reject the row as a constraint violation a
caller cannot act on. That is only an improvement while the two agree: a Python
copy that drifted would either refuse a value the database accepts, or promise
one it does not, and both failures look like a bug in the endpoint.

Read out of the migration rather than restated here, so this file cannot become
the third copy it exists to prevent.
"""

from __future__ import annotations

import re

from return_platform.configuration.settings import BACKEND_ROOT
from return_platform.operations.sql_business_state import TRACKING_TYPES

_MIGRATION = (
    BACKEND_ROOT
    / "src"
    / "return_platform"
    / "configuration"
    / "sql_migrations"
    / "002_domain_models.sql"
)
_CHECK = re.compile(
    r"CONSTRAINT\s+CK_return_tracking_type\s+CHECK\s*\(\s*tracking_type\s+IN\s*\((?P<values>[^)]*)\)",
    re.IGNORECASE,
)


def _declared_in_the_schema() -> frozenset[str]:
    match = _CHECK.search(_MIGRATION.read_text(encoding="utf-8"))
    assert match is not None, f"CK_return_tracking_type is no longer declared in {_MIGRATION}"
    return frozenset(value.strip().strip("'") for value in match.group("values").split(","))


def test_the_python_vocabulary_is_exactly_the_one_the_database_enforces() -> None:
    assert TRACKING_TYPES == _declared_in_the_schema()
