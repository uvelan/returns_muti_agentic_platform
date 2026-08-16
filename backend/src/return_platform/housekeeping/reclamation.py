"""What one reclamation pass did, and why it did not do more.

Every reclaimer answers with the same record because every debris class here
fails in the same way when it goes wrong: quietly. A pass that examined nothing
because it was gated off, a pass that examined two hundred candidates and
rejected all of them, and a pass that reclaimed two hundred resources are three
very different operational facts, and a bare count cannot tell them apart.

`skipped_reason` is therefore not decoration. A housekeeping worker deployed
into production runs its Temporal and probe-database reclaimers every interval
and reclaims nothing, forever, on purpose -- and an operator asking why must be
able to read the answer rather than infer it from a zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["ReclamationOutcome"]


@dataclass(frozen=True, slots=True)
class ReclamationOutcome:
    """One reclaimer's report from one pass."""

    #: Which debris class this is, for the log line and for the cycle summary.
    resource_class: str
    #: Candidates the pass looked at, before any eligibility rule was applied.
    examined: int = 0
    #: Resources actually removed.
    reclaimed: int = 0
    #: Identifiers of what was removed. Bounded by the reclaimer's batch limit,
    #: which is what keeps this from becoming an unbounded log payload.
    reclaimed_ids: tuple[str, ...] = ()
    #: Candidates that were eligible but whose removal failed. Not an error for
    #: the pass: the next pass retries them, and a probe database that is still
    #: connected is *supposed* to survive.
    failed: int = 0
    #: Set when the reclaimer did not run at all -- the gate it is behind, or
    #: the dependency it needs. `None` means the pass ran.
    skipped_reason: str | None = None
    #: Per-reclaimer detail an operator needs and a count cannot carry.
    details: dict[str, int] = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        return self.skipped_reason is None

    @classmethod
    def skipped(cls, resource_class: str, reason: str) -> ReclamationOutcome:
        return cls(resource_class=resource_class, skipped_reason=reason)
