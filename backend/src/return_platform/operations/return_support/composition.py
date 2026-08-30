"""One call that turns runtime pieces into a ready support-message analyser.

The integration agent reported that wiring `SupportMessageAnalyser` at
`workers/integration_outbox.py` meant building an `AIRoutePool`, an interception
policy and an attempt recorder from scratch, because that worker has none of
them -- only `runtime.ai_gateway_configuration`. A dozen decisions made at a
wiring site by someone who did not write the slice is how a `routing_policy_version`
becomes a literal and an `omc` port stays `None`; both of those had already
happened.

So the decisions live here, in the slice that owns them, and the wiring site
gets `build_support_message_classify_dispatcher(...)` -> `(topic, dispatcher)`.

**What the caller still has to decide, and why it is not defaulted.**
`interception` has no default, for the reason AI-01 exists: the defect was not a
missing mechanism, it was a mechanism two of three callers never opted into, and
a defaulted parameter is what made not opting in the silent option. A process
with no interception store passes `ALLOW_ALL` and has said so out loud.

**What is decided here.** The route pool (built from the same `build_routes` the
API process uses, over the released gateway document), the attempt recorder
(`RepositoryAIAttemptRecorder` over the operational repository, with the trace
sink gated on `settings.ai_trace_payloads` -- the `runtime_factory` precedent
verbatim, so support analysis lands in the same `ai_usage_attempts` collection
every cost query already reads), both stage invokers, the analysis record store,
the omc mirror, the relay, and the ingress store the dispatcher reads from.

**Indexes are not created here.** `ensure_support_ingress_indexes` and the
analysis-record indexes are startup duties on the process that owns startup;
a factory that quietly created indexes would make every construction a schema
change.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from pymongo import AsyncMongoClient

from return_platform.ai.gateway.final_dispatch import InterceptionPolicy
from return_platform.ai.gateway.structured_invocation import StructuredOutputInvoker
from return_platform.ai.gateway.telemetry import AIAttemptRecorder, RepositoryAIAttemptRecorder
from return_platform.ai.routing.routes import build_routes
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import LoadedAIGatewayConfiguration
from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.mongo_store import (
    MongoAtomicConversationStore,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    ConversationScope,
)
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.analysis_records import (
    SupportAnalysisRecordStore,
)
from return_platform.operations.return_support.analysis_wiring import (
    StructuredStageInvoker,
    SupportAnalysisEnvelope,
    SupportMessageClassifyDispatcher,
)
from return_platform.operations.return_support.ingress_store import (
    SUPPORT_MESSAGE_CLASSIFY_TOPIC,
    DurableSupportIngressStore,
)
from return_platform.operations.return_support.message_classification import (
    AGENT_ID,
    OMC_RETURN_UPDATE_TOPIC,
    SupportMessageAnalyser,
)
from return_platform.operations.return_support.omc_mirror import DurableOmcMirror
from return_platform.operations.return_support.relay import SupportTranscriptRelay
from return_platform.operations.return_support.service import ReturnSupportService
from return_platform.operations.support_events import DurableSupportEventStore
from return_platform.workflows.return_case_activities import ReturnCaseActivities

logger = logging.getLogger("return_platform.support_composition")

#: The two released tasks the analysis stages run against (contracts.md sect. 10).
CLASSIFY_TASK_ID: Final = "support.message.classify.v1"
EXTRACT_TASK_ID: Final = "support.message.extract.v1"


def _stage_invoker(
    *,
    settings: Settings,
    ai_gateway: LoadedAIGatewayConfiguration,
    route_pool: AIRoutePool,
    task_id: str,
    recorder: AIAttemptRecorder | None,
    interception: InterceptionPolicy,
) -> StructuredStageInvoker:
    """One stage, over the shipped dispatch boundary.

    `event_prefix` and `subject` are per task so two stages of one analysis are
    distinguishable in telemetry -- "the support analysis failed" is not an
    operational fact anyone can act on if it could mean either stage.
    """
    return StructuredStageInvoker(
        StructuredOutputInvoker(
            settings=settings,
            configuration=ai_gateway.configuration,
            route_pool=route_pool,
            task_id=task_id,
            response_model=SupportAnalysisEnvelope,
            logger=logger,
            event_prefix=task_id.replace(".", "_"),
            subject=f"Support message analysis ({task_id})",
            recorder=recorder,
            interception=interception,
        )
    )


def build_support_message_analyser(
    *,
    settings: Settings,
    mongo: AsyncMongoClient[dict[str, object]],
    return_configuration: ReturnPlatformConfiguration,
    ai_gateway: LoadedAIGatewayConfiguration,
    interception: InterceptionPolicy,
    route_pool: AIRoutePool | None = None,
    recorder: AIAttemptRecorder | None = None,
    source_mongo: AsyncMongoClient[dict[str, object]] | None = None,
) -> SupportMessageAnalyser:
    """A ready `SupportMessageAnalyser` from the pieces a process already has.

    `route_pool` is optional and worth passing when the process already owns
    one: circuit state learned by one caller must be visible to the next, and a
    second pool in one process rediscovers a dead credential once per pool. When
    it is not passed, one is built here from the released document.

    `recorder` is likewise optional and defaults to the operational repository,
    which is where every other dispatch path's attempts already go.
    """
    repository = OperationalRepository(mongo, settings, source_mongo)
    pool = route_pool or AIRoutePool(build_routes(settings), ai_gateway.configuration)
    attempt_recorder = recorder if recorder is not None else _default_recorder(repository, settings)

    support_service = ReturnSupportService(
        client=mongo,
        settings=settings,
        configuration=return_configuration,
        operational_repository=repository,
    )
    # Constructed for one bound method: `append_scoped_fact_once`, S1's scoped
    # identity derivation. Re-implementing that here over
    # `repository.append_scoped_case_fact` would be a second copy of the
    # `{fact_id}::{record_scope}` rule and of `SCOPED_FACT_IDENTITY_VERSION`,
    # and the replay guarantee sect. 4 attaches to it only holds while there is
    # exactly one.
    activities = ReturnCaseActivities(repository=repository, support_service=support_service)

    return SupportMessageAnalyser(
        records=SupportAnalysisRecordStore(mongo, settings),
        classifier=_stage_invoker(
            settings=settings,
            ai_gateway=ai_gateway,
            route_pool=pool,
            task_id=CLASSIFY_TASK_ID,
            recorder=attempt_recorder,
            interception=interception,
        ),
        extractor=_stage_invoker(
            settings=settings,
            ai_gateway=ai_gateway,
            route_pool=pool,
            task_id=EXTRACT_TASK_ID,
            recorder=attempt_recorder,
            interception=interception,
        ),
        configuration=return_configuration.support_ingress,
        record_store=repository,
        append_scoped_fact_once=activities.append_scoped_fact_once,
        support_events=DurableSupportEventStore(mongo, settings),
        omc=DurableOmcMirror(
            repository,
            topic=OMC_RETURN_UPDATE_TOPIC,
            actor_id=AGENT_ID,
        ),
        relay=SupportTranscriptRelay(
            store=MongoAtomicConversationStore(mongo, settings.mongo_database),
            cases=repository,
            scope_factory=ConversationScope,
        ),
    )


def _default_recorder(
    repository: OperationalRepository, settings: Settings
) -> AIAttemptRecorder | None:
    """The operational repository, where every other dispatch path's attempts go.

    `trace_sink` gated on `settings.ai_trace_payloads`, which is the
    `runtime_factory` precedent verbatim: with the sink set, the prompt and
    response of every support-analysis call land in `ai_traces` and the Control
    Centre can show what was actually said; with it unset, only the attempt row
    is written and no payload is stored.
    """
    return RepositoryAIAttemptRecorder(
        repository,
        trace_sink=repository if settings.ai_trace_payloads else None,
    )


def build_support_message_classify_dispatcher(
    *,
    settings: Settings,
    mongo: AsyncMongoClient[dict[str, object]],
    return_configuration: ReturnPlatformConfiguration,
    ai_gateway: LoadedAIGatewayConfiguration,
    interception: InterceptionPolicy,
    route_pool: AIRoutePool | None = None,
    recorder: AIAttemptRecorder | None = None,
    source_mongo: AsyncMongoClient[dict[str, object]] | None = None,
) -> tuple[str, SupportMessageClassifyDispatcher]:
    """`(topic, dispatcher)`, ready to put in the worker's dispatch table.

    Returns the topic beside the dispatcher rather than leaving the caller to
    import it: a registration keyed by the wrong constant is a queue nothing
    ever drains, and it fails silently because an unregistered topic simply
    never dispatches.
    """
    analyser = build_support_message_analyser(
        settings=settings,
        mongo=mongo,
        return_configuration=return_configuration,
        ai_gateway=ai_gateway,
        interception=interception,
        route_pool=route_pool,
        recorder=recorder,
        source_mongo=source_mongo,
    )
    ingress: Any = DurableSupportIngressStore(
        mongo, settings, return_configuration.support_ingress
    )
    return SUPPORT_MESSAGE_CLASSIFY_TOPIC, SupportMessageClassifyDispatcher(
        analyser=analyser, ingress=ingress
    )


__all__ = [
    "CLASSIFY_TASK_ID",
    "EXTRACT_TASK_ID",
    "build_support_message_analyser",
    "build_support_message_classify_dispatcher",
]
