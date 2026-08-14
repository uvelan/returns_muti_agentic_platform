"""Binds the Order Agent's `CaseStore` port to the operational repository.

The agent's port is deliberately one method wide -- confirm an order, get a case
id -- and this is the only place that widens into the return domain. Keeping the
adapter here rather than in `graph_nodes` is what stops the reasoning loop
importing `OperationalRepository` and, with it, everything else that repository
can do.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

from return_platform.dynamic_knowledge.order_agent.contracts import OrderConfirmation
from return_platform.dynamic_knowledge.order_agent.graph_nodes import ConfirmedCase
from return_platform.operations.models import FactAcquisition, FactChannel

__all__ = ["RepositoryCaseStore"]


class _CaseRepository(Protocol):
    """The slice of `OperationalRepository` this adapter uses.

    Structural rather than concrete so the adapter can be exercised without
    standing up a repository that owns twenty-odd collections.
    """

    async def find_case_by_confirmation(self, confirmation_key: str) -> dict[str, Any] | None: ...

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]: ...

    async def create_case(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        branch_id: str | None = ...,
        channel_a_conversation_id: str | None = ...,
        confirmed_order_reference: str | None = ...,
        configuration_release_id: str | None = ...,
        graph_generation_id: str | None = ...,
        confirmation_key: str | None = ...,
    ) -> dict[str, Any]: ...

    async def append_case_fact(
        self,
        *,
        fact_id: str,
        case_id: str,
        fact_name: str,
        value: Any,
        agent_id: str,
        channel: FactChannel,
        acquisition_method: FactAcquisition,
        turn_id: str | None = ...,
        source_system: str | None = ...,
        source_path: str | None = ...,
        observed_at: datetime | None = ...,
        supersedes_fact_id: str | None = ...,
        correlation_id: str | None = ...,
    ) -> dict[str, Any]: ...


class RepositoryCaseStore:
    def __init__(self, repository: _CaseRepository) -> None:
        self._repository = repository

    async def case_facts(self, case_id: str) -> dict[str, Any]:
        """Name -> value for everything the case currently knows.

        Flattened from the fact log's projection: the agent needs the values to
        avoid re-asking, and handing it whole provenance records would put
        agent ids and source paths into a reasoning prompt for no benefit.
        """
        latest = await self._repository.latest_case_facts(case_id)
        return {name: fact.get("value") for name, fact in latest.items()}

    async def confirm_case(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        branch_ids: tuple[str, ...],
        conversation_id: str,
        confirmation: OrderConfirmation,
        configuration_release_id: str,
        graph_generation_id: str,
        observed_facts: tuple[dict[str, Any], ...] = (),
    ) -> ConfirmedCase:
        """Obtain the case for this confirmation, creating it at most once.

        Idempotent on `tenant | conversation | order | line-set`. A Temporal
        retry of the same turn, or two confirmations racing, resolve to one
        case; confirming a *different* order or a different set of lines is a
        different intent and gets its own.

        The check-then-create is not a race: `create_case` is itself idempotent
        on the same key through a unique index, so a loser reads the winner's
        case rather than creating a second. The read first is an optimisation
        and a clearer `already_existed`, not the correctness mechanism.
        """
        key = confirmation.idempotency_key(tenant_id=tenant_id, conversation_id=conversation_id)
        existing = await self._repository.find_case_by_confirmation(key)
        if existing is not None:
            return ConfirmedCase(case_id=str(existing["caseId"]), already_existed=True)

        case_id = str(uuid.uuid4())
        created = await self._repository.create_case(
            case_id=case_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            # One branch, or none. A principal scoped to several branches has
            # not told us which this return is for, and guessing the first
            # would put the return in the wrong place silently.
            branch_id=branch_ids[0] if len(branch_ids) == 1 else None,
            channel_a_conversation_id=conversation_id,
            confirmed_order_reference=confirmation.order_reference,
            configuration_release_id=configuration_release_id,
            graph_generation_id=graph_generation_id,
            confirmation_key=key,
        )
        already_existed = str(created["caseId"]) != case_id
        if not already_existed:
            # The confirmation itself is a fact, recorded with how it was
            # obtained: an associate said so, on Channel A. Everything later
            # reads this rather than re-deriving it from the conversation.
            await self._repository.append_case_fact(
                fact_id=str(uuid.uuid4()),
                case_id=case_id,
                fact_name="confirmed_order_reference",
                value=confirmation.order_reference,
                agent_id="order-discovery-agent",
                channel=FactChannel.CHANNEL_A,
                acquisition_method=FactAcquisition.STATED,
                turn_id=conversation_id,
                source_path="ORDER_CONFIRMATION",
            )
            if confirmation.order_line_references:
                await self._repository.append_case_fact(
                    fact_id=str(uuid.uuid4()),
                    case_id=case_id,
                    fact_name="confirmed_order_lines",
                    value=list(confirmation.order_line_references),
                    agent_id="order-discovery-agent",
                    channel=FactChannel.CHANNEL_A,
                    acquisition_method=FactAcquisition.STATED,
                    turn_id=conversation_id,
                    source_path="ORDER_CONFIRMATION",
                )
            await self._append_observed(case_id, observed_facts)
        return ConfirmedCase(case_id=str(created["caseId"]), already_existed=already_existed)

    async def _append_observed(
        self, case_id: str, observed_facts: tuple[dict[str, Any], ...]
    ) -> None:
        """Write what the associate stated before the case existed.

        Written only on the turn that creates the case, alongside the
        confirmation facts: `append_case_fact` is append-only, so flushing the
        same conversation's facts again on a retried confirmation would leave
        the log claiming the associate stated the reason twice.

        Each fact keeps the turn and the message it was captured in. Stamping
        them with this turn's identity would be quicker and would be a lie --
        the record would say every fact was established at confirmation, which
        is precisely the record that hides a reason having been re-asked in
        between. `acquisition_method` distinguishes what someone said from what
        was computed from it, which is what a later agent reads to decide
        whether a fact is worth re-asking at all.
        """
        for entry in observed_facts:
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            if entry.get("status") not in {None, "USABLE", "CONFIRMATION_REQUIRED"}:
                # Conflicting, invalid, ambiguous or stale. The conversation
                # still owes the associate a question about it, so it is not yet
                # something the case knows.
                continue
            acquisition = (
                FactAcquisition.DERIVED
                if entry.get("acquisition") == "DERIVED"
                else FactAcquisition.STATED
            )
            await self._repository.append_case_fact(
                fact_id=str(uuid.uuid4()),
                case_id=case_id,
                fact_name=name,
                value=entry.get("value"),
                agent_id="order-discovery-agent",
                channel=FactChannel.CHANNEL_A,
                acquisition_method=acquisition,
                turn_id=str(entry.get("turnId") or ""),
                source_path=(
                    f"CONVERSATION_MESSAGE:{entry['sourceMessageId']}"
                    if entry.get("sourceMessageId")
                    else "CONVERSATION_MESSAGE"
                ),
            )
