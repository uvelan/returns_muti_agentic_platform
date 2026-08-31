"""Fixtures for the acceptance scenarios.

Re-export, not redefinition -- the same construction `tests/harness/conftest.py`
uses and for the same reason. The review-gate suite already builds the three
things every gate-driven scenario needs (the released configuration, the Mongo
double, and a `ReviewAggregateStore` with its indexes ensured), and rebuilding
them here would create a second definition of "a store the gate can be driven
against" that would drift on the first change to either.

They live in a conftest rather than being imported into each scenario module so
that a module can name a *parameter* `store` or `configuration` without
shadowing the fixture it is asking for -- which is what happens when a fixture
function and a test parameter share a namespace.
"""

from __future__ import annotations

from tests.test_support_template_review_gate import (  # noqa: F401 -- registered as fixtures
    configuration,
    mongo,
    store,
)
