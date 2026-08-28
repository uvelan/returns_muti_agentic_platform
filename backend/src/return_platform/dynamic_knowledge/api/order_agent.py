"""Versioned API boundary for the dynamic Order Discovery Agent.

Turn submission goes through a durable per-conversation Temporal workflow
(`OrderDiscoveryWorkflow`) rather than calling `DynamicOrderAgentCoordinator`
directly -- see `workflows/order_discovery_workflow.py`. This route owns
identity extraction (principal/tenant/roles/branch_ids) and the workflow
handle lifecycle; everything about *how* a turn is processed lives in the
workflow/activity/coordinator/graph stack behind it.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Annotated, Any, Final, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from return_platform.api.dependency_probes import (
    CONFIGURATION_HEALTH_ATTRIBUTE,
    resolve_known_agent_policy_ids,
)
from return_platform.configuration.return_configuration import (
    ConfigurationHealthFailure,
    LoadedReturnConfiguration,
    evaluate_configuration_health,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    AtomicConversationRepository,
    ConversationScope,
    ConversationSummary,
    ConversationTranscript,
)
from return_platform.resources import RuntimeResources
from return_platform.security.principal import Principal
from return_platform.shared.contracts import APIResponse, ResponseMeta
from return_platform.workflows.order_discovery_workflow import (
    OrderDiscoveryTurnOutcome,
    OrderDiscoveryWorkflow,
    OrderDiscoveryWorkflowInput,
    SubmitOrderDiscoveryTurnCommand,
    order_discovery_workflow_id,
)

logger = logging.getLogger("return_platform.order_agent_api")

router = APIRouter(prefix="/api/v2/order-agent", tags=["Order Agent"])

_HTTP_STATUS_BY_CODE: dict[str, int] = {
    "ORDER_AGENT_OUT_OF_SCOPE": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "CONVERSATION_VERSION_CONFLICT": status.HTTP_409_CONFLICT,
    "ORDER_AGENT_QUERY_BUDGET_EXCEEDED": status.HTTP_422_UNPROCESSABLE_CONTENT,
}

#: The one outcome code that can mean either "you named an agent that is not in
#: scope" or "this deployment's Copilot mapping does not resolve". The turn route
#: separates them below; everything else in `_HTTP_STATUS_BY_CODE` means exactly
#: one thing.
_UNRESOLVED_AGENT_CODE: Final = "ORDER_AGENT_OUT_OF_SCOPE"

#: Which configuration check answers "is the Copilot's agent mapping usable?".
#: `evaluate_configuration_health` runs the eligibility-policy check alongside
#: it, and an unpublished eligibility policy is not a reason to refuse a
#: discovery turn.
_COPILOT_BINDING_CHECK: Final = "COPILOT_AGENT_BINDING"


class DynamicOrderAgentRuntime:
    """Application-owned dependency bundle attached during startup."""

    def __init__(self, *, temporal_client: Client, task_queue: str) -> None:
        self.temporal_client = temporal_client
        self.task_queue = task_queue


def resolve_runtime(request: Request) -> DynamicOrderAgentRuntime:
    runtime = getattr(request.app.state, "dynamic_order_agent_runtime", None)
    # `isinstance`, not a class-name string. The name check made `runtime` stay
    # `Any`, so `-> DynamicOrderAgentRuntime` was unverified -- and it would
    # also accept any unrelated class that happened to share the name.
    if isinstance(runtime, DynamicOrderAgentRuntime):
        return runtime

    status_payload: Any = getattr(
        request.app.state,
        "dynamic_order_agent_status",
        {"state": "NOT_INITIALIZED"},
    )
    state = (
        str(status_payload.get("state", "NOT_INITIALIZED"))
        if isinstance(status_payload, dict)
        else "NOT_INITIALIZED"
    )
    code = {
        "DISABLED": "ORDER_AGENT_DISABLED",
        "DEPENDENCIES_UNAVAILABLE": "ORDER_AGENT_DEPENDENCIES_UNAVAILABLE",
        "INITIALIZATION_FAILED": "ORDER_AGENT_INITIALIZATION_FAILED",
    }.get(state, "ORDER_AGENT_UNAVAILABLE")
    message = {
        "DISABLED": "The Order Discovery Agent is disabled by runtime configuration.",
        "DEPENDENCIES_UNAVAILABLE": (
            "The Order Discovery Agent is waiting for required platform dependencies."
        ),
        "INITIALIZATION_FAILED": (
            "The Order Discovery Agent failed to initialize. Inspect backend startup logs."
        ),
    }.get(state, "The Order Discovery Agent runtime is not initialized.")
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": code,
            "message": message,
            "retryable": state != "DISABLED",
        },
    )


def _order_discovery_workflow_id(conversation_id: str) -> str:
    """Kept as the module's own name; the definition now lives with the workflow.

    A second caller needed it -- the item-selection write, which has to tell the
    conversation its pending question was answered elsewhere -- and two modules
    spelling an id by hand is how one of them ends up signalling nothing.
    """
    return order_discovery_workflow_id(conversation_id)


def _scope(request: Request) -> ConversationScope:
    """The caller's conversation scope, from the authenticated request only.

    Same two values `submit_turn` builds its `PrincipalContext` from below, so a
    conversation is read under exactly the identity that wrote it. Nothing here
    is client-supplied: a scope taken from a header or a query parameter would
    be an isolation boundary the caller chooses for themselves.
    """
    principal = cast(Principal, request.state.principal)
    return ConversationScope(
        tenant_id=str(getattr(request.state, "tenant_id", "default")),
        principal_id=principal.subject,
    )


def _conversations(request: Request) -> AtomicConversationRepository:
    """The conversation reader, or a clear 503.

    Deliberately not routed through Temporal. Reading history is a query
    against the record the worker already committed, and a history list that
    goes blank because the workflow host is down would be reporting the wrong
    outage.
    """
    repository = getattr(request.app.state, "order_agent_conversations", None)
    if isinstance(repository, AtomicConversationRepository):
        return repository
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "ORDER_AGENT_CONVERSATIONS_UNAVAILABLE",
            "message": "Conversation history is not available in this process.",
            "retryable": True,
        },
    )


@router.get("/conversations", response_model=APIResponse[list[ConversationSummary]])
async def list_conversations(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> APIResponse[list[ConversationSummary]]:
    """Recent conversations, newest first: the copilot's history list.

    Summaries only. The full turn results stay where they are -- rendering a
    list of titles must not pull every statement and evidence row of every
    conversation across the wire.
    """
    return APIResponse(
        data=await _conversations(request).list_recent(scope=_scope(request), limit=limit),
        meta=_meta(request),
    )


@router.get(
    "/conversations/{conversation_id}/transcript",
    response_model=APIResponse[ConversationTranscript],
)
async def read_conversation_transcript(
    conversation_id: str,
    request: Request,
) -> APIResponse[ConversationTranscript]:
    """What was said, so reopening a conversation shows it rather than a blank
    pane. Same bounded transcript the agent itself reasons over.

    Another principal's conversation is a 404, not a 403: the id is a
    client-generated UUID, and distinguishing "does not exist" from "exists but
    is not yours" would turn this endpoint into an existence oracle.
    """
    transcript = await _conversations(request).read_transcript(
        conversation_id, scope=_scope(request)
    )
    if transcript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "CONVERSATION_NOT_FOUND",
                "message": f"Conversation {conversation_id} does not exist.",
                "retryable": False,
            },
        )
    return APIResponse(data=transcript, meta=_meta(request))


def _meta(request: Request) -> ResponseMeta:
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=correlation_id if isinstance(correlation_id, str) else "unknown")


def _binding_failure(
    failures: Collection[ConfigurationHealthFailure],
) -> ConfigurationHealthFailure | None:
    return next((item for item in failures if item.check == _COPILOT_BINDING_CHECK), None)


async def _copilot_agent_binding_failure(request: Request) -> ConfigurationHealthFailure | None:
    """Is *this deployment's* Copilot mapping why the agent did not resolve?

    Plan sect. 5.4 asks the turn route to answer `503
    COPILOT_AGENT_CONFIGURATION_INVALID` for an unset or dangling
    `copilot.order_discovery_agent_id`, and that is a different fact from the
    one `ORDER_AGENT_OUT_OF_SCOPE` states. 422 says the caller named an agent
    that is legitimately not in scope -- a client error, and the caller's to
    fix. 503 says the deployment is misconfigured -- nobody's request is
    wrong, no retry helps, and an operator has to publish a mapping. Both used
    to leave here as 422, which is precisely what let the original P0 read as a
    client bug for as long as it did: every turn 422'd because the shipped
    mapping named a policy the active schema did not publish, and the status
    code said the associate had asked for the wrong thing.

    Evaluated on the failure path only. It resolves the active schema, which is
    a Mongo read plus a YAML parse, and a turn that succeeded has already
    proved the mapping resolves.

    `evaluate_configuration_health` rather than a fourth resolver: it is the one
    place that decides what configuration health means, and taking the message
    and the wire code from it is what keeps this route, `/api/return-history`
    and `/health/ready` reporting one fact rather than three spellings of it.
    Falls back to the last recorded evaluation when the schema cannot be
    resolved at all, so an unreachable release store degrades to the startup
    gate's answer instead of turning a 422 into a 500.
    """
    loaded = getattr(request.app.state, "return_configuration", None)
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if (
        isinstance(loaded, LoadedReturnConfiguration)
        and isinstance(resources, RuntimeResources)
        and isinstance(settings, Settings)
    ):
        try:
            known_agent_policy_ids = await resolve_known_agent_policy_ids(resources, settings)
        except Exception as exc:  # noqa: BLE001 - degrade to the recorded answer
            logger.warning(
                "copilot_agent_binding_check_unresolved",
                extra={"error_type": type(exc).__name__},
            )
        else:
            return _binding_failure(
                evaluate_configuration_health(loaded.configuration, known_agent_policy_ids)
            )
    recorded: Collection[ConfigurationHealthFailure] = getattr(
        request.app.state, CONFIGURATION_HEALTH_ATTRIBUTE, ()
    )
    return _binding_failure(recorded)


@router.post("/conversations/{conversation_id}/turns", response_model=APIResponse[AgentTurnResult])
async def process_turn(
    conversation_id: str,
    payload: AgentTurnRequest,
    request: Request,
    runtime: Annotated[DynamicOrderAgentRuntime, Depends(resolve_runtime)],
) -> APIResponse[AgentTurnResult]:
    """Run one reasoning turn.

    Returns the platform `{data, meta}` envelope like every other route. It did
    not, and was the only route that did not: it returned a bare
    `AgentTurnResult`. The browser client rejects any non-enveloped body
    outright, so a turn that reasoned correctly all the way to an
    evidence-cited answer still surfaced in the UI as "the server returned an
    invalid API response envelope" -- the failure looked like the agent, and was
    the response shape.
    """
    if payload.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "CONVERSATION_ID_MISMATCH",
                "message": "Path and payload conversation identifiers must match.",
                "retryable": False,
            },
        )

    principal = cast(Principal, request.state.principal)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    branch_ids_raw: Any = getattr(request.state, "branch_ids", ())
    branch_ids = frozenset(str(value) for value in branch_ids_raw)

    workflow_id = _order_discovery_workflow_id(conversation_id)
    try:
        handle = await runtime.temporal_client.start_workflow(
            OrderDiscoveryWorkflow.run,
            OrderDiscoveryWorkflowInput(conversation_id=conversation_id, agent_id=payload.agent_id),
            id=workflow_id,
            task_queue=runtime.task_queue,
        )
    except WorkflowAlreadyStartedError:
        handle = runtime.temporal_client.get_workflow_handle(workflow_id)

    try:
        outcome = cast(
            OrderDiscoveryTurnOutcome,
            await handle.execute_update(
                OrderDiscoveryWorkflow.submit_turn,
                SubmitOrderDiscoveryTurnCommand(
                    expected_conversation_version=payload.expected_conversation_version,
                    client_turn_id=payload.client_turn_id,
                    idempotency_key=payload.idempotency_key,
                    message_id=payload.message_id,
                    message=payload.message,
                    principal_id=principal.subject,
                    tenant_id=tenant_id,
                    roles=principal.roles,
                    branch_ids=branch_ids,
                    session_timezone=payload.session_timezone,
                    # Server-set, from the request middleware -- never read off
                    # the request body, which a client could fill with anything.
                    correlation_id=_meta(request).request_id,
                ),
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ORDER_AGENT_WORKFLOW_FAILED",
                "message": "The Order Discovery workflow could not process the request.",
                "retryable": True,
            },
        ) from exc

    if outcome.error is not None:
        if outcome.error.code == _UNRESOLVED_AGENT_CODE:
            # An agent that did not resolve is two different failures wearing one
            # code. Ask the configuration which one this is before answering.
            binding_failure = await _copilot_agent_binding_failure(request)
            if binding_failure is not None:
                logger.error(
                    "copilot_agent_configuration_invalid",
                    extra={
                        "check": binding_failure.check,
                        "code": binding_failure.code,
                        "requested_agent_id": payload.agent_id,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": binding_failure.code,
                        "message": binding_failure.message,
                        # Not retryable by the caller. `/api/return-history`
                        # says the same for the same code: repeating the request
                        # cannot help until an operator publishes a mapping.
                        "retryable": False,
                    },
                )
        http_status = _HTTP_STATUS_BY_CODE.get(
            outcome.error.code,
            status.HTTP_503_SERVICE_UNAVAILABLE
            if outcome.error.retryable
            else status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        raise HTTPException(
            status_code=http_status,
            detail={
                "code": outcome.error.code,
                "message": outcome.error.message,
                "retryable": outcome.error.retryable,
            },
        )

    if outcome.result is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ORDER_AGENT_WORKFLOW_FAILED",
                "message": "The Order Discovery workflow returned no result.",
                "retryable": True,
            },
        )

    return APIResponse(
        data=AgentTurnResult.model_validate_json(outcome.result.agent_turn_result_json),
        meta=_meta(request),
    )
