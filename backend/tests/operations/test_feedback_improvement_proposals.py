"""W4.4: feedback emits a typed improvement, governed by the shared kernel.

The defect being closed is specific: `reviewStatus` was stamped `REVIEW_PENDING`
and nothing could transition it. So the assertions are about the two things that
were missing -- a proposal a reviewer can actually act on, and a path from that
proposal into the configuration the platform runs -- plus the boundary that stops
the analysis from proposing anything it likes.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from return_platform.bootstrap.adapters.governance_improvement import (
    ImprovementProposalActivator,
    apply_improvement_changes,
)
from return_platform.configuration.graph_repository import (
    InMemoryConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import (
    AI_GATEWAY_DOMAIN_KEY,
    DEPENDENCY_SIMULATION_DOMAIN_KEY,
    RETURN_PLATFORM_DOMAIN_KEY,
)
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.operations.feedback_improvement import (
    AMBIGUITY_GAP_STEP_MILLIONTHS,
    build_improvement_changes,
    changes_to_documents,
)
from return_platform.platform.governance.errors import ActivationRefused, ForbiddenProposalKey
from return_platform.platform.governance.proposal import ProposalStatus, ProposalType
from return_platform.resources import RuntimeResources
from tests.governance_doubles import build_test_kernel

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def configuration(test_settings: Settings) -> ReturnPlatformConfiguration:
    return load_return_configuration(test_settings.return_configuration_path).configuration


# --- the rules ---------------------------------------------------------------


def test_a_clean_return_proposes_nothing(configuration: ReturnPlatformConfiguration) -> None:
    """A proposal per return would make the queue unreadable, so the expected
    answer is usually none."""
    assert (
        build_improvement_changes(
            configuration=configuration,
            event_types=["RETURN_CREATED", "SUPPORT_HANDOFF_COMPLETED"],
            confirmed_order_line_count=1,
        )
        == ()
    )


def test_support_rework_proposes_one_more_prompt_per_turn(
    configuration: ReturnPlatformConfiguration,
) -> None:
    changes = build_improvement_changes(
        configuration=configuration,
        event_types=["RETURN_SUPPORT_CLARIFICATION_REQUIRED"],
        confirmed_order_line_count=1,
    )
    assert [change.key for change in changes] == [
        "returns.discovery.clarification.max_prompts_per_turn"
    ]
    assert changes[0].before == configuration.clarification_policy.max_prompts_per_turn
    assert changes[0].after == changes[0].before + 1
    assert "RETURN_SUPPORT_CLARIFICATION_REQUIRED" in changes[0].reason


def test_an_unresolved_order_line_proposes_a_wider_ambiguity_gap(
    configuration: ReturnPlatformConfiguration,
) -> None:
    changes = build_improvement_changes(
        configuration=configuration,
        event_types=[],
        confirmed_order_line_count=3,
    )
    assert [change.key for change in changes] == [
        "returns.discovery.scoring.ambiguity_gap_millionths"
    ]
    assert changes[0].after == min(
        1_000_000, configuration.discovery.ambiguity_gap_millionths + AMBIGUITY_GAP_STEP_MILLIONTHS
    )


def test_a_value_already_at_its_bound_proposes_nothing(
    configuration: ReturnPlatformConfiguration,
) -> None:
    """A change of zero would ask a person to approve nothing."""
    at_ceiling = configuration.model_copy(
        update={
            "clarification_policy": configuration.clarification_policy.model_copy(
                update={"max_prompts_per_turn": 5}
            )
        }
    )
    assert (
        build_improvement_changes(
            configuration=at_ceiling,
            event_types=["SUPPORT_REVIEW_REQUIRED"],
            confirmed_order_line_count=1,
        )
        == ()
    )


def test_the_documents_leaf_paths_are_the_permitted_key_names(
    configuration: ReturnPlatformConfiguration,
) -> None:
    """This is what lets the kernel police the keys without a second table."""
    changes = build_improvement_changes(
        configuration=configuration,
        event_types=["SUPPORT_REVIEW_REQUIRED"],
        confirmed_order_line_count=2,
    )
    before, after = changes_to_documents(changes)
    kernel_before = before["returns"]["discovery"]["clarification"]["max_prompts_per_turn"]
    assert kernel_before == configuration.clarification_policy.max_prompts_per_turn
    assert set(after["returns"]["discovery"]) == {"clarification", "scoring"}


# --- the kernel governs it ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_proposal_reaches_the_review_queue(
    configuration: ReturnPlatformConfiguration,
) -> None:
    kernel, store, audit = build_test_kernel()
    changes = build_improvement_changes(
        configuration=configuration,
        event_types=["SUPPORT_REVIEW_REQUIRED"],
        confirmed_order_line_count=1,
    )
    before, after = changes_to_documents(changes)
    proposal = await kernel.submit(
        proposal_type=ProposalType.IMPROVEMENT,
        subject_id="FDB-1",
        title="improvement",
        before=before,
        after=after,
        proposed_by="agent.learning",
        occurred_at=NOW,
    )
    proposal = await kernel.validate(
        proposal.proposal_id, receipt="feedback-evidence:x", actor="agent.learning", occurred_at=NOW
    )
    proposal = await kernel.submit_for_review(
        proposal.proposal_id, actor="agent.learning", occurred_at=NOW
    )
    assert proposal.status is ProposalStatus.REVIEW_PENDING
    assert store.proposals[proposal.proposal_id].affected_keys == (
        "returns.discovery.clarification.max_prompts_per_turn",
    )
    assert "PROPOSAL_REVIEW_REQUESTED" in audit.actions()


@pytest.mark.asyncio
async def test_the_analysis_cannot_propose_a_forbidden_key() -> None:
    """A model that decided the fix was to disable authorization gets a refusal,
    not a review queue entry."""
    kernel, _, _ = build_test_kernel()
    with pytest.raises(ForbiddenProposalKey):
        await kernel.submit(
            proposal_type=ProposalType.IMPROVEMENT,
            subject_id="FDB-2",
            title="improvement",
            before={"workflow": {"authorization": {"required": True}}},
            after={"workflow": {"authorization": {"required": False}}},
            proposed_by="agent.learning",
            occurred_at=NOW,
        )


@pytest.mark.asyncio
async def test_a_value_outside_the_bounds_is_refused() -> None:
    """ "A ranking weight of one billion is a valid number that silently breaks
    elicitation" -- plan section 7."""
    kernel, _, _ = build_test_kernel()
    with pytest.raises(ForbiddenProposalKey):
        await kernel.submit(
            proposal_type=ProposalType.IMPROVEMENT,
            subject_id="FDB-3",
            title="improvement",
            before={"returns": {"discovery": {"scoring": {"anchor_weight": {"ORDER_NUMBER": 40}}}}},
            after={
                "returns": {
                    "discovery": {"scoring": {"anchor_weight": {"ORDER_NUMBER": 1_000_000_000}}}
                }
            },
            proposed_by="agent.learning",
            occurred_at=NOW,
        )


