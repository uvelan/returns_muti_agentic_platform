"""Loose-artifact binding (contracts.md sect. 4, DR-11). Owned by S1.

Support mentions things two ways. A *record grouping* -- "here is RMA-1, its
label and its tracking" -- flows through `record_support_outcome`'s existing
create-or-update path and never comes here. A *loose artifact* is the other
way: a tracking number, label, location or instruction mentioned without a
grouping, and the question is which of the case's records it belongs to.

The rules are code, never a prompt, and they are exactly the contract's four:

* names a reference the case holds -> **BOUND** to that record;
* names a reference the case does not hold -> **UNMATCHED** -- a
  map-or-reject clarification, *never* a new record;
* names no reference and exactly one record exists -> **BOUND** to it;
* names no reference and several exist -> **AMBIGUOUS** -- a clarification.

A caseless corner the contract leaves open is closed conservatively here: no
reference and *no* records is UNMATCHED, because both binding and
disambiguation are impossible and creating a record is forbidden.

V2 consumes this module and may not edit it. The persistence half writes
through ports so it composes with the shipped repository and with the scoped
fact path (`append_scoped_fact_once`) without this module importing either.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.fact_names import (
    SUPPORT_ARTIFACT_AMBIGUOUS,
    SUPPORT_ARTIFACT_UNMATCHED,
)
from return_platform.operations.models import FactAcquisition, FactChannel


class ArtifactType(StrEnum):
    """What kind of thing Support mentioned."""

    RMA = "RMA"
    TRACKING = "TRACKING"
    LABEL = "LABEL"
    SHIPPING_INSTRUCTION = "SHIPPING_INSTRUCTION"
    RETURN_LOCATION = "RETURN_LOCATION"


class BindingStatus(StrEnum):
    BOUND = "BOUND"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"


#: Stored return-record field each artifact type lands in when it binds --
#: the same stored keys `RETURN_RECORD_MERGED_FIELDS` merges, and the same
#: semantics (this field if the artifact gave one; null never overwrites).
#: Mirrored here rather than imported because that constant lives in the
#: workflows layer, which will import *this* module; a test pins the two
#: against each other so they cannot drift apart silently. An RMA artifact
#: has no field: its value *is* the record's identity, so binding one is a
#: statement about which record it is, not new data to merge onto it.
ARTIFACT_STORED_FIELDS: Final[dict[ArtifactType, str | None]] = {
    ArtifactType.RMA: None,
    ArtifactType.TRACKING: "trackingReference",
    ArtifactType.LABEL: "labelReference",
    ArtifactType.SHIPPING_INSTRUCTION: "shippingInstructionReference",
    ArtifactType.RETURN_LOCATION: "returnLocation",
}


@dataclass(frozen=True)
class ExtractedArtifact:
    """One loose artifact, as ingress extraction hands it over.

    `binding` is the return reference the artifact *names* -- the RMA the
    surrounding text attached it to -- when it names one. It is a claim to be
    checked against the case's records, never trusted as an id.
    """

    artifact_type: ArtifactType
    value: str
    binding: str | None = None

    def named_reference(self) -> str | None:
        """The reference this artifact names, if any.

        An RMA artifact names itself: its value is a return reference by
        definition, so it can never fall into the no-reference rules.
        """
        if self.binding is not None and self.binding.strip():
            return self.binding.strip()
        if self.artifact_type is ArtifactType.RMA:
            return self.value.strip() or None
        return None


@dataclass(frozen=True)
class ArtifactBindingDecision:
    """The outcome of the rules for one artifact. Pure data, no side effects."""

    artifact: ExtractedArtifact
    status: BindingStatus
    #: Set exactly when `status` is BOUND.
    return_record_id: str | None = None
    #: The records an AMBIGUOUS artifact could belong to, in stored order.
    candidate_record_ids: tuple[str, ...] = ()
    #: Why an UNMATCHED artifact did not bind -- for the clarification text.
    reason: str | None = None


def bind_artifact(
    artifact: ExtractedArtifact, records: Sequence[Mapping[str, Any]]
) -> ArtifactBindingDecision:
    """Apply the contract's binding rules to one artifact. Pure.

    `records` are the case's stored return-record documents, exactly as
    `list_return_records` returns them -- keyed by `returnReference` here the
    way `_plan_support_outcome` keys them, because the RMA is the business
    identity and the minted id only names an attempt.
    """
    by_reference = {
        str(document["returnReference"]): document
        for document in records
        if document.get("returnReference")
    }
    reference = artifact.named_reference()
    if reference is not None:
        matched = by_reference.get(reference)
        if matched is not None:
            return ArtifactBindingDecision(
                artifact=artifact,
                status=BindingStatus.BOUND,
                return_record_id=str(matched["returnRecordId"]),
            )
        return ArtifactBindingDecision(
            artifact=artifact,
            status=BindingStatus.UNMATCHED,
            reason=f"names return reference {reference!r}, which this case does not hold",
        )
    if len(records) == 1:
        return ArtifactBindingDecision(
            artifact=artifact,
            status=BindingStatus.BOUND,
            return_record_id=str(records[0]["returnRecordId"]),
        )
    if not records:
        return ArtifactBindingDecision(
            artifact=artifact,
            status=BindingStatus.UNMATCHED,
            reason="names no return reference and the case holds no records",
        )
    return ArtifactBindingDecision(
        artifact=artifact,
        status=BindingStatus.AMBIGUOUS,
        candidate_record_ids=tuple(str(document["returnRecordId"]) for document in records),
    )


def bind_artifacts(
    artifacts: Sequence[ExtractedArtifact], records: Sequence[Mapping[str, Any]]
) -> tuple[ArtifactBindingDecision, ...]:
    """Every artifact of one message against one read of the records."""
    return tuple(bind_artifact(artifact, records) for artifact in artifacts)


class ReturnRecordStorePort(Protocol):
    """The two repository reads/writes persistence needs. `CaseRepository`
    satisfies it structurally."""

    async def list_return_records(self, case_id: str) -> list[dict[str, Any]]: ...

    async def update_return_record(
        self, return_record_id: str, updates: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]: ...


class ScopedFactAppendPort(Protocol):
    """`ReturnCaseActivities.append_scoped_fact_once`, structurally."""

    async def __call__(self, *, record_scope: str | None, **fact: Any) -> bool: ...


async def persist_binding_decision(
    decision: ArtifactBindingDecision,
    *,
    case_id: str,
    dedupe_key: str,
    records: ReturnRecordStorePort,
    append_scoped_fact_once: ScopedFactAppendPort,
) -> bool:
    """Make one decision durable. Returns whether anything was written.

    BOUND merges the artifact's value onto its record under the
    `RETURN_RECORD_MERGED_FIELDS` semantics: this field, because the artifact
    gave one; a value the record already holds is a redelivery and writes
    nothing (so no revision moves and no client re-renders over nothing); no
    other field is touched, so a null can never overwrite. The optimistic
    write retries exactly once on a version conflict, re-reading first --
    `_update_record_once_retried`'s shape -- and a second conflict propagates
    to the caller's retry policy.

    AMBIGUOUS and UNMATCHED never touch a record. Each writes its scoped fact
    (case-level scope: the whole point is that no record owns the artifact
    yet) through the append-once path, so a redelivery under the same
    `dedupe_key` is absorbed, and the clarification flow reads the payload --
    value, what it names, the candidates, the reason.
    """
    if decision.status is BindingStatus.BOUND:
        return await _merge_bound_artifact(decision, case_id=case_id, records=records)
    fact_name = (
        SUPPORT_ARTIFACT_AMBIGUOUS
        if decision.status is BindingStatus.AMBIGUOUS
        else SUPPORT_ARTIFACT_UNMATCHED
    )
    return await append_scoped_fact_once(
        record_scope=None,
        fact_id=f"{fact_name}-{case_id}-{dedupe_key}",
        case_id=case_id,
        fact_name=fact_name,
        value={
            "artifactType": decision.artifact.artifact_type.value,
            "value": decision.artifact.value,
            "namedReference": decision.artifact.named_reference(),
            "candidateRecordIds": list(decision.candidate_record_ids),
            "reason": decision.reason,
        },
        agent_id="artifact-binding",
        channel=FactChannel.CHANNEL_B,
        acquisition_method=FactAcquisition.DERIVED,
        source_system="RETURN_SUPPORT",
        source_path="ARTIFACT_BINDING",
    )


async def _merge_bound_artifact(
    decision: ArtifactBindingDecision, *, case_id: str, records: ReturnRecordStorePort
) -> bool:
    stored_key = ARTIFACT_STORED_FIELDS[decision.artifact.artifact_type]
    if stored_key is None:
        # A bound RMA artifact confirms identity and carries nothing to merge.
        return False
    value = decision.artifact.value.strip()
    if not value:
        # The absence of a statement, exactly as the merged-fields docstring
        # reads a null: nothing to write, nothing to overwrite.
        return False

    async def _attempt() -> bool | None:
        stored = next(
            (
                document
                for document in await records.list_return_records(case_id)
                if str(document["returnRecordId"]) == decision.return_record_id
            ),
            None,
        )
        if stored is None:
            raise LookupError(
                f"bound record {decision.return_record_id!r} not on case {case_id!r}"
            )
        if stored.get(stored_key) == value:
            # A redelivery, or a notice repeating what the record already says.
            return False
        try:
            await records.update_return_record(
                str(decision.return_record_id),
                {stored_key: value},
                expected_version=int(stored.get("version", 0)),
            )
        except ConcurrencyConflictError:
            return None  # lost the race; re-read and try once more
        return True

    outcome = await _attempt()
    if outcome is not None:
        return outcome
    retried = await _attempt()
    if retried is None:
        # Two conflicts on one merge: let the caller's retry policy re-run
        # the whole read-plan-write rather than spinning here.
        raise ConcurrencyConflictError(str(decision.return_record_id))
    return retried
