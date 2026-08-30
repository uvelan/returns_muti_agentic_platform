"""Reusable acceptance infrastructure (ACC brief, contracts.md sect. 3).

Test-side only, and deliberately importable rather than magic: everything here
is a plain function or class that a test can call, with the pytest fixtures
kept as thin wrappers around them. An acceptance scenario that needs a business
calendar or a worker restart should be readable without knowing which conftest
supplied what.

Nothing in this package opens a connection to a datastore. Modules under
`tests/` are AST-scanned by
`tests/platform/test_the_normal_suite_never_needs_live_infrastructure.py`, and
a helper that constructed a driver would have to be live-classified as a whole
-- which would drag every scenario importing it into the live suite whether or
not it needed one. The primitives take their clients and handles as arguments
instead, so the live boundary stays a property of the scenario.
"""
