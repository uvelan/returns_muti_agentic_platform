"""When the resolution ladder runs, and what becomes of what it decided.

Contracts.md sect. 9 places the resolver on the **inbound-support-question
path**. Phase 1 built the ladder, the reply gate and the clarification
round-trip, and left that sentence unimplemented: nothing in `src/` constructed
`LadderDependencies` or called `build_resolution_ladder`. This module is the
door.

## Where the trigger sits, and why it is not a new topic

On the **existing** `return-case.support-message.classify` command, as a
continuation of V2's classify dispatch rather than a fifth outbox topic.

Three reasons, in the order they decided it.

1. **Sect. 7's topic list is an enumeration, and enumerations here are closed.**
   It names four new topics. AMENDMENT-1 established that widening a frozen
   enumeration takes an orchestrator amendment and not an implementation --
   sect. 8's binding sources went three to four that way. A slice that quietly
   added a fifth topic would be doing what AMENDMENT-1 exists to forbid.

2. **The classified intent is the trigger's whole input, and it exists exactly
   once, at the end of that dispatch.** A separate topic would have to carry the
   intent in its payload (a second copy of a committed decision, free to
   disagree with the record) or re-read the record anyway.

3. **At-least-once redelivery is already the right retry for this work.** V2's
   analysis reuses its accepted stage results rather than re-invoking, so a
   redelivered classify command re-runs analysis for free; and the ladder is
   checkpointed on `support-resolver:{case_id}:{support_event_id}`, so a
   redelivery *resumes* it. The retry envelope this needs is the one it is
   already inside.

The cost is stated rather than hidden: a resolution failure fails the whole
command, including an analysis half that already committed. That is acceptable
because every write on both sides is idempotent, and it is made *legible* by a
distinct `error_code` -- an operator must be able to tell "the analysis blocked"
from "the analysis was fine and the resolver fell over".

## No V2 file is modified

`ResolvingSupportMessageClassifyDispatcher` **delegates** to V2's dispatcher and
then triggers. The integration change is one call swapped in
`workers/integration_outbox.py`; V2's dispatcher, analyser and wiring are
untouched, and V2 keeps its own factory for a process that wants analysis
without resolution.

## The two conditions, and why intent alone is not enough

`resolution_is_triggered` is pure and its reasons are a closed set, because
"the ladder did not run" is an operational fact somebody will have to explain.

* **the intent must be a released trigger intent** -- `info_request` by default,
  the one member of sect. 5's taxonomy in which Support asks rather than tells;
* **the message must carry prose.** A structured `.../return-outcome` event
  normalises with no `body_text` at all. Its intent can still be a trigger
  intent, and the ladder would then be handed an empty `question_text` and asked
  to answer it -- a model given no question still returns *something*, and the
  gate would carry it to Support under the platform's name.

## What the terminal state becomes

`resolution` -> `gate_reply`, which is where the released `reply_gate` decides
review-or-send. `escalation` -> `support_clarification_requested`, the fact
sect. 9 specifies, composed through `compose_clarification_prompt`.

That composer is phase-1 code with tests and, until now, **no production
caller** -- written, reviewed, merged and never executed. It is the shape the
frontend phase found ~800 lines of. This is its call site.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from langgraph.graph.state import CompiledStateGraph

from return_platform.configuration.support_ingress_configuration import (
    SupportIngressConfiguration,
)
from return_platform.operations.fact_names import SUPPORT_CLARIFICATION_REQUESTED
from return_platform.operations.integrations.outbox import (
    DispatchResult,
    OutboxCommand,
    PermanentDeliveryFailure,
    TransientDeliveryFailure,
)
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.return_support.ingress import coerce_intent
from return_platform.operations.return_support.outbound_composition import (
    DisclosureLike,
    compose_clarification_prompt,
)
from return_platform.operations.return_support.reply_gating import (
    GatedReply,
    ReviewStorePort,
    ScopedFactWriterPort,
    SupportThreadPort,
    gate_reply,
)
from return_platform.operations.return_support.resolution_ladder import (
    AGENT_ID,
    LadderDependencies,
    build_resolution_ladder,
)
from return_platform.operations.return_support.resolution_state import (
    SupportResolverState,
    support_resolver_thread_id,
)

logger = logging.getLogger("return_platform.support_resolution_trigger")

__all__ = [
    "RESOLUTION_FAILED",
    "RESOLVER_CLARIFICATION_NAMESPACE",
    "AnalysisRecordPort",
    "CaseOwnerPort",
    "ResolutionDisposition",
    "ResolutionTriggerOutcome",
    "ResolvingSupportMessageClassifyDispatcher",
    "SupportQuestionResolver",
    "TriggerDecision",
    "TriggerReason",
    "build_support_question_resolver",
    "classified_intent_of",
    "resolution_is_triggered",
    "resolver_clarification_id",
]

#: `error_code` for a command whose analysis committed and whose *resolution*
#: could not complete. Distinct from `SUPPORT_ANALYSIS_BLOCKED` on purpose: the
#: two dead letters need different operator responses, and one code for both
#: would make the queue unreadable.
RESOLUTION_FAILED: Final = "SUPPORT_RESOLUTION_FAILED"

#: A fixed namespace for resolver-raised clarification ids. Its own, not
#: `NAMESPACE_URL` under a shared prefix, so a resolver escalation and V2's
#: artifact clarification on the *same support event* can never mint one id.
RESOLVER_CLARIFICATION_NAMESPACE: Final = uuid.UUID("2c7f4b91-8e05-5a3d-b6c2-70d1e9f4a835")

_CLARIFICATION_SOURCE_PATH: Final = "SUPPORT_QUESTION_RESOLVER"


class TriggerReason:
    """Why the ladder ran, or did not. A closed set; there is no fourth value."""

    ELIGIBLE: Final = "eligible"
    #: The classification is not one the release sends to the resolver.
    INTENT_NOT_TRIGGERING: Final = "intent_not_triggering"
    #: The event carries no prose -- a structured outcome, not a question.
    NO_QUESTION_TEXT: Final = "no_question_text"


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    eligible: bool
    reason: str


def resolution_is_triggered(
    *, intent: str, body_text: str, trigger_intents: Sequence[str]
) -> TriggerDecision:
    """Whether this analysed message reaches the ladder. Pure.

    Ordered intent-first deliberately: a `rma_issued` event with no prose is not
    a question *whatever* its body, and reporting `no_question_text` for it would
    invite someone to conclude that giving it a body would have resolved it.

    `trigger_intents` is passed rather than the whole configuration so that the
    one decision this function makes cannot quietly start depending on a second
    released value.
    """
    if intent not in set(trigger_intents):
        return TriggerDecision(eligible=False, reason=TriggerReason.INTENT_NOT_TRIGGERING)
    if not body_text or not body_text.strip():
        return TriggerDecision(eligible=False, reason=TriggerReason.NO_QUESTION_TEXT)
    return TriggerDecision(eligible=True, reason=TriggerReason.ELIGIBLE)


def resolver_clarification_id(*, case_id: str, support_event_id: str) -> str:
    """One id per support event. Derived, so a retried escalation is one fact."""
    return str(
        uuid.uuid5(
            RESOLVER_CLARIFICATION_NAMESPACE,
            f"support-resolver-clarification:{case_id}:{support_event_id}",
        )
    )


def classified_intent_of(
    record: Mapping[str, Any], configuration: SupportIngressConfiguration
) -> str:
    """The committed classification, coerced through the released taxonomy.

    Read from the **accepted** stage result on the analysis record rather than
    taken from the dispatch's return value, which is the same discipline
    `require_accepted_extraction` enforces on the other stage: the durable
    decision is the one that governs, and a value handed along in memory is free
    to disagree with it after a concurrent worker's answer won the CAS.

    Coerced through the same `coerce_intent` V2 used, over the same released
    list, so the trigger and the classifier can never disagree about what a
    classification *is*.
    """
    accepted = record.get("accepted_classification")
    if not isinstance(accepted, Mapping):
        return coerce_intent(None, configuration)
    raw = accepted.get("intent")
    return coerce_intent(str(raw) if raw is not None else None, configuration)


# --------------------------------------------------------------------- the ports


class AnalysisRecordPort(Protocol):
    """`SupportAnalysisRecordStore.get`, structurally."""

    async def get(self, support_event_id: str) -> Mapping[str, Any]: ...


class InboundStorePort(Protocol):
    """`DurableSupportIngressStore.get_inbound`, structurally."""

    async def get_inbound(self, *, support_event_id: str) -> Mapping[str, Any] | None: ...


class CaseOwnerPort(Protocol):
    """`OperationalRepository.get_case`, structurally.

    Needed for `tenant_id` and `principal_id`, which `gate_reply` passes to
    `ensure_case_support_thread`. These are the **case's own** tenancy and
    owning principal -- read from the case document exactly as
    `return_case_activities.py` reads them when it issues a return -- and are a
    different thing from `LadderDependencies.principal_id`, which is the
    authority a *tool* would run under and which this base does not have.
    """

    async def get_case(self, case_id: str) -> Mapping[str, Any] | None: ...


class ClassifyDispatcherPort(Protocol):
    """V2's `SupportMessageClassifyDispatcher`, structurally."""

    async def dispatch(self, command: OutboxCommand) -> DispatchResult: ...


