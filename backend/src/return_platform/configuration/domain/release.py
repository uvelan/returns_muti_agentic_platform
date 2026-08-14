from __future__ import annotations

from enum import StrEnum


class ReleaseStatus(StrEnum):
    """Whether a configuration *manifest* is finished. Not a promotion lifecycle.

    Two values, because two is what the manifest has to distinguish:
    `LegacyCompatibilityAdapter.build_canonical_snapshot` refuses DRAFT and
    serves anything else, and the packaged manifest declares ACTIVE. Those are
    the only values `ConfigurationLoader` has ever parsed out of a real file.

    `VALIDATED`, `APPROVED` and `SUPERSEDED` used to be members too. They were
    the vocabulary of `RELEASE_SERVICE_TRANSITIONS` (DRAFT -> VALIDATED ->
    APPROVED), which went with `ReleaseService` in Wave D3; no module declared
    them, nothing transitioned to them, and the docstring here had to warn
    readers not to reach for this enum to model a promotion. The warning is
    unnecessary now that there is no promotion vocabulary left to reach for --
    and the names are gone from the place they were most dangerous, which is
    that three of them collide with the live lifecycle below while meaning
    something different.

    **The live lifecycle is `graph_repository.RELEASE_TRANSITIONS`** --
    DRAFT -> VALIDATED -> RELEASED -> SUPERSEDED -> ARCHIVED, in Neo4j. That one
    is a promotion, it is enforced by `transition_allowed`, and it is a separate
    vocabulary on purpose: a manifest on disk and a release in the graph are
    different things with different states.
    """

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
