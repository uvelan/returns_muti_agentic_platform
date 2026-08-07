from __future__ import annotations

from enum import StrEnum
from typing import Mapping


class ReleaseStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


# Transitions owned exclusively by ReleaseService (pre-activation).
# APPROVED -> ACTIVE and ACTIVE -> SUPERSEDED are owned exclusively by ActivationService.
RELEASE_SERVICE_TRANSITIONS: Mapping[ReleaseStatus, frozenset[ReleaseStatus]] = {
    ReleaseStatus.DRAFT: frozenset({ReleaseStatus.VALIDATED}),
    ReleaseStatus.VALIDATED: frozenset({ReleaseStatus.APPROVED, ReleaseStatus.DRAFT}),
}
