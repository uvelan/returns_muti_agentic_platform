"""Binds the analyzer's `SchemaReasoningPort` to the shared AI route pool.

Lives here, not in `graph_schema_analyzer/`, because it is the only place
permitted to see both the analyzer's port and the gateway's concrete world
(design doc section 2.7 -- the analyzer owns no `adapters/` package).

It adds no execution machinery of its own. Routing, failover, rate limits,
circuit breakers, tier escalation, safety inspection and attempt logging all
come from `ai/gateway/structured_invocation.py`, the same path the Order Agent
runs on, so
there is still exactly one AI execution path and this adapter is only its
analyzer-facing projection.

**The prompt arrives pre-framed.** `application/prompt_context.py` has already
built the six ordered blocks and neutralised anything in them that could
impersonate a block delimiter. This adapter passes those blocks straight
through. Re-framing them here would mean a second, divergent copy of the
untrusted-input rules, and the block that matters -- 5, the source sample -- is
exactly the one a re-framing bug would mislabel as trusted.

**The model is never asked for the snapshot hash.** `SchemaProposal` is grounded
in a named snapshot, but the model has no independent knowledge of that name: it
could only echo back what we told it, so asking is a question whose answer we
already hold and cannot verify any better than by comparing it to our own copy.
The response model deliberately omits the field and the adapter stamps the
authoritative value, which makes a wrong hash structurally impossible rather
than merely unlikely.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.ai.gateway.interception_policy import (
    AIGatewaySettingsSource,
    build_interception_policy,
)
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.interception.store import InterceptionStore
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import AIGatewayConfiguration
from return_platform.configuration.settings import Settings
from return_platform.graph_schema_analyzer.ports.ai_port import (
    ProposedNode,
    ProposedRelationship,
    SchemaProposal,
    SchemaReasoningPort,
)

__all__ = [
    "GatewaySchemaReasoningAdapter",
    "SchemaProposalUnavailable",
    "build_analyzer_ai_adapter",
]

logger = logging.getLogger("return_platform.bootstrap.analyzer_ai_adapter")

GRAPH_SCHEMA_PROPOSAL_TASK_ID = "GRAPH_SCHEMA_PROPOSAL_V1"


class SchemaProposalUnavailable(StructuredInvocationUnavailable):
    """Raised when every route failed to produce a usable schema proposal."""


# What the model is actually asked for: `SchemaProposal` minus its grounding.
# Keeping this separate is what lets the adapter supply the snapshot hash itself,
# and it keeps the schema shown to the model down to the fields it can genuinely
# reason about.
#
# Deliberately documented in a comment rather than a docstring: pydantic copies a
# class docstring into the JSON schema's `description`, and this schema is sent
# to the model verbatim. Explaining here which field we withhold, and why, would
# put that field's name in front of the model and invite it to emit one --
# which `extra="forbid"` would then reject, burning a route on a retry.
class _SchemaProposalDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[ProposedNode, ...]
    relationships: tuple[ProposedRelationship, ...]
    open_questions: tuple[str, ...] = ()


class GatewaySchemaReasoningAdapter:
    """Structurally satisfies `SchemaReasoningPort`."""

    def __init__(
        self,
        *,
        settings: Settings,
        configuration: AIGatewayConfiguration,
        route_pool: AIRoutePool,
        task_id: str = GRAPH_SCHEMA_PROPOSAL_TASK_ID,
        interception_store: InterceptionStore | None = None,
        gateway_settings: AIGatewaySettingsSource | None = None,
    ) -> None:
        self._invoker: StructuredOutputInvoker[_SchemaProposalDraft] = StructuredOutputInvoker(
            settings=settings,
            configuration=configuration,
            route_pool=route_pool,
            task_id=task_id,
            response_model=_SchemaProposalDraft,
            logger=logger,
            event_prefix="analyzer_schema_proposal",
            subject="Graph schema proposal",
            unavailable_error=SchemaProposalUnavailable,
            # AI-01. Block 5 of the analyzer's prompt is UNTRUSTED SOURCE SAMPLE
            # -- rows read out of a customer's database -- so of the two paths
            # that bypassed interception this is the one carrying the most
            # sensitive payload, and the one an operator is most likely to want
            # to look at before it leaves.
            interception=build_interception_policy(
                store=interception_store,
                settings_source=gateway_settings,
                subject="analyzer_schema_proposal",
            ),
        )

    async def propose_schema(
        self,
        *,
        analysis_id: str,
        snapshot_content_hash: str,
        prompt_blocks: Sequence[Mapping[str, Any]],
    ) -> SchemaProposal:
        blocks_json = json.dumps(
            [dict(block) for block in prompt_blocks],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        invocation = await self._invoker.invoke(
            payload={
                "analysisId": analysis_id,
                # Sent for interception/replay correlation only -- the response
                # model does not carry it back, so it cannot be echoed wrong.
                "snapshotContentHash": snapshot_content_hash,
                "promptBlocksJson": blocks_json,
            },
            size_probe=blocks_json,
            log_context={
                "analysis_id": analysis_id,
                "snapshot_content_hash": snapshot_content_hash,
            },
        )
        draft = invocation.value
        logger.info(
            "analyzer_schema_proposal_produced",
            extra={
                "analysis_id": analysis_id,
                "snapshot_content_hash": snapshot_content_hash,
                "provider": invocation.provider,
                "model": invocation.model,
                "node_count": len(draft.nodes),
                "relationship_count": len(draft.relationships),
                "open_question_count": len(draft.open_questions),
            },
        )
        return SchemaProposal(
            snapshot_content_hash=snapshot_content_hash,
            nodes=draft.nodes,
            relationships=draft.relationships,
            open_questions=draft.open_questions,
        )


def build_analyzer_ai_adapter(
    *,
    settings: Settings,
    configuration: AIGatewayConfiguration,
    route_pool: AIRoutePool,
    task_id: str = GRAPH_SCHEMA_PROPOSAL_TASK_ID,
    interception_store: InterceptionStore | None = None,
    gateway_settings: AIGatewaySettingsSource | None = None,
) -> SchemaReasoningPort:
    """Typed factory -- the return annotation is what makes mypy prove port
    conformance, rather than a runtime isinstance that only checks method names.

    This matters more than usual here: the analyzer's DI guard is a runtime
    `isinstance` against a `@runtime_checkable` Protocol, which only checks that
    the method *names* exist. An adapter with a wrong signature would satisfy it
    and fail at call time; a missing method would make routes 503 instead of
    failing loudly at import. Static conformance is the real check.
    """
    return GatewaySchemaReasoningAdapter(
        settings=settings,
        configuration=configuration,
        route_pool=route_pool,
        task_id=task_id,
        interception_store=interception_store,
        gateway_settings=gateway_settings,
    )
