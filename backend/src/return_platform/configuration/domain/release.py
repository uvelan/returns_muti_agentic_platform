from __future__ import annotations

from enum import StrEnum


class ReleaseStatus(StrEnum):
    """Status vocabulary of a configuration *manifest*, not of the live lifecycle.

    Wave D3 retired the Mongo release lifecycle these values were designed for,
    and `APPROVED`/`ACTIVE` no longer name any state the platform transitions
    through. The enum survives because the manifest pipeline still parses a
    `status` field with it (`application/loader.py`) and `ConfigurationRelease`
    carries it as provenance.

    **The live lifecycle is `graph_repository.RELEASE_TRANSITIONS`** --
    DRAFT → VALIDATED → RELEASED → SUPERSEDED → ARCHIVED, in Neo4j. Do not reach
    for this enum to model a promotion; the two vocabularies overlap on three
    names and mean different things.

    (`RELEASE_SERVICE_TRANSITIONS` lived here and went with `ReleaseService`. It
    described DRAFT → VALIDATED → APPROVED, which nothing performs any more.)
    """

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
