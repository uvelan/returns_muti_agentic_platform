"""Canonical case-fact names (contracts.md sect. 4).

The single home for the fact-name vocabulary the support bridge writes: a
string literal of one of these names anywhere else in the tree is an RV
blocking finding, precisely so a rename is a one-line change and a typo is an
import error rather than a fact nobody ever reads back.

Deliberately sparse. A constant lands here in the same change as its first
real reference -- an unused name is vocabulary nothing enforces, and the
contract forbids shipping one. Later slices append their own as they earn
them.
"""

from typing import Final

#: A loose artifact that names no return reference on a case holding several
#: records -- it could belong to any of them, and code must not guess
#: (contracts.md sect. 4 binding rules). Scoped to no record by construction.
SUPPORT_ARTIFACT_AMBIGUOUS: Final[str] = "support_artifact_ambiguous"

#: A loose artifact that names a return reference the case does not hold --
#: or that no record could take at all. Never creates a record; it waits for
#: the map-or-reject clarification.
SUPPORT_ARTIFACT_UNMATCHED: Final[str] = "support_artifact_unmatched"