# --- and it reaches the configuration -----------------------------------------


def _payload(configuration: ReturnPlatformConfiguration) -> dict[str, Any]:
    return cast(dict[str, Any], configuration.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_the_change_lands_on_the_configuration_field_the_key_names(
    configuration: ReturnPlatformConfiguration,
) -> None:
    kernel, _, _ = build_test_kernel()
    changes = build_improvement_changes(
        configuration=configuration,
        event_types=["SUPPORT_REVIEW_REQUIRED"],
        confirmed_order_line_count=2,
    )
    before, after = changes_to_documents(changes)
    proposal = await kernel.submit(
        proposal_type=ProposalType.IMPROVEMENT,
        subject_id="FDB-4",
        title="improvement",
        before=before,
        after=after,
        proposed_by="agent.learning",
        occurred_at=NOW,
    )
    updated = apply_improvement_changes(_payload(configuration), proposal)

    assert updated["clarification_policy"]["max_prompts_per_turn"] == (
        configuration.clarification_policy.max_prompts_per_turn + 1
    )
    assert updated["discovery"]["ambiguity_gap_millionths"] == (
        configuration.discovery.ambiguity_gap_millionths + AMBIGUITY_GAP_STEP_MILLIONTHS
    )
    # Still a valid configuration -- the release would refuse it otherwise, and
    # discovering that at publish time is discovering it too late.
    ReturnPlatformConfiguration.model_validate(updated)


@pytest.mark.asyncio
async def test_a_smart_question_priority_is_matched_by_field_not_by_position(
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The position of a question in the list is not something anything promises
    to keep stable."""
    kernel, _, _ = build_test_kernel()
    key = "returns.elicitation.field_priority.phone"
    proposal = await kernel.submit(
        proposal_type=ProposalType.IMPROVEMENT,
        subject_id="FDB-5",
        title="improvement",
        before={"returns": {"elicitation": {"field_priority": {"phone": 90}}}},
        after={"returns": {"elicitation": {"field_priority": {"phone": 130}}}},
        proposed_by="agent.learning",
        occurred_at=NOW,
    )
    assert proposal.affected_keys == (key,)
    updated = apply_improvement_changes(_payload(configuration), proposal)
    phone = next(
        entry for entry in updated["clarification_policy"]["fields"] if entry["field"] == "phone"
    )
    assert phone["priority"] == 130
    ReturnPlatformConfiguration.model_validate(updated)


class _RecordingRuntimeActivator:
    def __init__(self) -> None:
        self.refreshes = 0

    async def refresh(self, *, force: bool = False) -> None:
        del force
        self.refreshes += 1
        return None


@pytest.mark.asyncio
async def test_an_approved_improvement_publishes_a_release_and_refreshes_the_runtime(
    configuration: ReturnPlatformConfiguration,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 7: feedback proposals never activate anything *directly*. They go
    through the release lifecycle, which is what this asserts."""
    from return_platform.ai_gateway.configuration import load_ai_gateway_configuration
    from return_platform.dependency_simulation.configuration import (
        load_dependency_simulation_configuration,
    )

    async def accept_receipts(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "return_platform.configuration.application.release_promotion"
        ".verify_runtime_validation_receipts",
        accept_receipts,
    )

    repository = InMemoryConfigurationGraphRepository()
    baseline = {
        RETURN_PLATFORM_DOMAIN_KEY: _payload(configuration),
        AI_GATEWAY_DOMAIN_KEY: load_ai_gateway_configuration(
            test_settings.ai_gateway_configuration_path
        ).configuration.model_dump(mode="json"),
        DEPENDENCY_SIMULATION_DOMAIN_KEY: load_dependency_simulation_configuration(
            test_settings.dependency_simulation_configuration_path
        ).configuration.model_dump(mode="json"),
    }
    for key, payload in baseline.items():
        await repository.save_draft_domain("baseline", key, copy.deepcopy(payload), actor_id="seed")
    await repository.promote_release("baseline", "VALIDATED", actor_id="seed")
    await repository.promote_release(
        "baseline", "RELEASED", actor_id="seed", expected_head_revision=0
    )

    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    resources.mongo = cast(Any, object())
    runtime = _RecordingRuntimeActivator()
    activator = ImprovementProposalActivator(
        repository=repository,
        resources=resources,
        activator=cast(Any, runtime),
    )
    kernel, _, audit = build_test_kernel({ProposalType.IMPROVEMENT: activator})

    changes = build_improvement_changes(
        configuration=configuration,
        event_types=["SUPPORT_REVIEW_REQUIRED"],
        confirmed_order_line_count=1,
    )
    before, after = changes_to_documents(changes)
    proposal = await kernel.submit(
        proposal_type=ProposalType.IMPROVEMENT,
        subject_id="FDB-6",
        title="improvement",
        before=before,
        after=after,
        proposed_by="agent.learning",
        occurred_at=NOW,
    )
    proposal = await kernel.validate(
        proposal.proposal_id, receipt="feedback-evidence:x", actor="agent.learning", occurred_at=NOW
    )
    proposal = await kernel.submit_for_review(
        proposal.proposal_id, actor="agent.learning", occurred_at=NOW
    )
    await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=NOW)
    activated, receipt = await kernel.activate(
        proposal.proposal_id, actor="reviewer", occurred_at=NOW
    )

    assert activated.status is ProposalStatus.ACTIVATED
    published = await repository.get_release(receipt.reference)
    assert published is not None and published.status == "RELEASED"
    domain = await repository.get_domain_config(receipt.reference, RETURN_PLATFORM_DOMAIN_KEY)
    assert domain is not None
    assert domain["clarification_policy"]["max_prompts_per_turn"] == (
        configuration.clarification_policy.max_prompts_per_turn + 1
    )
    # Every other domain travelled with it.
    assert set(await repository.get_all_domain_configs(receipt.reference)) == set(baseline)
    assert runtime.refreshes >= 1
    assert "PROPOSAL_ACTIVATED" in audit.actions()


@pytest.mark.asyncio
async def test_activation_without_an_active_release_is_refused(
    configuration: ReturnPlatformConfiguration,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> None:
    repository = InMemoryConfigurationGraphRepository()
    resources = RuntimeResources(settings=test_settings, catalog=loaded_empty_catalog)
    activator = ImprovementProposalActivator(
        repository=repository,
        resources=resources,
        activator=cast(Any, _RecordingRuntimeActivator()),
    )
    kernel, _, _ = build_test_kernel({ProposalType.IMPROVEMENT: activator})
    changes = build_improvement_changes(
        configuration=configuration,
        event_types=["SUPPORT_REVIEW_REQUIRED"],
        confirmed_order_line_count=1,
    )
    before, after = changes_to_documents(changes)
    proposal = await kernel.submit(
        proposal_type=ProposalType.IMPROVEMENT,
        subject_id="FDB-7",
        title="improvement",
        before=before,
        after=after,
        proposed_by="agent.learning",
        occurred_at=NOW,
    )
    proposal = await kernel.validate(
        proposal.proposal_id, receipt="r", actor="agent.learning", occurred_at=NOW
    )
    proposal = await kernel.submit_for_review(
        proposal.proposal_id, actor="agent.learning", occurred_at=NOW
    )
    await kernel.approve(proposal.proposal_id, actor="reviewer", occurred_at=NOW)
    with pytest.raises(ActivationRefused):
        await kernel.activate(proposal.proposal_id, actor="reviewer", occurred_at=NOW)
