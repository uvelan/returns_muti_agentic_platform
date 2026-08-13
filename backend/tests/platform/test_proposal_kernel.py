"""The shared proposal kernel: one lifecycle, three types, re-checked at activation.

What is asserted here is the set of properties that make the kernel worth having
rather than a table of state names: a decision is final, a change that moved
cannot keep its review, a forbidden key is refused *again* at activation, and a
record that lies about its own diff does not activate.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.platform.governance.errors import (
    ForbiddenProposalKey,
    InvalidProposalTransition,
    ProposalIntegrityError,
    UnknownProposal,
)
from return_platform.platform.governance.kernel import NoActivatorRegistered, ProposalKernel
from return_platform.platform.governance.key_policy import (
    KeyPolicy,
    evaluate_keys,
    policy_for,
    resolve_improvement_key,
)
from return_platform.platform.governance.ports import ActivationReceipt
from return_platform.platform.governance.proposal import (
    ChangeKind,
    Proposal,
    ProposalStatus,
    ProposalType,
    RiskLevel,
    diff_documents,
)
from tests.governance_doubles import InMemoryProposalStore, RecordingAudit, build_test_kernel

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)


class RecordingActivator:
    def __init__(self, reference: str = "release-1") -> None:
        self.reference = reference
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    async def activate(
        self,
        proposal: Proposal,
        *,
        actor: str,
        occurred_at: datetime,
        parameters: Mapping[str, Any],
    ) -> ActivationReceipt:
        del actor, occurred_at
        self.calls.append((proposal.proposal_id, dict(parameters)))
        return ActivationReceipt(reference=self.reference, detail="applied")


async def _reviewed(
    kernel: ProposalKernel,
    *,
    proposal_type: ProposalType = ProposalType.IMPROVEMENT,
    subject_id: str = "subject-1",
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> Proposal:
    proposal = await kernel.submit(
        proposal_type=proposal_type,
        subject_id=subject_id,
        title="a change",
        before=before if before is not None else {"returns": {"reminders": {"max_reminders": 3}}},
        after=after if after is not None else {"returns": {"reminders": {"max_reminders": 2}}},
        evidence=("session:s-1",),
        proposed_by="proposer",
        occurred_at=NOW,
    )
    proposal = await kernel.validate(
        proposal.proposal_id, receipt="receipt-1", actor="proposer", occurred_at=NOW
    )
    return await kernel.submit_for_review(proposal.proposal_id, actor="proposer", occurred_at=NOW)


# --- the diff is derived, not asserted --------------------------------------


def test_the_diff_addresses_leaves_and_classifies_each_change() -> None:
    entries = diff_documents(
        {"a": {"kept": 1, "changed": 2, "removed": 3}},
        {"a": {"kept": 1, "changed": 9, "added": 4}},
    )
    assert [(entry.key, entry.change) for entry in entries] == [
        ("a.added", ChangeKind.ADDED),
        ("a.changed", ChangeKind.CHANGED),
        ("a.removed", ChangeKind.REMOVED),
    ]


def test_a_list_is_one_leaf_not_an_index_per_element() -> None:
    """Addressing `fields.2.priority` would tie the permitted-key policy to an
    ordering nothing promises to keep stable."""
    entries = diff_documents({"fields": [1, 2, 3]}, {"fields": [1, 9, 3]})
    assert [entry.key for entry in entries] == ["fields"]


@pytest.mark.asyncio
async def test_risk_follows_the_shape_of_the_change() -> None:
    kernel, _, _ = build_test_kernel()
    added = await kernel.submit(
        proposal_type=ProposalType.GRAPH_SCHEMA,
        subject_id="d1",
        title="add",
        before={},
        after={"entities": {"Order": {"label": "Order"}}},
        proposed_by="analyst",
        occurred_at=NOW,
    )
    assert added.risk is RiskLevel.LOW

    removed = await kernel.submit(
        proposal_type=ProposalType.GRAPH_SCHEMA,
        subject_id="d2",
        title="remove",
        before={"entities": {"Order": {"label": "Order"}}},
        after={"entities": {}},
        proposed_by="analyst",
        occurred_at=NOW,
    )
    assert removed.risk is RiskLevel.HIGH


# --- lifecycle ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_decision_is_final() -> None:
    kernel, _, _ = build_test_kernel()
    proposal = await _reviewed(kernel)
    approved = await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=LATER)
    assert approved.status is ProposalStatus.APPROVED
    with pytest.raises(InvalidProposalTransition):
        await kernel.reject(proposal.proposal_id, actor="someone-else", occurred_at=LATER)


@pytest.mark.asyncio
async def test_a_rejected_proposal_is_terminal() -> None:
    kernel, _, _ = build_test_kernel()
    proposal = await _reviewed(kernel)
    await kernel.reject(proposal.proposal_id, actor="reviewer", occurred_at=LATER, note="no")
    with pytest.raises(InvalidProposalTransition):
        await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=LATER)


@pytest.mark.asyncio
async def test_a_second_proposal_for_one_subject_supersedes_the_first() -> None:
    """Two live proposals for one subject are two reviewers approving two
    different futures of the same thing."""
    kernel, store, _ = build_test_kernel()
    first = await _reviewed(kernel)
    await kernel.submit(
        proposal_type=ProposalType.IMPROVEMENT,
        subject_id="subject-1",
        title="a newer change",
        before={"returns": {"reminders": {"max_reminders": 3}}},
        after={"returns": {"reminders": {"max_reminders": 1}}},
        proposed_by="proposer",
        occurred_at=LATER,
    )
    assert store.proposals[first.proposal_id].status is ProposalStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_only_an_approved_proposal_activates() -> None:
    kernel, _, _ = build_test_kernel({ProposalType.IMPROVEMENT: RecordingActivator()})
    proposal = await _reviewed(kernel)
    with pytest.raises(InvalidProposalTransition):
        await kernel.activate(proposal.proposal_id, actor="operator", occurred_at=LATER)


@pytest.mark.asyncio
async def test_activation_records_the_receipt_and_retires_its_predecessor() -> None:
    activator = RecordingActivator()
    kernel, store, _ = build_test_kernel({ProposalType.IMPROVEMENT: activator})

    first = await _reviewed(kernel)
    await kernel.approve(first.proposal_id, actor="reviewer", occurred_at=LATER)
    activated, receipt = await kernel.activate(
        first.proposal_id, actor="operator", occurred_at=LATER, parameters={"activate": True}
    )
    assert activated.status is ProposalStatus.ACTIVATED
    assert activated.activation_reference == receipt.reference
    assert activator.calls == [(first.proposal_id, {"activate": True})]

    second = await _reviewed(kernel)
    await kernel.approve(second.proposal_id, actor="reviewer", occurred_at=LATER)
    await kernel.activate(second.proposal_id, actor="operator", occurred_at=LATER)
    assert store.proposals[first.proposal_id].status is ProposalStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_the_next_proposals_before_is_what_is_actually_running() -> None:
    """A diff against a document assembled for the occasion tells a reviewer
    what the author was looking at, not what is live."""
    kernel, _, _ = build_test_kernel({ProposalType.IMPROVEMENT: RecordingActivator()})
    first = await _reviewed(kernel)
    await kernel.approve(first.proposal_id, actor="reviewer", occurred_at=LATER)
    await kernel.activate(first.proposal_id, actor="operator", occurred_at=LATER)

    successor = await kernel.submit(
        proposal_type=ProposalType.IMPROVEMENT,
        subject_id="subject-1",
        title="another change",
        after={"returns": {"reminders": {"max_reminders": 1}}},
        proposed_by="proposer",
        occurred_at=LATER,
    )
    assert successor.before == {"returns": {"reminders": {"max_reminders": 2}}}
    assert successor.affected_keys == ("returns.reminders.max_reminders",)


@pytest.mark.asyncio
async def test_an_approved_proposal_with_no_activator_is_refused_not_marked_live() -> None:
    kernel, store, _ = build_test_kernel()
    proposal = await _reviewed(kernel)
    await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=LATER)
    with pytest.raises(NoActivatorRegistered):
        await kernel.activate(proposal.proposal_id, actor="operator", occurred_at=LATER)
    assert store.proposals[proposal.proposal_id].status is ProposalStatus.APPROVED


@pytest.mark.asyncio
async def test_a_failed_activation_leaves_the_proposal_approved() -> None:
    class FailingActivator:
        async def activate(
            self,
            proposal: Proposal,
            *,
            actor: str,
            occurred_at: datetime,
            parameters: Mapping[str, Any],
        ) -> ActivationReceipt:
            raise RuntimeError("the release store is unreachable")

    kernel, store, _ = build_test_kernel({ProposalType.IMPROVEMENT: FailingActivator()})
    proposal = await _reviewed(kernel)
    await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=LATER)
    with pytest.raises(RuntimeError):
        await kernel.activate(proposal.proposal_id, actor="operator", occurred_at=LATER)
    assert store.proposals[proposal.proposal_id].status is ProposalStatus.APPROVED


@pytest.mark.asyncio
async def test_an_unknown_proposal_is_named_not_silently_missing() -> None:
    kernel, _, _ = build_test_kernel()
    with pytest.raises(UnknownProposal):
        await kernel.get("proposal-nope")


# --- the permitted-key policy ------------------------------------------------


def test_the_policy_is_derived_from_the_type() -> None:
    assert policy_for(ProposalType.IMPROVEMENT) is KeyPolicy.ALLOWLIST
    assert policy_for(ProposalType.CONFIGURATION) is KeyPolicy.DENYLIST
    assert policy_for(ProposalType.GRAPH_SCHEMA) is KeyPolicy.NOT_KEY_SCOPED


def test_a_forbidden_key_is_caught_however_deeply_it_is_nested() -> None:
    """A prefix-only matcher is defeated by one level of nesting, which is
    exactly the shape a document editor produces."""
    decision = evaluate_keys(KeyPolicy.DENYLIST, ["agent.payload.secrets.api_key"])
    assert decision.forbidden == ("agent.payload.secrets.api_key",)
    assert not decision.accepted


def test_a_source_connection_block_is_forbidden_through_its_wildcard() -> None:
    decision = evaluate_keys(KeyPolicy.DENYLIST, ["sources.sales_inv.connection.dsn"])
    assert decision.forbidden == ("sources.sales_inv.connection.dsn",)


def test_an_unlisted_key_is_refused_under_the_allowlist() -> None:
    decision = evaluate_keys(KeyPolicy.ALLOWLIST, ["returns.workflow.bay_wait_seconds"])
    assert decision.not_permitted == ("returns.workflow.bay_wait_seconds",)


def test_a_permitted_prefix_with_no_configuration_field_is_refused() -> None:
    """Accepting it would produce a proposal nothing could ever activate."""
    decision = evaluate_keys(KeyPolicy.ALLOWLIST, ["returns.support.template.acknowledgement"])
    assert decision.unresolvable == ("returns.support.template.acknowledgement",)


def test_numeric_bounds_are_enforced_server_side() -> None:
    key = "returns.discovery.scoring.anchor_weight.ORDER_NUMBER"
    assert resolve_improvement_key(key) is not None
    assert evaluate_keys(KeyPolicy.ALLOWLIST, [key], {key: 40}).accepted
    assert evaluate_keys(KeyPolicy.ALLOWLIST, [key], {key: 1_000_000_000}).out_of_bounds == (key,)


@pytest.mark.asyncio
async def test_a_forbidden_key_is_refused_at_submission() -> None:
    kernel, _, _ = build_test_kernel()
    with pytest.raises(ForbiddenProposalKey):
        await kernel.submit(
            proposal_type=ProposalType.IMPROVEMENT,
            subject_id="subject-1",
            title="quietly widen access",
            before={"capabilities": {"approve": ["admin"]}},
            after={"capabilities": {"approve": ["admin", "everyone"]}},
            proposed_by="model",
            occurred_at=NOW,
        )


@pytest.mark.asyncio
async def test_activation_re_checks_the_key_policy_against_the_stored_record() -> None:
    """Never trust the UI -- or anything else that can write the collection.

    The proposal is walked past the submission-time check by writing a forbidden
    document straight into the store, which is precisely what a compromised
    surface or a bad migration would produce.
    """
    activator = RecordingActivator()
    kernel, store, _ = build_test_kernel({ProposalType.IMPROVEMENT: activator})
    proposal = await _reviewed(kernel)
    approved = await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=LATER)

    forged_after = {"auth": {"required": False}}
    forged_diff = diff_documents(approved.before, forged_after)
    store.proposals[approved.proposal_id] = approved.model_copy(
        update={
            "after": forged_after,
            "diff": forged_diff,
            "affected_keys": tuple(entry.key for entry in forged_diff),
        }
    )

    with pytest.raises(ForbiddenProposalKey):
        await kernel.activate(approved.proposal_id, actor="operator", occurred_at=LATER)
    assert activator.calls == []


@pytest.mark.asyncio
async def test_a_record_that_disagrees_with_its_own_documents_does_not_activate() -> None:
    """A reviewer approves a diff; an activator applies a document. If those two
    can disagree, the review is decorative."""
    activator = RecordingActivator()
    kernel, store, _ = build_test_kernel({ProposalType.IMPROVEMENT: activator})
    proposal = await _reviewed(kernel)
    approved = await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=LATER)

    store.proposals[approved.proposal_id] = approved.model_copy(
        update={"after": {"returns": {"reminders": {"max_reminders": 50}}}}
    )
    with pytest.raises(ProposalIntegrityError):
        await kernel.activate(approved.proposal_id, actor="operator", occurred_at=LATER)
    assert activator.calls == []


@pytest.mark.asyncio
async def test_a_graph_schema_proposal_is_not_measured_against_configuration_keys() -> None:
    """Its keys are entity paths. An allowlist of configuration keys would
    refuse every schema change and prove nothing."""
    kernel, _, _ = build_test_kernel()
    proposal = await kernel.submit(
        proposal_type=ProposalType.GRAPH_SCHEMA,
        subject_id="draft-1",
        title="a schema",
        before={},
        after={"entities": {"Order": {"identifier_properties": ["order_id"]}}},
        proposed_by="analyst",
        occurred_at=NOW,
    )
    assert proposal.status is ProposalStatus.DRAFT
    assert proposal.affected_keys == ("entities.Order.identifier_properties",)


# --- audit -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_transition_reaches_the_audit_trail() -> None:
    kernel, _, audit = build_test_kernel({ProposalType.IMPROVEMENT: RecordingActivator()})
    proposal = await _reviewed(kernel)
    await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=LATER)
    await kernel.activate(proposal.proposal_id, actor="operator", occurred_at=LATER)
    assert audit.actions() == [
        "PROPOSAL_SUBMITTED",
        "PROPOSAL_VALIDATED",
        "PROPOSAL_REVIEW_REQUESTED",
        "PROPOSAL_APPROVED",
        "PROPOSAL_ACTIVATED",
    ]
    assert audit.entries[-1]["actor"] == "operator"
    assert audit.entries[-1]["target"] == proposal.proposal_id


def test_the_in_memory_double_satisfies_the_store_port() -> None:
    from return_platform.platform.governance.ports import ProposalStorePort

    assert isinstance(InMemoryProposalStore(), ProposalStorePort)
    assert isinstance(RecordingAudit(), object)
