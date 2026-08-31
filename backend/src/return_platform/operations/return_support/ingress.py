"""One internal shape for everything Support says (contracts.md sect. 5).

Support reaches this platform two ways and the plan's first instinct was to let
the second replace the first. The reviewer's fix, which DR-2 accepted, is the
arrangement here instead: **both paths stay, and they normalize into one
contract.** The structured `.../return-outcome` endpoint is always on and never
involves a model; the natural-language `.../work-items/{id}/messages` endpoint
is gated by `support_ingress.nl_enabled`. What comes out of either is a
`NormalizedSupportEvent`, and everything downstream -- record groups to
`record_support_outcome`, loose artifacts to S1's binding module, the relay to
Channel A -- reads that and nothing else.

The property that makes this worth doing is testable and is tested: a
structured reply and a natural-language message that says the same thing
produce the *same business event*. `canonical_business_form()` is that
statement written down. It deliberately omits the fields that describe how the
message arrived -- transport, external id, sequence, causation -- because those
differ by construction and a comparison that included them could never hold.

**Two things this module does not do.** It does not call a model: the
structured path has nothing to ask, and the natural-language path's classify
and extract stages happen later, under S2's analysis record, and are folded in
through `with_analysis`. And it does not decide which record a loose artifact
belongs to -- that is S1's `operations/artifact_binding.py`, whose rules are
code, and this module carries artifacts as *claims* until that module rules on
them.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.support_ingress_configuration import (
    FALLBACK_INTENT,
    SupportIngressConfiguration,
)
from return_platform.operations.artifact_binding import ArtifactType, ExtractedArtifact
from return_platform.operations.support_events import support_return_record

logger = logging.getLogger("return_platform.support_ingress")

#: The transport half of the dedupe identity for the structured endpoint.
#: A named constant because the identity is `(case_id, transport_id,
#: external_message_id)` and a structured reply and an email that happened to
#: carry the same external id are different messages -- which only holds if the
#: structured path's transport has one spelling.
STRUCTURED_TRANSPORT_ID: Final = "return-outcome"

#: The namespace `derive_support_event_id` mints under. Fixed, so the same
#: (case, transport, external id) derives the same internal id in every process
#: and on every replay -- which is what makes the derivation a dedupe rather
#: than a hope.
_SUPPORT_EVENT_NAMESPACE: Final = uuid.UUID("6f0d1a2e-9c3b-4f5a-8d6e-1b2c3d4e5f60")

#: Intents the structured path assigns without asking anything. Both are
#: members of the frozen sect. 5 taxonomy; neither is inferred.
STRUCTURED_ISSUED_INTENT: Final = "rma_issued"
STRUCTURED_REJECTION_INTENT: Final = "rejection"

#: The longest artifact value this platform will carry, matching the widest
#: stored column one lands in (`ReturnOutcomeRecord.labelReference`, 256). An
#: artifact value is a model's reading of support-authored text and reaches an
#: associate's screen, so the bound is code rather than prompt: instructions
#: are advice to a model, and this is a parser.
MAX_ARTIFACT_VALUE_CHARS: Final = 256

#: The longest *claimed* return reference on a loose artifact, matching
#: `ReturnOutcomeRecord.returnReference` (128). A claim longer than any
#: reference the store can hold cannot match one.
MAX_ARTIFACT_BINDING_CHARS: Final = 128


class SupportEventStatus:
    """What became of one inbound event.

    Not a `StrEnum` on the record's behalf -- these are the ingress statuses,
    and S2's `AnalysisStatus` is a different axis (what the *analysis* came to).
    An event can be `ACCEPTED` with its analysis still `PENDING`.
    """

    ACCEPTED: Final = "ACCEPTED"
    #: Persisted while `nl_enabled` is false. Never a refusal (contracts.md
    #: sect. 5): the message is on file and is reprocessed in stream order when
    #: the switch flips.
    PARKED: Final = "PARKED"


def derive_support_event_id(*, case_id: str, transport_id: str, external_message_id: str) -> str:
    """The internal id for one inbound message, derived from its identity.

    Derived rather than minted, because the contract's dedupe key is
    `(case_id, transport_id, external_message_id)` and a random id would make
    every redelivery a new event with the uniqueness constraint powerless to
    say so. uuid5 over the three parts joined by a separator that cannot appear
    in a uuid5 input ambiguously -- the parts are length-prefixed rather than
    merely joined, so `("a", "bc")` and `("ab", "c")` cannot collide.
    """
    parts = (case_id, transport_id, external_message_id)
    encoded = "|".join(f"{len(part)}:{part}" for part in parts)
    return str(uuid.uuid5(_SUPPORT_EVENT_NAMESPACE, encoded))


@dataclass(frozen=True, slots=True)
class SupportSender:
    """Who said it, as the transport reported them.

    Untrusted in exactly the way the body text is: a display name is a string
    a sender chose. Carried so the relay and the digest can attribute the
    message, never used for authorization -- that is the endpoint's capability
    check on the *principal*, which is a different thing entirely.
    """

    sender_id: str
    display_name: str | None = None
    role: str | None = None

    def as_document(self) -> dict[str, Any]:
        return {
            "senderId": self.sender_id,
            "displayName": self.display_name,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class ReturnRecordBinding:
    """One record group, as Support grouped it.

    DR-11: this is the shape that create-or-updates a return record by
    `(caseId, returnReference)` through the existing `record_support_outcome`
    path. It is emphatically *not* a loose artifact -- the grouping is the
    statement that these values belong to this RMA, and the binding rules in
    S1's module exist for the messages that make no such statement.
    """

    return_reference: str
    tracking_reference: str | None = None
    label_reference: str | None = None
    return_location: str | None = None
    shipping_instruction_reference: str | None = None
    return_method: str | None = None
    carrier: str | None = None
    order_line_references: tuple[str, ...] = ()

    def as_support_record(self) -> dict[str, Any]:
        """The signal argument, built by the existing helper.

        Built through `support_return_record` rather than as a dict literal so
        that the key names stay a contract with the workflow dataclass in one
        place. A second literal here is how the NL path would come to send
        `trackingReference` to a field called `tracking_reference`.
        """
        return support_return_record(
            return_reference=self.return_reference,
            tracking_reference=self.tracking_reference,
            label_reference=self.label_reference,
            return_location=self.return_location,
            shipping_instruction_reference=self.shipping_instruction_reference,
            return_method=self.return_method,
            carrier=self.carrier,
            order_line_references=self.order_line_references,
        )


@dataclass(frozen=True, slots=True)
class NormalizedSupportEvent:
    """The internal contract (contracts.md sect. 5), whichever door it came in.

    `stream_sequence` and `causation` are `None` until the enqueuing store
    allocates them: sect. 7 is explicit that ordering fields are *populated
    only by the enqueuing store*, so a value here before the commit would be a
    guess that the store then has to contradict.
    """

    case_id: str
    work_item_id: str
    support_event_id: str
    transport_id: str
    external_message_id: str
    sender: SupportSender
    return_record_bindings: tuple[ReturnRecordBinding, ...] = ()
    artifacts: tuple[ExtractedArtifact, ...] = ()
    intent: str | None = None
    body_text: str | None = None
    rejected: bool = False
    reason: str | None = None
    #: Allocated by the enqueuing store, never by a normalizer.
    stream_sequence: int | None = None
    causation: str | None = None
    status: str = SupportEventStatus.ACCEPTED
    #: Free-form provenance the transport supplied (channel hint, thread ref).
    #: Kept out of the canonical business form: it describes the pipe.
    transport_metadata: Mapping[str, Any] = field(default_factory=dict)

    def with_analysis(
        self,
        *,
        intent: str,
        artifacts: Sequence[ExtractedArtifact] = (),
        bindings: Sequence[ReturnRecordBinding] | None = None,
    ) -> NormalizedSupportEvent:
        """Fold an accepted analysis into the event.

        Returns a new event rather than mutating: the stored event is the
        message as it arrived, and the analysis is a separate committed fact
        under S2's record. An in-place update here would make "what did Support
        say" and "what did the model make of it" the same row, and no later
        reader could separate them.
        """
        return replace(
            self,
            intent=intent,
            artifacts=tuple(artifacts),
            return_record_bindings=(
                self.return_record_bindings if bindings is None else tuple(bindings)
            ),
        )

    def parked(self) -> NormalizedSupportEvent:
        return replace(self, status=SupportEventStatus.PARKED)

    def support_records(self) -> list[dict[str, Any]]:
        """The record groups, in the shape `record_support_outcome` consumes."""
        return [binding.as_support_record() for binding in self.return_record_bindings]

    def canonical_business_form(self) -> dict[str, Any]:
        """What the event *says*, with how it arrived left out.

        This is the definition behind "structured and natural-language
        normalize to identical events". Transport id, external id, internal
        event id, sequence, causation and status are all omitted: they differ
        between the two doors by construction, and a comparison that included
        them would be a comparison that can never hold and therefore never
        catches anything.

        Order is normalised too. Record groups sort by reference and artifacts
        by their `(type, value, binding)` triple, because a list whose order
        depends on which door the message came in is a difference this form
        exists to remove.
        """
        return {
            "caseId": self.case_id,
            "intent": self.intent,
            "rejected": self.rejected,
            "reason": self.reason,
            "records": sorted(
                (binding.as_support_record() for binding in self.return_record_bindings),
                key=lambda record: str(record["return_reference"]),
            ),
            "artifacts": sorted(
                (
                    {
                        "artifactType": artifact.artifact_type.value,
                        "value": artifact.value,
                        "binding": artifact.binding,
                    }
                    for artifact in self.artifacts
                ),
                key=lambda item: (
                    str(item["artifactType"]),
                    str(item["value"]),
                    str(item["binding"] or ""),
                ),
            ),
        }


class SupportInboundMessage(BaseModel):
    """One natural-language message, as a transport hands it over.

    `extra="forbid"`: a transport that starts sending a field this platform has
    never agreed to read should be a failed request rather than a field
    silently dropped on the way to a model.
    """

    model_config = ConfigDict(extra="forbid")

    #: The transport's own id for this message. Half of the dedupe identity, so
    #: it is required -- a transport that cannot name its message cannot have
    #: its redeliveries recognised, and inventing one here would give every
    #: redelivery a fresh identity.
    external_message_id: str = Field(min_length=1, max_length=256)
    body_text: str = Field(min_length=1)
    sender: str = Field(min_length=1, max_length=256)
    sender_display_name: str | None = Field(default=None, max_length=256)
    #: Which transport this arrived on -- email, the support console, a chat
    #: bridge. The other half of the dedupe identity. Two transports carrying
    #: the same words are two messages (contracts.md sect. 5: "distinct
    #: transports = distinct messages"), which is exactly why this is part of
    #: the key rather than metadata beside it.
    channel_hint: str = Field(default="unspecified", min_length=1, max_length=64)


def normalize_return_outcome(
    *,
    case_id: str,
    work_item_id: str,
    support_event_id: str,
    records: Iterable[Mapping[str, Any]],
    rejected: bool,
    reason: str | None,
    sender: SupportSender,
    external_message_id: str | None = None,
) -> NormalizedSupportEvent:
    """The structured door. No model, no inference (contracts.md sect. 5).

    `records` are `ReturnOutcomeRecord`-shaped mappings -- the API model dumped,
    or the equivalent -- taken as a mapping rather than as the pydantic type so
    that `operations/` does not import `api/`. The intent is assigned from the
    payload's own shape and nothing else: a reply that rejects is a
    `rejection`, a reply that carries record groups issued them. Both members
    are in the frozen taxonomy; neither is a guess, which is what "no LLM on
    this path" has to mean if it means anything.
    """
    bindings = tuple(
        ReturnRecordBinding(
            return_reference=str(record["returnReference"]),
            tracking_reference=_optional(record.get("trackingReference")),
            label_reference=_optional(record.get("labelReference")),
            return_location=_optional(record.get("returnLocation")),
            shipping_instruction_reference=_optional(record.get("shippingInstructionReference")),
            return_method=_optional(record.get("returnMethod")),
            carrier=_optional(record.get("carrier")),
            order_line_references=tuple(
                str(line) for line in record.get("orderLineReferences", ())
            ),
        )
        for record in records
    )
    return NormalizedSupportEvent(
        case_id=case_id,
        work_item_id=work_item_id,
        support_event_id=support_event_id,
        transport_id=STRUCTURED_TRANSPORT_ID,
        external_message_id=external_message_id or support_event_id,
        sender=sender,
        return_record_bindings=bindings,
        intent=STRUCTURED_REJECTION_INTENT if rejected else STRUCTURED_ISSUED_INTENT,
        rejected=rejected,
        reason=reason,
    )


def normalize_inbound_message(
    message: SupportInboundMessage,
    *,
    case_id: str,
    work_item_id: str,
) -> NormalizedSupportEvent:
    """The natural-language door, before anything has been asked of a model.

    `intent` and `artifacts` are empty here on purpose. They are the analysis,
    the analysis is a model's answer, and sect. 5 requires that answer to be
    pinned, attempted and CAS-accepted under S2's record before anything reads
    it. A normalizer that guessed an intent from a keyword would be a second,
    unpinned classifier whose output nothing could audit.
    """
    return NormalizedSupportEvent(
        case_id=case_id,
        work_item_id=work_item_id,
        support_event_id=derive_support_event_id(
            case_id=case_id,
            transport_id=message.channel_hint,
            external_message_id=message.external_message_id,
        ),
        transport_id=message.channel_hint,
        external_message_id=message.external_message_id,
        sender=SupportSender(sender_id=message.sender, display_name=message.sender_display_name),
        body_text=message.body_text,
    )


def coerce_intent(candidate: str | None, configuration: SupportIngressConfiguration) -> str:
    """Force a classification into the released taxonomy.

    Contracts.md sect. 5: the set is closed and out-of-set becomes `other`.
    Enforced here, in code, over the *released* list, so widening the config
    widens what is recognised and can never widen what runs -- and so a model
    that returns a plausible-looking intent nobody wrote a branch for lands on
    the branch that exists.
    """
    if candidate is None:
        return FALLBACK_INTENT
    normalized = candidate.strip().lower()
    if normalized in configuration.normalized_intents():
        return normalized
    return FALLBACK_INTENT


def extracted_artifacts(payload: Mapping[str, Any]) -> tuple[ExtractedArtifact, ...]:
    """Read an accepted extraction's artifacts into S1's input type.

    Unknown artifact types are dropped rather than passed through. S1's rules
    are written against a closed `ArtifactType`, and a type nobody has a
    binding rule for cannot be bound, cannot be clarified about, and would
    otherwise arrive at `bind_artifact` as a `KeyError` at persistence time.

    **Values are length-bounded here, in code.** An artifact value is a model's
    reading of support-authored text and it ends up interpolated into a
    clarification an associate reads. Until now the only bounds on it were the
    prompt's instructions and the task's `maximumOutputTokens`, and a prompt is
    not a parser -- so the guarantee was instructed rather than structural. The
    ceilings match the stored columns the same values land in
    (`ReturnOutcomeRecord`: 256 for a label reference, 128 for a return
    reference), because a value the authoritative store cannot hold is not a
    value worth carrying.

    Over-long values are **dropped, not truncated**. A truncated tracking
    number is not a shorter tracking number; it is a different one, and binding
    it would attach the wrong parcel to a real return. Dropping leaves the
    message on file with its raw body intact, which is where a person can still
    read what Support actually wrote.
    """
    raw = payload.get("artifacts")
    if not isinstance(raw, Sequence):
        return ()
    known = {member.value: member for member in ArtifactType}
    artifacts: list[ExtractedArtifact] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        artifact_type = known.get(str(item.get("artifactType", "")).strip().upper())
        value = str(item.get("value", "")).strip()
        if artifact_type is None or not value:
            continue
        if len(value) > MAX_ARTIFACT_VALUE_CHARS:
            logger.warning(
                "support_artifact_value_too_long",
                extra={
                    "artifactType": artifact_type.value,
                    "length": len(value),
                    "limit": MAX_ARTIFACT_VALUE_CHARS,
                },
            )
            continue
        binding = _optional(item.get("binding"))
        if binding is not None and len(binding) > MAX_ARTIFACT_BINDING_CHARS:
            # The binding is a *claim* about which return this belongs to, and
            # a claim longer than any return reference the platform can store
            # cannot match one. Dropped to `None` rather than dropping the
            # artifact: with no reference it falls into S1's no-reference rules
            # and becomes a clarification, which is the honest outcome.
            logger.warning(
                "support_artifact_binding_too_long",
                extra={"length": len(binding), "limit": MAX_ARTIFACT_BINDING_CHARS},
            )
            binding = None
        artifacts.append(
            ExtractedArtifact(
                artifact_type=artifact_type,
                value=value,
                binding=binding,
            )
        )
    return tuple(artifacts)


def record_bindings_from_extraction(
    payload: Mapping[str, Any],
) -> tuple[ReturnRecordBinding, ...]:
    """Read an accepted extraction's record *groups* into the DR-11 shape.

    A group is a statement that these values belong to this RMA. Anything
    without a `returnReference` is not a group -- it is a loose artifact, and
    it belongs on the other path -- so it is skipped here rather than turned
    into a record with an empty reference, which is the create-never rule
    breaking quietly.
    """
    raw = payload.get("records")
    if not isinstance(raw, Sequence):
        return ()
    bindings: list[ReturnRecordBinding] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        reference = str(item.get("returnReference", "")).strip()
        if not reference:
            continue
        bindings.append(
            ReturnRecordBinding(
                return_reference=reference,
                tracking_reference=_optional(item.get("trackingReference")),
                label_reference=_optional(item.get("labelReference")),
                return_location=_optional(item.get("returnLocation")),
                shipping_instruction_reference=_optional(item.get("shippingInstructionReference")),
                return_method=_optional(item.get("returnMethod")),
                carrier=_optional(item.get("carrier")),
                order_line_references=tuple(
                    str(line) for line in item.get("orderLineReferences", ()) or ()
                ),
            )
        )
    return tuple(bindings)


def _optional(value: Any) -> str | None:
    """A blank string is not a statement; neither is whitespace.

    The same rule `_canonical` in `support_events.py` applies when it drops
    nulls before hashing, and for the same reason: `""` is not `None`, so a
    blank sailing through the merge would erase a value Support never
    mentioned.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None