# ------------------------------------------------------------------ the resolver


class ResolutionDisposition:
    """What one triggered resolution came to."""

    NOT_TRIGGERED: Final = "not_triggered"
    ANSWERED: Final = "answered"
    ESCALATED: Final = "escalated"


@dataclass(frozen=True, slots=True)
class ResolutionTriggerOutcome:
    """Returned so a test can assert, not narrate."""

    disposition: str
    reason: str
    support_event_id: str
    intent: str
    #: Set when the ladder answered and the gate acted on it.
    gated: GatedReply | None = None
    #: Set when the ladder escalated.
    clarification_id: str | None = None
    escalation_reason: str | None = None
    #: True when a prior attempt had already reached a terminal state and this
    #: one read it back rather than re-running the ladder.
    replayed: bool = False


class SupportQuestionResolver:
    """Runs the ladder for one analysed support message, and lands the result.

    Holds the compiled graph rather than rebuilding it per question: the
    topology is a function of the dependencies alone, and rebuilding it per
    message would make an expensive constant look like per-message work.
    """

    def __init__(
        self,
        *,
        dependencies: LadderDependencies,
        ladder: CompiledStateGraph[SupportResolverState, None],
        ingress_configuration: SupportIngressConfiguration,
        cases: CaseOwnerPort,
        reviews: ReviewStorePort,
        threads: SupportThreadPort,
        append_scoped_fact_once: ScopedFactWriterPort,
    ) -> None:
        self._deps = dependencies
        self._ladder = ladder
        self._ingress = ingress_configuration
        self._cases = cases
        self._reviews = reviews
        self._threads = threads
        self._append_scoped_fact_once = append_scoped_fact_once

    @property
    def disclosure(self) -> DisclosureLike | None:
        """The released disclosure line, read per use, never captured.

        `agent_disclosure` rides the release (sect. 9); a copy taken at
        construction would be the line that was in force when the process
        started rather than the one in force now.
        """
        return self._ingress.agent_disclosure

    async def resolve(
        self, *, case_id: str, support_event_id: str, intent: str, question_text: str
    ) -> ResolutionTriggerOutcome:
        """Run (or resume, or read back) the ladder, then act on its terminal.

        **Check-then-act, in three states**, and the check is not decoration.
        LangGraph applies an input state as an *update* to whatever the thread
        already holds, so passing the initial state on a retry would reset
        `llm_invocations_used` to zero and `graph_synced` to `False` -- silently
        defeating both `per_case_llm_budget` and the sync-once guarantee
        acceptance 23 is about, while the run still completed and still looked
        right.
        """
        config = {
            "configurable": {
                "thread_id": support_resolver_thread_id(
                    case_id=case_id, support_event_id=support_event_id
                )
            }
        }
        snapshot = await self._ladder.aget_state(config)
        held = dict(snapshot.values or {})

        if held.get("resolution") or held.get("escalation"):
            # Terminal already. Landing it again is safe -- every write below is
            # append-once or keyed on a derived delivery identity -- and is what
            # makes a redelivery whose *landing* half failed converge.
            final: Mapping[str, Any] = held
            replayed = True
        elif held:
            final = await self._ladder.ainvoke(None, config=config)
            replayed = False
        else:
            final = await self._ladder.ainvoke(
                self._initial_state(
                    case_id=case_id,
                    support_event_id=support_event_id,
                    intent=intent,
                    question_text=question_text,
                ),
                config=config,
            )
            replayed = False

        resolution = final.get("resolution")
        if resolution:
            gated = await self._land_answer(
                resolution,
                case_id=case_id,
                support_event_id=support_event_id,
                intent=intent,
                question_text=question_text,
            )
            return ResolutionTriggerOutcome(
                disposition=ResolutionDisposition.ANSWERED,
                reason=TriggerReason.ELIGIBLE,
                support_event_id=support_event_id,
                intent=intent,
                gated=gated,
                replayed=replayed,
            )

        escalation = dict(final.get("escalation") or {})
        clarification_id = await self._land_escalation(
            escalation,
            case_id=case_id,
            support_event_id=support_event_id,
            question_text=question_text,
        )
        return ResolutionTriggerOutcome(
            disposition=ResolutionDisposition.ESCALATED,
            reason=TriggerReason.ELIGIBLE,
            support_event_id=support_event_id,
            intent=intent,
            clarification_id=clarification_id,
            escalation_reason=str(escalation.get("reason") or ""),
            replayed=replayed,
        )

    def _initial_state(
        self, *, case_id: str, support_event_id: str, intent: str, question_text: str
    ) -> SupportResolverState:
        """The pinned identity one attempt reasons under.

        `run_id` is **derived**, not generated. A fresh uuid per attempt would
        make two resumes of one thread claim to be two runs, and the run id is
        the thing an audit joins a checkpoint to an invocation by.
        """
        return SupportResolverState(
            case_id=case_id,
            support_event_id=support_event_id,
            intent=intent,
            question_text=question_text,
            configuration_release_id=self._deps.resolver.release_id,
            prompt_version=self._deps.resolver.prompt_version,
            agent_id=AGENT_ID,
            run_id=str(
                uuid.uuid5(
                    RESOLVER_CLARIFICATION_NAMESPACE,
                    f"support-resolver-run:{case_id}:{support_event_id}",
                )
            ),
            as_of=datetime.now(UTC).isoformat(),
            rungs_attempted=(),
            consumed_fact_ids=(),
            context_hash="",
            graph_synced=False,
            llm_invocations_used=0,
            budget_exhausted=False,
        )

    async def _land_answer(
        self,
        resolution: Mapping[str, Any],
        *,
        case_id: str,
        support_event_id: str,
        intent: str,
        question_text: str,
    ) -> GatedReply:
        case = await self._cases.get_case(case_id) or {}
        return await gate_reply(
            resolution,
            case_id=case_id,
            support_event_id=support_event_id,
            intent=intent,
            question_text=question_text,
            tenant_id=str(case.get("tenantId") or ""),
            principal_id=str(case.get("principalId") or ""),
            configuration=self._deps.configuration,
            disclosure=self.disclosure,
            reviews=self._reviews,
            threads=self._threads,
            append_scoped_fact_once=self._append_scoped_fact_once,
        )

    async def _land_escalation(
        self,
        escalation: Mapping[str, Any],
        *,
        case_id: str,
        support_event_id: str,
        question_text: str,
    ) -> str:
        """The clarification sect. 9 specifies, from the ladder's own escalation.

        `CHANNEL_B` and `DERIVED`: the question being put to the associate is
        *about* something Support said, and it was computed from the ladder's
        own descent rather than observed or stated by anyone. That matches how
        V2 files its artifact clarifications, which matters because both land in
        the same panel section and a reader must not have to know which
        component wrote one to know what it is.

        Append-once on a derived id, so a redelivered command -- or a resolution
        replayed from a terminal checkpoint -- writes one fact, not one per
        attempt.
        """
        clarification_id = resolver_clarification_id(
            case_id=case_id, support_event_id=support_event_id
        )
        attempts = [str(rung) for rung in escalation.get("resolutionAttempts") or ()]
        needed_field = escalation.get("neededField")
        why = _why_unresolvable(escalation)
        prompt = compose_clarification_prompt(
            verbatim_question=question_text,
            why_unresolvable=why,
            needed_field=str(needed_field) if needed_field else None,
            resolution_attempts=attempts,
        )
        await self._append_scoped_fact_once(
            record_scope=None,
            fact_id=f"{SUPPORT_CLARIFICATION_REQUESTED}-{clarification_id}",
            case_id=case_id,
            fact_name=SUPPORT_CLARIFICATION_REQUESTED,
            value={
                "clarificationId": clarification_id,
                # The composed, neutralised prompt -- what the associate is
                # actually shown -- for the reason V2 stores its composed
                # sentence here: one field, one meaning, across both writers.
                "verbatimQuestion": prompt,
                "whyUnresolvable": why,
                "neededField": str(needed_field) if needed_field else None,
                "resolutionAttempts": attempts,
                "supportEventId": support_event_id,
                "escalationReason": str(escalation.get("reason") or ""),
                "missingEntities": [str(item) for item in escalation.get("missingEntities") or ()],
                "consumedFactIds": [str(item) for item in escalation.get("consumedFactIds") or ()],
                "contextHash": escalation.get("contextHash") or "",
                "choice": "ANSWER",
            },
            agent_id=AGENT_ID,
            channel=FactChannel.CHANNEL_B,
            acquisition_method=FactAcquisition.DERIVED,
            source_system="RETURN_SUPPORT",
            source_path=_CLARIFICATION_SOURCE_PATH,
        )
        return clarification_id


