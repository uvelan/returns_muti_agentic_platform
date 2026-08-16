"""Errors raised by the operational repositories.

Kept in their own module so that `repository.py` and the aggregate mixins it
composes -- `case_repository.py` and any that follow -- can all raise the same
exception class without importing one another. Splitting the case band out of
`repository.py` would otherwise have made the two modules mutually dependent,
and a cycle broken with a deferred import is a cycle that fails on whichever
module happens to be imported first.

`ConcurrencyConflictError` is re-exported from `repository.py`, which is where
every existing caller imports it from; this module is its definition site, not
a second one.
"""

from __future__ import annotations


class ConcurrencyConflictError(RuntimeError):
    pass
