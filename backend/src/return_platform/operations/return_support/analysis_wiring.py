"""What plugs `SupportMessageAnalyser` into the shipped gateway and outbox.

Three small pieces, deliberately kept out of the analyser itself so that the
record loop -- the part with the contract in it -- can be tested against stubs
whose call counts are the assertion:

* `SupportAnalysisEnvelope`, the gateway's standard `{decision, explanation,
  confidenceMillionths}` reply with `explanation` parsed. `explanation` carries
  compact JSON rather than fields of its own because that is the envelope every
  other task in `ai_gateway.yaml` returns, and inventing a second envelope for
  two tasks would be a second thing for the dispatch boundary to know about.
* `StructuredStageInvoker`, the `StageInvokerPort` adapter over
  `StructuredOutputInvoker`.
* `SupportMessageClassifyDispatcher`, the `TopicDispatcher` the integration
  agent registers against `return-case.support-message.classify`. One line at
  the wiring site; every decision it makes is here.

**One honest limitation, recorded rather than papered over.** Contracts sect. 5
asks each stage to pin `ordered_candidate_routes[]` before invoking and to store
each attempt with *its* selected route. `StructuredOutputInvoker` routes
internally: a caller cannot say "use this route and only this route". So the
pinned list here is the released task's `allowedProviders`, **in declaration
order** -- which is exactly the fact the pin exists to record ("which providers
were eligible", not "which one answered") -- and the attempt stores the pinned
candidate it was made against, with the provider that actually answered beside
it in `detail`. Constraining the invoker to one route would mean changing shared
AI-gateway code, which is not this slice's to change. Registered as a follow-up.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
)
from return_platform.operations.integrations.outbox import (
    DispatchResult,
    OutboxCommand,
    PermanentDeliveryFailure,
    TransientDeliveryFailure,
)
from return_platform.operations.return_support.analysis_records import (
    CandidateRoutesExhaustedError,
)
from return_platform.operations.return_support.message_classification import (
    RouteUnavailableError,
    SupportMessageAnalyser,
)

logger = logging.getLogger("return_platform.support_analysis_wiring")

#: `error_code` for a classify command whose event has no stored message. The
#: command names an event that does not exist, so no number of retries will
#: make it dispatchable -- it is a dead letter, not an outage.
UNKNOWN_SUPPORT_EVENT: Final = "UNKNOWN_SUPPORT_EVENT"

#: `error_code` for an analysis that blocked with every pinned candidate tried.
#: The record is already `BLOCKED` and on the operations surface by the time
#: this is raised; the dead letter is how the *command* stops.
ANALYSIS_BLOCKED: Final = "SUPPORT_ANALYSIS_BLOCKED"


class SupportAnalysisEnvelope(BaseModel):
    """The gateway's standard reply, with the payload still a string.

    `explanation` is parsed by `parsed_explanation` rather than by a validator,
    because a model that returns unparseable JSON here has produced an unusable
    *answer*, not an unavailable route, and the two must reach the record
    differently: one is an attempt that failed, the other burns a candidate.
    """

    model_config = ConfigDict(extra="forbid")

    decision: str
    explanation: str
    confidenceMillionths: int = Field(ge=0, le=1_000_000)

    def parsed_explanation(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.explanation)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


class _DispatcherLike(Protocol):
    """Just the live-configuration read. `FinalDispatcher` satisfies it."""

    @property
    def configuration(self) -> Any: ...


class _InvokerLike(Protocol):
    """Read-only throughout: every member the real invoker exposes here is a
    property that re-resolves the released document, so declaring them as
    settable variables would be satisfied only by an object that had captured
    them at construction -- the thing this adapter exists not to do."""

    @property
    def task(self) -> Any: ...

    #: The dispatch boundary, whose `configuration` property is the AI gateway
    #: document **in force now** -- `AIRoutePool.replace_routes` swaps routes and
    #: configuration together under the pool's own lock, so this is the one
    #: object in the process that is already atomic across a release activation.
    @property
    def dispatcher(self) -> _DispatcherLike: ...

    async def invoke(
        self,
        *,
        payload: Mapping[str, Any],
        size_probe: str,
        log_context: Mapping[str, Any],
    ) -> Any: ...


#: The task fields that decide **where a call can go**, and only those.
#:
#: `tier` and `allowedProviders` filter the candidate routes outright;
#: `allowTierEscalation` widens that filter; `fallbackStrategy` /
#: `fallbackTemplate` decide what happens when every candidate is refused; and
#: `maximumInputTokens` is what `AIGatewayConfiguration.context_shortfall`
#: compares a model's declared window against to rule a route out per selection.
#:
#: Deliberately **not** here: `promptVersion` and the prompt itself (that is
#: `release_id`, pinned separately on the same record, and folding it in here
#: would make the two fields say one thing twice), `maximumOutputTokens`,
#: `allowedInputKeys`, and every other task in the document. A release that
#: rewrites a different task's prompt did not change this task's routing policy.
_ROUTING_TASK_FIELDS: Final = (
    "tier",
    "allowedProviders",
    "allowTierEscalation",
    "fallbackStrategy",
    "fallbackTemplate",
    "maximumInputTokens",
)

#: The document-level fields that decide the same thing. `pricing` is excluded
#: on the same principle: prices ride the release lifecycle but they do not
#: route, and a version that moved when a rate card was corrected would report a
#: routing-policy change that did not happen.
_ROUTING_DOCUMENT_FIELDS: Final = (
    "schemaVersion",
    "circuitBreaker",
    "retry",
    "rateLimits",
    "providerLimits",
    "modelContexts",
)


def derive_routing_policy_version(configuration: Any, task: Any) -> str:
    """The routing policy in force for one task, as a released identity.

    **Item C.** `StructuredStageInvoker` used to take this as a required free
    string, and nothing in `src/` produced one -- so the only way to construct
    the analyser was to type a literal at the wiring site, which is exactly the
    hardcoding the reviewer greps for. sect. 5 asks each stage to pin a
    `routing_policy_version` *before* invoking; a pin that a wiring site invents
    records nothing.

    Derived the way every other versioned identity here is derived -- sha256
    over a canonical JSON projection, the shape `load_return_configuration`'s
    `sha256` and `build_loaded_ai_gateway_configuration` both use -- so changing
    routing policy is a **released** change and the analysis record's pin moves
    with it. Rendered `"<schemaVersion>:<digest>"`: the schema version in front
    because it is the one part a person reading an audit row can act on, the
    digest because it is the part that cannot be wrong.

    Not put in `support_ingress` configuration. A hand-written version string in
    config is a value someone must remember to bump, and forgetting is silent --
    every record then pins a policy version that no longer describes the policy.
    A digest cannot be forgotten.

    The projection is enumerated, not "everything": see `_ROUTING_TASK_FIELDS`
    and `_ROUTING_DOCUMENT_FIELDS` for what is in it and what is deliberately
    out. Digesting the whole document would make this bump on a prompt fix or a
    price correction, and a version that moves for reasons unrelated to routing
    is a version nobody can reason from.
    """
    task_dump = task.model_dump(mode="json")
    document_dump = configuration.model_dump(mode="json")
    projection = {
        "document": {field: document_dump[field] for field in _ROUTING_DOCUMENT_FIELDS},
        "task": {field: task_dump[field] for field in _ROUTING_TASK_FIELDS},
    }
    encoded = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{document_dump['schemaVersion']}:{hashlib.sha256(encoded).hexdigest()}"


class StructuredStageInvoker:
    """`StageInvokerPort` over `StructuredOutputInvoker`.

    `release_id` and `ordered_candidate_routes` are read from the *currently
    released* task on every property access rather than captured at
    construction. That is the same rule `StructuredOutputInvoker.task` follows
    and for the same reason: a value captured at process start is a release pin
    that the Control Centre cannot move, and here it would additionally mean the
    analysis record recorded a release the invocation did not use.
    """

    def __init__(self, invoker: _InvokerLike) -> None:
        self._invoker = invoker

    @property
    def release_id(self) -> str:
        return str(self._invoker.task.promptVersion)

    @property
    def routing_policy_version(self) -> str:
        """Derived from the released document, on every access.

        A property for the same reason `release_id` is one, and it used to be a
        constructor argument -- a free string with no producer anywhere in
        `src/`. A literal typed at the wiring site would have made every
        analysis record pin a policy version that changed when somebody
        remembered to change it, which is not a version.
        """
        return derive_routing_policy_version(
            self._invoker.dispatcher.configuration, self._invoker.task
        )

    @property
    def ordered_candidate_routes(self) -> tuple[str, ...]:
        """The eligible providers, in the order the release declares them.

        Declaration order, not sorted: the order in the released task *is* the
        preference the operator expressed, and sorting it would replace their
        ranking with an alphabet.
        """
        return tuple(str(provider) for provider in self._invoker.task.allowedProviders)

    async def invoke(self, *, route_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """One staged call, translated into the record's vocabulary.

        `StructuredInvocationUnavailable` becomes `RouteUnavailableError` so the
        record loop advances to the next pinned candidate. Anything else
        propagates: an unusable answer is not an unreachable provider, and
        swallowing one as the other would burn the whole candidate list on a
        single malformed reply.
        """
        probe = str(payload.get("bodyText", ""))
        try:
            invocation = await self._invoker.invoke(
                payload=dict(payload),
                size_probe=probe,
                log_context={"routeCandidate": route_id},
            )
        except StructuredInvocationUnavailable as error:
            raise RouteUnavailableError(str(error)) from error
        envelope: SupportAnalysisEnvelope = invocation.value
        result = envelope.parsed_explanation()
        # The provider that actually answered, recorded beside the pinned
        # candidate rather than in place of it -- the pin says who was eligible,
        # this says who replied, and the audit trail needs both.
        result["provider"] = getattr(invocation, "provider", None)
        result["confidenceMillionths"] = envelope.confidenceMillionths
        return result


class SupportMessageClassifyDispatcher:
    """The `TopicDispatcher` for `return-case.support-message.classify`.

    Thin on purpose. Its whole job is to turn a stored command into the
    analyser's arguments and the analyser's failures into the outbox's two
    dispositions -- and getting *that* wrong is how a blocked analysis becomes
    an infinite retry nobody is watching.
    """

    def __init__(self, *, analyser: SupportMessageAnalyser, ingress: Any) -> None:
        self._analyser = analyser
        self._ingress = ingress

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        support_event_id = str(command.payload.get("supportEventId", ""))
        stored = await self._ingress.get_inbound(support_event_id=support_event_id)
        if stored is None:
            # The command names an event that is not on file. Retrying cannot
            # make one exist, so this is a dead letter rather than an outage.
            raise PermanentDeliveryFailure(
                f"no inbound support message for event {support_event_id!r}",
                error_code=UNKNOWN_SUPPORT_EVENT,
            )
        try:
            outcome = await self._analyser.analyse(
                case_id=str(command.payload.get("caseId", stored.get("caseId", ""))),
                work_item_id=str(stored.get("workItemId") or ""),
                support_event_id=support_event_id,
                workflow_id=str(command.payload.get("workflowId", "")),
                body_text=str(stored.get("rawBody") or ""),
                correlation_id=str(stored.get("correlationId") or "") or None,
            )
        except CandidateRoutesExhaustedError as error:
            # The record is already BLOCKED and already on the operations
            # surface; `block_exhausted` made the durable half before raising.
            # The dead letter is only how the command stops.
            raise PermanentDeliveryFailure(str(error), error_code=ANALYSIS_BLOCKED) from error
        except TransientDeliveryFailure:
            raise
        except (ConnectionError, TimeoutError) as error:
            # An infrastructure blip is a retry, not a dead letter. Named
            # exception types rather than a bare `except`: a bug in the analyser
            # must not be retried forever as though it were an outage.
            raise TransientDeliveryFailure(str(error)) from error

        logger.info(
            "support_message_analysed",
            extra={
                "caseId": command.aggregate_id,
                "supportEventId": support_event_id,
                "intent": outcome.intent,
                "reusedClassification": outcome.reused_classification,
                "reusedExtraction": outcome.reused_extraction,
                "recordGroups": len(outcome.record_group_references),
                "clarifications": len(outcome.clarifications),
            },
        )
        return DispatchResult(external_reference=support_event_id, response_digest=None)