#: One sentence per escalation reason, in the associate's vocabulary rather than
#: the ladder's. Enumerated rather than formatted from the enum value, because
#: `SUB_THRESHOLD` on a console tells a branch associate nothing, and the three
#: cases call for three different actions from them.
_WHY: Final[Mapping[str, str]] = {
    "SUB_THRESHOLD": (
        "the platform found an answer but was not confident enough in it to send it"
    ),
    "CONFLICTING_SOURCES": (
        "two of the platform's own sources gave different answers, so neither was sent"
    ),
    "MISSING_REQUIRED_ENTITY": "the platform is missing a detail it needs to answer this",
    "NO_ELIGIBLE_TOOL": "no released tool covers this question",
    "TOOL_UNAVAILABLE": "the tool that would answer this could not be reached",
    "BUDGET_EXHAUSTED": (
        "this case has used its whole automatic-answering budget, so the rest is manual"
    ),
}
_WHY_UNKNOWN: Final = "the platform could not answer this"


def _why_unresolvable(escalation: Mapping[str, Any]) -> str:
    return _WHY.get(str(escalation.get("reason") or ""), _WHY_UNKNOWN)


# --------------------------------------------------------------- the dispatcher


class ResolvingSupportMessageClassifyDispatcher:
    """V2's classify dispatch, followed by the resolution trigger.

    Delegation rather than modification: V2 owns `analysis_wiring.py`, its
    dead-letter classification is the thing that keeps a blocked analysis from
    retrying forever, and re-implementing that here to add four lines would be
    a second copy of the one piece of code that decides whether a support
    message ever stops.
    """

    def __init__(
        self,
        *,
        analysis: ClassifyDispatcherPort,
        resolver: SupportQuestionResolver,
        records: AnalysisRecordPort,
        inbound: InboundStorePort,
        ingress_configuration: SupportIngressConfiguration,
        trigger_intents: Sequence[str],
    ) -> None:
        self._analysis = analysis
        self._resolver = resolver
        self._records = records
        self._inbound = inbound
        self._ingress = ingress_configuration
        self._trigger_intents = tuple(trigger_intents)

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        """Analyse first, always. Then resolve, if the release says to.

        The delegation is unguarded on purpose: whatever V2's dispatcher raises
        -- `PermanentDeliveryFailure` for a blocked analysis, a transient for an
        outage -- propagates with its own `error_code`, because a resolver
        wrapper must not be able to relabel an analysis failure.
        """
        result = await self._analysis.dispatch(command)
        support_event_id = str(command.payload.get("supportEventId", ""))
        stored = await self._inbound.get_inbound(support_event_id=support_event_id) or {}
        case_id = str(command.payload.get("caseId", stored.get("caseId", "")))
        body_text = str(stored.get("rawBody") or "")

        record = await self._records.get(support_event_id)
        intent = classified_intent_of(record, self._ingress)
        decision = resolution_is_triggered(
            intent=intent, body_text=body_text, trigger_intents=self._trigger_intents
        )
        if not decision.eligible:
            logger.info(
                "support_resolution_not_triggered",
                extra={
                    "caseId": case_id,
                    "supportEventId": support_event_id,
                    "intent": intent,
                    "reason": decision.reason,
                },
            )
            return result

        try:
            outcome = await self._resolver.resolve(
                case_id=case_id,
                support_event_id=support_event_id,
                intent=intent,
                question_text=body_text,
            )
        except (PermanentDeliveryFailure, TransientDeliveryFailure):
            raise
        except (ConnectionError, TimeoutError) as error:
            # An outage is a retry. Named types rather than a bare `except`, for
            # the reason V2's dispatcher gives: a bug in the resolver must not be
            # retried forever as though it were an outage.
            raise TransientDeliveryFailure(str(error)) from error
        except Exception as error:  # noqa: BLE001 - the analysis half already committed
            # A dead letter with its **own** error code. `SUPPORT_ANALYSIS_BLOCKED`
            # would tell an operator to look at a stage that succeeded.
            raise PermanentDeliveryFailure(
                f"support resolution failed for event {support_event_id!r}: {error}",
                error_code=RESOLUTION_FAILED,
            ) from error

        logger.info(
            "support_resolution_triggered",
            extra={
                "caseId": case_id,
                "supportEventId": support_event_id,
                "intent": intent,
                "disposition": outcome.disposition,
                "escalationReason": outcome.escalation_reason,
                "replayed": outcome.replayed,
                "gateOutcome": outcome.gated.outcome if outcome.gated else None,
            },
        )
        return result


def build_support_question_resolver(
    *,
    dependencies: LadderDependencies,
    ingress_configuration: SupportIngressConfiguration,
    cases: CaseOwnerPort,
    reviews: ReviewStorePort,
    threads: SupportThreadPort,
    append_scoped_fact_once: ScopedFactWriterPort,
    checkpointer: Any = None,
) -> SupportQuestionResolver:
    """Compile the ladder once and bind it to what lands its result."""
    return SupportQuestionResolver(
        dependencies=dependencies,
        ladder=build_resolution_ladder(dependencies, checkpointer=checkpointer),
        ingress_configuration=ingress_configuration,
        cases=cases,
        reviews=reviews,
        threads=threads,
        append_scoped_fact_once=append_scoped_fact_once,
    )
