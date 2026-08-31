"""One call that turns runtime pieces into a ready support-question resolver.

The same shape, and for the same reason, as `composition.py`: an integration
agent asked to wire the resolver's ports reported that there was nothing to wire
them into and that four of them could not be built without deciding something
the slice owns. A dozen decisions taken at a wiring site by someone who did not
write the code is how a `routing_policy_version` becomes a literal and an `omc`
port stays `None`; both had already happened once.

So the decisions live here, and the wiring site gets one call ->
`(topic, dispatcher)`.

## What the caller still has to decide, and why neither is defaulted

**`interception`** -- for the reason AI-01 exists, restated in `composition.py`:
the defect was a mechanism two of three callers never opted into, and a
defaulted parameter is what made not opting in the silent option.

**`checkpointer`** -- because a `None` checkpointer produces a ladder that runs
perfectly and remembers nothing. Every retry would start the descent again, at
full model cost, with the budget reset: acceptance 23's guarantee gone, and
gone *silently*, because the run still completes and still answers. The wiring
site passes
`SystemStoreCheckpointSaver(*await bootstrap_system_store(settings, mongo))`.

**It is not bootstrapped here**, on `composition.py`'s own rule: it creates
Mongo structures and runs migrations, and a factory that quietly did that would
make every construction a schema change.

## What is decided here

The route pool, the attempt recorder, the resolve invoker over the released
`support.question.resolve.v1`, the fact-log adapter, the context policy, the
review store, the support thread operations, the scoped-fact writer, and the
rung inventory.

## The rung inventory this build can actually serve: rung one

`LadderDependencies` is constructed with **no `graph_sync`, no `graph_read`, no
`trusted_facts`, no `tools` and no `principal_id`**, so `build_resolution_ladder`
compiles a graph containing only the fact rung, `finalize` and `escalate`. That
is not a placeholder and it is not a degradation to be fixed by a later
`None`-check; it is the honest inventory of this build, and step:11 made it a
property a test can assert rather than a sentence in a docstring.

Each omission, and precisely why it is one rather than an adapter:

* **`GraphSyncPort`.** `OnDemandSyncCoordinator.synchronize` needs an
  `ActiveSchema`, a `graph_generation_id`, a `request_digest` and a
  `LogicalTargetedReadPlan`. `synchronize_for_case(case_id)` supplies none of
  them, so an adapter would have to author *what the resolver reads from the
  graph* and a digest scheme for it. `GraphReturnRecordSync` is the closest
  existing shape and is record-anchored, not case-anchored; bending it here
  would be inventing a read policy in a factory.
* **`GraphReadPort`.** There is no question-independent, case-scoped graph read
  in `src/`. Every graph read that exists is either schema introspection or a
  *question-derived* plan -- and a read parameterised by support prose is
  exactly what this port's own docstring forbids. Choosing what a case-scoped
  view contains is authoring a read contract.
* **`TrustedEntityPort`.** `trusted_entities_from` documents its input as a
  projection keyed by **entity name**, and nothing here maps fact names onto
  entity names. That mapping decides what a released tool binding may be filled
  from: a security decision, behind the boundary sect. 9 puts it behind.
* **`ToolExecutor.contracts` + `AuthorizationPort`.** A contract-name ->
  class allowlist with no production `AuthorizationPort` behind it. And
  `tool_bindings` is `[]` in `production.yaml` by deliberate closed default, so
  any allowlist populated here would be a guess at what a deployment that has
  bound nothing wanted.
* **`principal_id`.** Documented as *the platform's service principal, never the
  support sender's*. Inventing a credential or identity path is an automatic
  stop.

**The tool rung is therefore genuinely unreachable, and visibly so**: `[]` in the
released config, absent from `compiled_rungs`, absent from the compiled graph.
Not a stub that raises, and not a port that returns nothing convincingly.

Note the one thing that *is* read from the case and is **not** the above:
`gate_reply` needs a `tenant_id` and a `principal_id` for
`ensure_case_support_thread`. Those are the **case's own** tenancy and owning
principal, read from the case document exactly as `return_case_activities.py`
reads them when it issues a return. A different thing from the tool principal,
and not invented.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from langgraph.checkpoint.base import BaseCheckpointSaver
from pymongo import AsyncMongoClient

from return_platform.ai.gateway.final_dispatch import InterceptionPolicy
from return_platform.ai.gateway.structured_invocation import StructuredOutputInvoker
from return_platform.ai.gateway.telemetry import AIAttemptRecorder, RepositoryAIAttemptRecorder
from return_platform.ai.routing.routes import build_routes
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import LoadedAIGatewayConfiguration
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.settings import Settings
from return_platform.operations.case_commands import DurableCaseCommandStore
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.analysis_records import (
    SupportAnalysisRecordStore,
)
from return_platform.operations.return_support.analysis_wiring import SupportAnalysisEnvelope
from return_platform.operations.return_support.composition import (
    build_support_message_classify_dispatcher,
)
from return_platform.operations.return_support.ingress_store import (
    DurableSupportIngressStore,
)
from return_platform.operations.return_support.resolution_ladder import (
    LadderDependencies,
    compiled_rungs,
)
from return_platform.operations.return_support.resolution_trigger import (
    ResolvingSupportMessageClassifyDispatcher,
    build_support_question_resolver,
)
from return_platform.operations.return_support.service import ReturnSupportService
from return_platform.operations.review_aggregate import ReviewAggregateStore
from return_platform.workflows.return_case_activities import ReturnCaseActivities

logger = logging.getLogger("return_platform.support_resolver_composition")

__all__ = [
    "RESOLVE_TASK_ID",
    "RepositoryCaseFacts",
    "StructuredResolutionInvoker",
    "TriggerIntentNotInTaxonomyError",
    "build_resolving_classify_dispatcher",
    "build_support_resolution_ladder",
]

#: The released task the resolve rungs invoke (contracts.md sect. 10).
RESOLVE_TASK_ID: Final = "support.question.resolve.v1"


class TriggerIntentNotInTaxonomyError(ValueError):
    """A released trigger intent that the classifier can never produce.

    `coerce_intent` forces every classification into `support_ingress.intents`
    or into `other`, so a trigger intent outside that set matches nothing, ever.
    The resolver would then be configured, wired, deployed and silently inert --
    the exact green-but-blind shape this run keeps finding. Refused at process
    start, where somebody is watching, rather than at the first support question,
    where nobody is.
    """

    def __init__(self, unknown: tuple[str, ...], taxonomy: frozenset[str]) -> None:
        super().__init__(
            f"support_resolver.trigger_intents names {sorted(unknown)}, which "
            f"support_ingress.intents does not contain ({sorted(taxonomy)}); a trigger "
            "intent the classifier cannot produce makes the resolver silently inert"
        )
        self.unknown = unknown


class StructuredResolutionInvoker:
    """`ResolutionInvokerPort` over `StructuredOutputInvoker`.

    `release_id` and `prompt_version` are properties that re-read the *currently
    released* task on every access, not values captured at construction --
    `StructuredStageInvoker` states the reason and it applies unchanged here: a
    pin taken at process start is a release the Control Centre cannot move, and
    here it would additionally mean a checkpoint recorded a release the
    invocation did not use.

    The response is the gateway's standard `{decision, explanation,
    confidenceMillionths}` envelope, reused rather than reinvented: it is what
    every other task in `ai_gateway.yaml` returns, and a second envelope for one
    task would be a second thing for the dispatch boundary to know about. The
    `explanation` carries the resolution fields as compact JSON, and
    `parse_resolution_attempt` -- which is total and never raises -- is what
    turns them into a `ResolutionAttempt`.
    """

    def __init__(self, invoker: Any) -> None:
        self._invoker = invoker

    @property
    def release_id(self) -> str:
        return str(self._invoker.task.promptVersion)

    @property
    def prompt_version(self) -> str:
        return str(self._invoker.task.promptVersion)

    async def invoke(self, *, payload: Any) -> dict[str, Any]:
        invocation = await self._invoker.invoke(
            payload=dict(payload),
            # The question, and only the question. `size_probe` decides whether
            # a call fits a route's context window; probing the assembled
            # context would be more accurate and would also mean the probe grew
            # every time a case gained a fact.
            size_probe=str(payload.get("question", "")),
            log_context={"rung": payload.get("rung")},
        )
        envelope: SupportAnalysisEnvelope = invocation.value
        result = envelope.parsed_explanation()
        # The envelope's confidence is authoritative. Written last so an
        # `explanation` that also carried a `confidenceMillionths` cannot
        # override the measured one with a claimed one.
        result["confidenceMillionths"] = envelope.confidenceMillionths
        return result


class RepositoryCaseFacts:
    """`CaseFactsPort` over the operational repository.

    One method, because this port now has one: the whole fact log, which
    `assemble_case_context` collapses, orders and budgets itself. `list_case_facts`
    is already documented as "the whole log, oldest first" -- the projection is
    deliberately *not* done here, so the ordering rule and the projection rule
    stay in one place.

    It does **not** implement `TrustedEntityPort`, and could not: see this
    module's docstring.
    """

    def __init__(self, repository: OperationalRepository) -> None:
        self._repository = repository

    async def fact_log(self, case_id: str) -> list[dict[str, Any]]:
        return await self._repository.list_case_facts(case_id)


def _resolve_invoker(
    *,
    settings: Settings,
    ai_gateway: LoadedAIGatewayConfiguration,
    route_pool: AIRoutePool,
    recorder: AIAttemptRecorder | None,
    interception: InterceptionPolicy,
) -> StructuredResolutionInvoker:
    return StructuredResolutionInvoker(
        StructuredOutputInvoker(
            settings=settings,
            configuration=ai_gateway.configuration,
            route_pool=route_pool,
            task_id=RESOLVE_TASK_ID,
            response_model=SupportAnalysisEnvelope,
            logger=logger,
            event_prefix=RESOLVE_TASK_ID.replace(".", "_"),
            subject=f"Support question resolution ({RESOLVE_TASK_ID})",
            recorder=recorder,
            interception=interception,
        )
    )


def build_support_resolution_ladder(
    *,
    settings: Settings,
    mongo: AsyncMongoClient[dict[str, object]],
    return_configuration: ReturnPlatformConfiguration,
    ai_gateway: LoadedAIGatewayConfiguration,
    interception: InterceptionPolicy,
    checkpointer: BaseCheckpointSaver[str],
    route_pool: AIRoutePool | None = None,
    recorder: AIAttemptRecorder | None = None,
    source_mongo: AsyncMongoClient[dict[str, object]] | None = None,
) -> Any:
    """A ready `SupportQuestionResolver` from the pieces a process already has.

    Validates the released trigger intents against the released taxonomy first,
    because the two blocks are edited independently and a mismatch is invisible
    at runtime -- see `TriggerIntentNotInTaxonomyError`.
    """
    resolver_configuration = return_configuration.support_resolver
    ingress_configuration = return_configuration.support_ingress
    taxonomy = ingress_configuration.normalized_intents()
    unknown = tuple(
        intent for intent in resolver_configuration.trigger_intents if intent not in taxonomy
    )
    if unknown:
        raise TriggerIntentNotInTaxonomyError(unknown, taxonomy)

    repository = OperationalRepository(mongo, settings, source_mongo)
    pool = route_pool or AIRoutePool(build_routes(settings), ai_gateway.configuration)
    attempt_recorder = recorder if recorder is not None else _default_recorder(repository, settings)

    support_service = ReturnSupportService(
        client=mongo,
        settings=settings,
        configuration=return_configuration,
        operational_repository=repository,
    )
    # Constructed for one bound method -- `append_scoped_fact_once`, S1's scoped
    # identity derivation -- for the reason `composition.py` gives: the replay
    # guarantee sect. 4 attaches to it holds only while there is exactly one
    # implementation of `{fact_id}::{record_scope}`.
    activities = ReturnCaseActivities(repository=repository, support_service=support_service)

    dependencies = LadderDependencies(
        configuration=resolver_configuration,
        context_policy=return_configuration.context_assembly,
        facts=RepositoryCaseFacts(repository),
        resolver=_resolve_invoker(
            settings=settings,
            ai_gateway=ai_gateway,
            route_pool=pool,
            recorder=attempt_recorder,
            interception=interception,
        ),
        append_scoped_fact_once=activities.append_scoped_fact_once,
        intent_taxonomy=taxonomy,
        # Deliberately absent: graph_sync, graph_read, trusted_facts, tools and
        # principal_id. See the module docstring -- each is a decision this
        # factory is not entitled to make, and an absent rung is honest where a
        # stub or an empty return would not be.
    )
    logger.info(
        "support_resolver_rungs",
        extra={"rungs": list(compiled_rungs(dependencies))},
    )
    return build_support_question_resolver(
        dependencies=dependencies,
        ingress_configuration=ingress_configuration,
        cases=repository,
        reviews=ReviewAggregateStore(
            mongo, settings, command_store=DurableCaseCommandStore(mongo, settings)
        ),
        threads=support_service,
        append_scoped_fact_once=activities.append_scoped_fact_once,
        checkpointer=checkpointer,
    )


def _default_recorder(
    repository: OperationalRepository, settings: Settings
) -> AIAttemptRecorder | None:
    """Where every other dispatch path's attempts already go.

    `trace_sink` gated on `settings.ai_trace_payloads`, the `runtime_factory`
    precedent verbatim, so resolution calls land in the same `ai_usage_attempts`
    collection every cost query already reads.
    """
    return RepositoryAIAttemptRecorder(
        repository,
        trace_sink=repository if settings.ai_trace_payloads else None,
    )


def build_resolving_classify_dispatcher(
    *,
    settings: Settings,
    mongo: AsyncMongoClient[dict[str, object]],
    return_configuration: ReturnPlatformConfiguration,
    ai_gateway: LoadedAIGatewayConfiguration,
    interception: InterceptionPolicy,
    checkpointer: BaseCheckpointSaver[str],
    route_pool: AIRoutePool | None = None,
    recorder: AIAttemptRecorder | None = None,
    source_mongo: AsyncMongoClient[dict[str, object]] | None = None,
) -> tuple[str, ResolvingSupportMessageClassifyDispatcher]:
    """`(topic, dispatcher)` for the classify topic, with resolution attached.

    A **drop-in replacement** for `build_support_message_classify_dispatcher` at
    the wiring site: same topic, same signature plus `checkpointer`, and V2's
    dispatcher is built by V2's own factory and delegated to rather than
    reimplemented. Returning the topic beside the dispatcher for the reason V2's
    factory does: a table keyed by the wrong constant is a queue nothing ever
    drains, and it fails silently.
    """
    topic, analysis = build_support_message_classify_dispatcher(
        settings=settings,
        mongo=mongo,
        return_configuration=return_configuration,
        ai_gateway=ai_gateway,
        interception=interception,
        route_pool=route_pool,
        recorder=recorder,
        source_mongo=source_mongo,
    )
    resolver = build_support_resolution_ladder(
        settings=settings,
        mongo=mongo,
        return_configuration=return_configuration,
        ai_gateway=ai_gateway,
        interception=interception,
        checkpointer=checkpointer,
        route_pool=route_pool,
        recorder=recorder,
        source_mongo=source_mongo,
    )
    return topic, ResolvingSupportMessageClassifyDispatcher(
        analysis=analysis,
        resolver=resolver,
        records=SupportAnalysisRecordStore(mongo, settings),
        inbound=DurableSupportIngressStore(mongo, settings, return_configuration.support_ingress),
        ingress_configuration=return_configuration.support_ingress,
        trigger_intents=return_configuration.support_resolver.trigger_intents,
    )
