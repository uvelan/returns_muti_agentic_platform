"""Classify a support message, extract from it, and only then write anything.

Contracts.md sect. 5. This is the dispatcher registered against
`return-case.support-message.classify`, and it is where V2's obligations to S2's
analysis record are actually kept. The record is S2's; the invocations that fill
it are V2's; the two rules that make the arrangement worth anything are both
enforced here:

**A stage with an accepted result is never invoked again.** Not "invoked and
then discarded" -- not invoked. `accept_result` returning `is_new=False` already
means the second answer was thrown away, but by then a model has been paid, a
provider has seen the message, and a non-deterministic call has happened on a
retry path. So the accepted result is read *before* the candidate loop, and if
it is there the loop does not run. The dispatcher's at-least-once redelivery
becomes a redelivery of a decision rather than a second decision, which is the
whole claim.

**Nothing durable is written from an attempt.** Artifacts, record groups and the
intent fact all come from `require_accepted_extraction(record)` /
`accepted_classification`. A losing attempt's artifacts sitting on the case
beside the winner's would leave nothing downstream able to say which extraction
the case's own data came from.

What the accepted extraction is then split into follows DR-11 exactly:

* **record groups** -- an RMA with its label, tracking, location -- go to the
  *existing* `record_support_outcome` path, through the standard
  `support_response` signal chain. Nothing new creates a return record.
* **loose artifacts** -- a tracking number with no RMA attached -- go to S1's
  binding module, whose rules are code. `AMBIGUOUS` and `UNMATCHED` produce a
  clarification rather than a guess and *never* a new record.
* the **omc mirror row** is enqueued per bound artifact, keyed by delivery
  identity. sect. 5 says "in the artifact-persistence transaction"; there is no
  such transaction to be in, and `omc_mirror.DurableOmcMirror` states exactly
  what holds instead and why it is weaker than the contract's wording.

The relay to Channel A (DR-3) happens after all of that commits, and is a port
here rather than an import: the transcript belongs to `dynamic_knowledge`, and
an operations module reaching into it would be the coupling this file exists to
avoid.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from return_platform.configuration.support_ingress_configuration import (
    SupportIngressConfiguration,
)
from return_platform.operations.artifact_binding import (
    ARTIFACT_STORED_FIELDS,
    ArtifactBindingDecision,
    BindingStatus,
    ExtractedArtifact,
    ReturnRecordStorePort,
    ScopedFactAppendPort,
    bind_artifacts,
    persist_binding_decision,
)
from return_platform.operations.fact_names import (
    SUPPORT_CLARIFICATION_REQUESTED,
    SUPPORT_MESSAGE_INTENT,
    SUPPORT_MESSAGE_RECEIVED,
)
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.return_support.analysis_records import (
    AnalysisStage,
    require_accepted_extraction,
)
from return_platform.operations.return_support.ingress import (
    ReturnRecordBinding,
    coerce_intent,
    extracted_artifacts,
    record_bindings_from_extraction,
)
from return_platform.operations.return_support.omc_mirror import derive_omc_delivery_id

logger = logging.getLogger("return_platform.support_classification")

#: The omc mirror topic for inbound artifacts (contracts.md sect. 5).
OMC_RETURN_UPDATE_TOPIC: Final = "omc.return.update"

#: What an invocation attempt came to, recorded on the analysis record. Values
#: rather than a bare bool: "the provider was unreachable" and "the provider
#: answered something unusable" are different operational facts and the audit
#: trail has to keep them apart.
ATTEMPT_ACCEPTED: Final = "ACCEPTED"
ATTEMPT_UNAVAILABLE: Final = "UNAVAILABLE"

#: The agent id stamped on every fact this module writes. One value, so a
#: reader can filter the support bridge's derivations out of a case's fact log
#: without matching on fact names.
AGENT_ID: Final = "support-message-analysis"


class RouteUnavailableError(RuntimeError):
    """This candidate route could not answer. Try the next pinned one.

    Distinct from any other failure on purpose: an unusable *answer* is not an
    unavailable route, and treating them alike would burn the whole pinned
    candidate list on one malformed response.
    """


class StageInvokerPort(Protocol):
    """One staged model call, bound to one route.

    A port rather than `StructuredOutputInvoker` directly, because the record
    loop is the thing worth testing and it must be testable with a stub whose
    call count can be asserted. The production adapter is
    `StructuredOutputInvoker`-backed and lives at the wiring site.
    """

    # Read-only, and declared as properties rather than as attributes for a
    # reason that only appeared once a real adapter existed: the production
    # implementation derives all three from the *currently released*
    # configuration on every access, so they are properties, and a Protocol
    # declaring them as settable variables refuses that -- it would be satisfied
    # only by an object that had captured them at construction, which is the
    # thing this slice is trying not to do.
    @property
    def release_id(self) -> str: ...

    @property
    def routing_policy_version(self) -> str: ...

    @property
    def ordered_candidate_routes(self) -> tuple[str, ...]: ...

    async def invoke(self, *, route_id: str, payload: Mapping[str, Any]) -> dict[str, Any]: ...


class SupportEventSignalPort(Protocol):
    """`DurableSupportEventStore.record_support_response`, structurally."""

    async def record_support_response(
        self,
        *,
        case_id: str,
        work_item_id: str,
        support_event_id: str,
        records: Sequence[Mapping[str, Any]],
        rejected: bool,
        reason: str | None,
        workflow_id: str,
        actor_id: str,
        correlation_id: str | None = None,
    ) -> Any: ...


class OmcMirrorPort(Protocol):
    """Enqueue one `omc.return.update` row in the artifact-persistence txn."""

    async def enqueue_omc_update(
        self,
        *,
        case_id: str,
        support_event_id: str,
        delivery_id: str,
        payload: Mapping[str, Any],
    ) -> str: ...


class TranscriptRelayPort(Protocol):
    """Append the typed system entry to the Order Discovery transcript (DR-3).

    A port, so this module never imports `dynamic_knowledge`. The entry is not
    a turn and must not disturb the turn-based agent contract; enforcing that is
    the adapter's job, and the adapter is where the conversation store lives.
    """

    async def append_system_entry(
        self,
        *,
        case_id: str,
        support_event_id: str,
        entry_kind: str,
        return_record_id: str | None,
        payload: Mapping[str, Any],
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """What one dispatch came to. Returned so a test can assert, not narrate."""

    support_event_id: str
    intent: str
    reused_classification: bool
    reused_extraction: bool
    record_group_references: tuple[str, ...] = ()
    bound_artifacts: int = 0
    clarifications: tuple[str, ...] = ()
    relayed_entries: int = 0
    omc_rows: tuple[str, ...] = field(default=())


class SupportMessageAnalyser:
    """The classify/extract loop, and what an accepted extraction becomes."""

    def __init__(
        self,
        *,
        records: Any,
        classifier: StageInvokerPort,
        extractor: StageInvokerPort,
        configuration: SupportIngressConfiguration,
        record_store: ReturnRecordStorePort,
        append_scoped_fact_once: ScopedFactAppendPort,
        support_events: SupportEventSignalPort,
        # Required, and it used to default to `None`. That default was the whole
        # of the omc gap: a wiring site that simply did not mention `omc` got an
        # analyser that silently dropped sect. 5's mirror, and every test passed
        # because every test passed a stub. An absent port must be a wiring
        # error, not a runtime shrug.
        omc: OmcMirrorPort,
        relay: TranscriptRelayPort | None = None,
    ) -> None:
        self._records = records
        self._classifier = classifier
        self._extractor = extractor
        self._configuration = configuration
        self._record_store = record_store
        self._append_scoped_fact_once = append_scoped_fact_once
        self._support_events = support_events
        self._omc = omc
        self._relay = relay

    # ------------------------------------------------------------ the stages

    async def _run_stage(
        self,
        *,
        case_id: str,
        support_event_id: str,
        stage: AnalysisStage,
        invoker: StageInvokerPort,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Pin, then reuse-or-invoke. Returns `(accepted_result, reused)`.

        The pin happens before the accepted-result read and not after, because
        `pin_routing_decision` is idempotent by *keeping the first pin* and a
        second call is therefore free -- while a stage that was accepted without
        ever being pinned is a record that cannot say which providers were
        eligible, and re-pinning it later would be inventing that.
        """
        await self._records.ensure_record(case_id=case_id, support_event_id=support_event_id)
        await self._records.pin_routing_decision(
            support_event_id=support_event_id,
            stage=stage,
            release_id=invoker.release_id,
            routing_policy_version=invoker.routing_policy_version,
            ordered_candidate_routes=invoker.ordered_candidate_routes,
        )
        record = await self._records.get(support_event_id)

        accepted = record.get(_ACCEPTED_FIELD[stage])
        if isinstance(accepted, Mapping) and accepted:
            # The line the whole contract turns on. Not "invoke and discard the
            # answer" -- do not invoke. A retry after a crash mid-analysis is a
            # redelivery of a decision, and a model that is asked again has
            # already made a second one whatever we do with it.
            return dict(accepted), True

        while True:
            route = self._records.next_candidate_route(record, stage)
            if route is None:
                # Blocks the record durably and raises for the dispatcher to
                # classify as permanent. Never falls back to an empty result,
                # which downstream would read as "the message said nothing".
                await self._records.block_exhausted(support_event_id=support_event_id, stage=stage)
            try:
                result = await invoker.invoke(route_id=route, payload=payload)
            except RouteUnavailableError as error:
                await self._records.record_attempt(
                    support_event_id=support_event_id,
                    stage=stage,
                    route_id=route,
                    outcome=ATTEMPT_UNAVAILABLE,
                    detail={"error": str(error)},
                )
                record = await self._records.get(support_event_id)
                continue
            await self._records.record_attempt(
                support_event_id=support_event_id,
                stage=stage,
                route_id=route,
                outcome=ATTEMPT_ACCEPTED,
            )
            committed, is_new = await self._records.accept_result(
                support_event_id=support_event_id,
                stage=stage,
                route_id=route,
                result=result,
            )
            # `is_new=False` means a concurrent worker's answer won and this
            # one was discarded. Theirs is the analysis; ours is not merged and
            # is not preferred for being newer.
            return committed, not is_new

    # ----------------------------------------------------------- the dispatch

    async def analyse(
        self,
        *,
        case_id: str,
        work_item_id: str,
        support_event_id: str,
        workflow_id: str,
        body_text: str,
        correlation_id: str | None = None,
    ) -> AnalysisOutcome:
        """Classify, extract, and commit what the accepted extraction says.

        Safely re-runnable end to end (contracts §3): every write below is
        either CAS'd, append-once on a derived id, or keyed on the delivery
        identity, so a second dispatch of the same command changes nothing.
        """
        classification, reused_classification = await self._run_stage(
            case_id=case_id,
            support_event_id=support_event_id,
            stage=AnalysisStage.CLASSIFICATION,
            invoker=self._classifier,
            payload={"bodyText": body_text, "intents": list(self._configuration.intents)},
        )
        intent = coerce_intent(_optional_str(classification.get("intent")), self._configuration)

        # The stage's own answer is deliberately discarded. It is *not* the
        # source of anything durable -- `require_accepted_extraction` below is,
        # and reading this variable instead would be the gate bypassed by a
        # local. The underscore is the statement, not an apology for it.
        _discarded_extraction, reused_extraction = await self._run_stage(
            case_id=case_id,
            support_event_id=support_event_id,
            stage=AnalysisStage.EXTRACTION,
            invoker=self._extractor,
            payload={"bodyText": body_text, "intent": intent},
        )

        # The gate, and the source of the data, are the same call. A caller
        # cannot take the gate and then read the payload from somewhere else.
        record = await self._records.get(support_event_id)
        source = require_accepted_extraction(record)

        await self._write_message_facts(
            case_id=case_id,
            support_event_id=support_event_id,
            intent=intent,
            body_text=body_text,
        )

        groups = record_bindings_from_extraction(source)
        if groups:
            await self._record_support_outcome(
                case_id=case_id,
                work_item_id=work_item_id,
                support_event_id=support_event_id,
                workflow_id=workflow_id,
                groups=groups,
                correlation_id=correlation_id,
            )

        artifacts = extracted_artifacts(source)
        bound, clarifications, omc_rows = await self._persist_artifacts(
            case_id=case_id,
            support_event_id=support_event_id,
            artifacts=artifacts,
        )

        relayed = await self._relay_to_channel_a(
            case_id=case_id,
            support_event_id=support_event_id,
            intent=intent,
            groups=groups,
            clarifications=clarifications,
        )

        return AnalysisOutcome(
            support_event_id=support_event_id,
            intent=intent,
            reused_classification=reused_classification,
            reused_extraction=reused_extraction,
            record_group_references=tuple(group.return_reference for group in groups),
            bound_artifacts=bound,
            clarifications=clarifications,
            relayed_entries=relayed,
            omc_rows=omc_rows,
        )

    # ------------------------------------------------------------ the writes

    async def _write_message_facts(
        self, *, case_id: str, support_event_id: str, intent: str, body_text: str
    ) -> None:
        """The two case-level facts one analysed message produces.

        **Case-level, and with new fact names only.** A message is addressed to
        the case; the record-scoped facts are the ones the *binding* produces.
        Writing either of these under a legacy fact name with a record scope
        would put a per-record value into `latest_case_facts` where it could
        shadow the case-level value of that name -- the exact shadowing the
        scoped projection exists to prevent (contracts.md sect. 4).
        """
        await self._append_scoped_fact_once(
            record_scope=None,
            fact_id=f"{SUPPORT_MESSAGE_RECEIVED}-{support_event_id}",
            case_id=case_id,
            fact_name=SUPPORT_MESSAGE_RECEIVED,
            value={"supportEventId": support_event_id, "bodyText": body_text},
            agent_id=AGENT_ID,
            channel=FactChannel.CHANNEL_B,
            acquisition_method=FactAcquisition.OBSERVED,
            source_system="RETURN_SUPPORT",
            source_path="SUPPORT_INGRESS",
        )
        await self._append_scoped_fact_once(
            record_scope=None,
            fact_id=f"{SUPPORT_MESSAGE_INTENT}-{support_event_id}",
            case_id=case_id,
            fact_name=SUPPORT_MESSAGE_INTENT,
            value={"supportEventId": support_event_id, "intent": intent},
            agent_id=AGENT_ID,
            channel=FactChannel.CHANNEL_B,
            # A model's answer, and labelled as one. `DERIVED` would claim it
            # was computed from other facts, which is the trust distinction
            # `FactAcquisition` exists to keep.
            acquisition_method=FactAcquisition.INFERRED,
            source_system="RETURN_SUPPORT",
            source_path="SUPPORT_MESSAGE_CLASSIFY",
        )

    async def _record_support_outcome(
        self,
        *,
        case_id: str,
        work_item_id: str,
        support_event_id: str,
        workflow_id: str,
        groups: Sequence[ReturnRecordBinding],
        correlation_id: str | None,
    ) -> None:
        """Record groups take the path they already took (DR-11).

        Through `DurableSupportEventStore`, on the `support_response` signal
        chain, into `record_support_outcome`'s create-or-update by
        `(caseId, returnReference)`. Nothing here creates a return record, and
        nothing here is a second implementation of the semantics that path
        already has -- which is what "the existing path, unchanged, already
        multi-RMA-safe" has to mean.

        The support event id is reused verbatim, so a redelivered classify
        command produces the same event identity and is absorbed as a duplicate
        rather than issuing the RMA twice.
        """
        await self._support_events.record_support_response(
            case_id=case_id,
            work_item_id=work_item_id,
            support_event_id=support_event_id,
            records=[group.as_support_record() for group in groups],
            rejected=False,
            reason=None,
            workflow_id=workflow_id,
            actor_id=AGENT_ID,
            correlation_id=correlation_id,
        )

    async def _persist_artifacts(
        self,
        *,
        case_id: str,
        support_event_id: str,
        artifacts: Sequence[ExtractedArtifact],
    ) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
        """Bind loose artifacts through S1's module and mirror what bound.

        The decisions come from `bind_artifacts` and the persistence from
        `persist_binding_decision`; neither rule is restated here. That is the
        ownership boundary and also the correctness argument -- a second
        implementation of "names an unknown reference is UNMATCHED, never a new
        record" is a second chance to get DR-11 wrong.
        """
        if not artifacts:
            return 0, (), ()
        stored = await self._record_store.list_return_records(case_id)
        decisions = bind_artifacts(artifacts, stored)

        bound = 0
        clarifications: list[str] = []
        omc_rows: list[str] = []
        for index, decision in enumerate(decisions):
            dedupe_key = f"{support_event_id}-{index}"
            wrote = await persist_binding_decision(
                decision,
                case_id=case_id,
                dedupe_key=dedupe_key,
                records=self._record_store,
                append_scoped_fact_once=self._append_scoped_fact_once,
            )
            if decision.status is BindingStatus.BOUND:
                if wrote:
                    bound += 1
                # Gated on the *decision*, not on `wrote`. `wrote` is false on
                # every redelivery -- the merge finds the value already on the
                # record -- so a mirror gated on it is a mirror that is lost for
                # good the moment a crash lands between the merge and the
                # enqueue. That is the exact window sect. 5's "in the
                # artifact-persistence transaction" was meant to close, and the
                # transaction does not exist (see `omc_mirror.DurableOmcMirror`).
                # The decision is a pure function of the accepted extraction, so
                # it is identical on every attempt and the rerun completes.
                row = await self._mirror_to_omc(
                    case_id=case_id,
                    support_event_id=support_event_id,
                    decision=decision,
                )
                if row is not None:
                    omc_rows.append(row)
                continue
            clarification_id = await self._request_clarification(
                case_id=case_id,
                support_event_id=support_event_id,
                dedupe_key=dedupe_key,
                decision=decision,
            )
            clarifications.append(clarification_id)
        return bound, tuple(clarifications), tuple(omc_rows)

    async def _mirror_to_omc(
        self,
        *,
        case_id: str,
        support_event_id: str,
        decision: ArtifactBindingDecision,
    ) -> str | None:
        """One `omc.return.update` row per bound artifact, keyed by delivery id.

        Derived rather than random, for the reason every identity in this
        programme is derived: a random delivery id on a retried dispatch is a
        second mirror row for one business change, and the receiver has nothing
        to dedupe on. The derivation and its two design decisions live in
        `omc_mirror.derive_omc_delivery_id`.

        Two bound decisions carry nothing to mirror and are skipped here rather
        than sent as empty updates. Both conditions are properties of the
        decision, so they hold identically on a redelivery:

        * an artifact type with no stored field -- a bound **RMA** confirms which
          record this is and adds no data to it;
        * a blank value, which `_merge_bound_artifact` reads as the absence of a
          statement and this reads the same way.
        """
        if ARTIFACT_STORED_FIELDS[decision.artifact.artifact_type] is None:
            return None
        if not decision.artifact.value.strip():
            return None
        record_id = str(decision.return_record_id or "")
        delivery_id = derive_omc_delivery_id(
            case_id=case_id,
            support_event_id=support_event_id,
            return_record_id=record_id,
            artifact_type=decision.artifact.artifact_type.value,
            value=decision.artifact.value,
        )
        return await self._omc.enqueue_omc_update(
            case_id=case_id,
            support_event_id=support_event_id,
            delivery_id=delivery_id,
            payload={
                "caseId": case_id,
                "returnRecordId": decision.return_record_id,
                "artifactType": decision.artifact.artifact_type.value,
                "value": decision.artifact.value,
                "supportEventId": support_event_id,
            },
        )

    async def _request_clarification(
        self,
        *,
        case_id: str,
        support_event_id: str,
        dedupe_key: str,
        decision: ArtifactBindingDecision,
    ) -> str:
        """The map-or-reject question, as a fact. V3 owns the answer.

        Carries what sect. 9 requires an unmatched-artifact clarification to
        carry: the value, the evidence span, the candidate records, and the
        choice.

        **The frame is platform-composed; the interpolated value is not.** Every
        word of the question except the artifact's own value and type comes from
        this function -- the message body never reaches it, so support cannot
        author the sentence a person is asked. What *is* support-derived is the
        value itself, necessarily: a clarification that would not say which
        tracking number it is about would be unanswerable. That value is
        length-bounded in code by `extracted_artifacts` rather than by the
        prompt, and phase 2 must render it as **data, never as markup or
        preformatted text** where a newline could restructure the view.
        """
        clarification_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"support-clarification:{case_id}:{dedupe_key}")
        )
        candidates = list(decision.candidate_record_ids)
        artifact = decision.artifact
        if decision.status is BindingStatus.AMBIGUOUS:
            question = (
                f"Support gave a {artifact.artifact_type.value.replace('_', ' ').lower()} "
                f"({artifact.value}) without saying which return it belongs to, and this "
                f"case has {len(candidates)} returns. Which one is it for?"
            )
            why = "the artifact names no return reference and the case holds several records"
        else:
            question = (
                f"Support gave a {artifact.artifact_type.value.replace('_', ' ').lower()} "
                f"({artifact.value}) for a return this case does not hold. Map it to one "
                "of this case's returns, or reject it."
            )
            why = decision.reason or "the named return reference is not on this case"

        await self._append_scoped_fact_once(
            record_scope=None,
            fact_id=f"{SUPPORT_CLARIFICATION_REQUESTED}-{clarification_id}",
            case_id=case_id,
            fact_name=SUPPORT_CLARIFICATION_REQUESTED,
            value={
                "clarificationId": clarification_id,
                "verbatimQuestion": question,
                "whyUnresolvable": why,
                "neededField": artifact.artifact_type.value,
                "resolutionAttempts": [decision.status.value],
                "supportEventId": support_event_id,
                "artifactValue": artifact.value,
                # The evidence span: what the message named, not what it said.
                "evidenceSpan": artifact.named_reference(),
                "candidateRecordIds": candidates,
                "choice": "MAP_OR_REJECT",
            },
            agent_id=AGENT_ID,
            channel=FactChannel.CHANNEL_B,
            acquisition_method=FactAcquisition.DERIVED,
            source_system="RETURN_SUPPORT",
            source_path="ARTIFACT_BINDING",
        )
        return clarification_id

    async def _relay_to_channel_a(
        self,
        *,
        case_id: str,
        support_event_id: str,
        intent: str,
        groups: Sequence[ReturnRecordBinding],
        clarifications: Sequence[str],
    ) -> int:
        """Typed system entries on the Order Discovery transcript (DR-3).

        **One entry per record**, with the configured do-not-mix framing key,
        because a case with two RMAs going to two places is exactly the case
        where a single merged entry gets a label attached to the wrong return.
        A case with no record groups still relays once, so an associate is told
        Support replied even when the reply changed no record.

        Append-once is the adapter's contract (the entry id is derived from the
        event and the record), so a redelivered classify command appends
        nothing new. That is why "the transcript entry is appended once" holds
        across the at-least-once dispatcher.
        """
        if self._relay is None:
            return 0
        framing = self._configuration.multi_record_framing_prompt_key
        appended = 0
        targets: list[str | None] = [group.return_reference for group in groups] or [None]
        for target in targets:
            wrote = await self._relay.append_system_entry(
                case_id=case_id,
                support_event_id=support_event_id,
                entry_kind=SUPPORT_UPDATE_ENTRY_KIND,
                return_record_id=target,
                payload={
                    "supportEventId": support_event_id,
                    "intent": intent,
                    "returnReference": target,
                    "clarificationIds": list(clarifications),
                    "multiRecord": len(targets) > 1,
                    "framingPromptKey": framing if len(targets) > 1 else None,
                },
            )
            if wrote:
                appended += 1
        return appended


#: The transcript entry kind the relay appends (DR-3). A *kind*, not a turn:
#: the Order Discovery agent's contract is turn-based and a system entry that
#: presented as a turn would put words in the agent's mouth on the next replay.
SUPPORT_UPDATE_ENTRY_KIND: Final = "SUPPORT_UPDATE"

#: Mirrored from S2's private stage-field map. Mirrored rather than imported
#: because it is private to that module; a test pins the two together so they
#: cannot drift apart silently.
_ACCEPTED_FIELD: Final[Mapping[AnalysisStage, str]] = {
    AnalysisStage.CLASSIFICATION: "accepted_classification",
    AnalysisStage.EXTRACTION: "accepted_extraction",
}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
