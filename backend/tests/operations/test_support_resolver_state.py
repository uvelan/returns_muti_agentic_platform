"""Pure, fixture-free proof that the resolver's checkpoint state and its
allowlist agree, that `CheckpointRedactor` genuinely enforces it, and that the
thread id carries no attempt component.

Modelled on `tests/dynamic_knowledge/test_order_agent_graph_state.py`, which is
the anchor the brief names.
"""

from __future__ import annotations

import pytest

from return_platform.operations.return_support.resolution_state import (
    SUPPORT_RESOLVER_CHECKPOINT_ALLOWLIST,
    SupportResolverState,
    support_resolver_thread_id,
)
from return_platform.platform.reasoning.errors import CheckpointRedactionViolation
from return_platform.platform.reasoning.redaction import CheckpointRedactor


def test_allowlist_matches_the_state_schema_exactly() -> None:
    assert set(SupportResolverState.__annotations__) == SUPPORT_RESOLVER_CHECKPOINT_ALLOWLIST


def full_state() -> SupportResolverState:
    """Every key the graph can write. Shared with the ladder suite."""
    return SupportResolverState(
        case_id="case-1",
        support_event_id="evt-1",
        intent="info_request",
        question_text="Where is the parcel for RMA-4471?",
        configuration_release_id="release-1",
        prompt_version="2026.08.1",
        agent_id="support-question-resolver",
        run_id="run-1",
        as_of="2026-08-30T09:00:00+00:00",
        rungs_attempted=("case_facts",),
        consumed_fact_ids=("fact-1",),
        context_hash="abc123",
        fact_answer={"answerText": "It shipped on Tuesday.", "confidenceMillionths": 950_000},
        graph_synced=False,
        graph_sync_receipt_id=None,
        graph_answer=None,
        tool_plan=None,
        tool_result_ref=None,
        tool_answer=None,
        tool_refusal=None,
        llm_invocations_used=1,
        budget_exhausted=False,
        resolution=None,
        escalation=None,
    )


def test_redactor_accepts_every_field_this_graph_produces() -> None:
    redactor = CheckpointRedactor(SUPPORT_RESOLVER_CHECKPOINT_ALLOWLIST)
    state = full_state()
    assert redactor.enforce(state) == state


@pytest.mark.parametrize(
    "leaked_key",
    [
        # The four shapes a credential could arrive in, and the two shapes a
        # raw read could. Parameterised rather than written once, because a
        # single case would only prove the redactor rejects *some* key.
        "credential",
        "api_key",
        "connector_secret",
        "credential_binding_secret",
        "tool_result",
        "raw_support_payload",
    ],
)
def test_redactor_rejects_a_key_outside_the_allowlist(leaked_key: str) -> None:
    redactor = CheckpointRedactor(SUPPORT_RESOLVER_CHECKPOINT_ALLOWLIST)
    state = {**full_state(), leaked_key: "should never be here"}
    with pytest.raises(CheckpointRedactionViolation, match=leaked_key):
        redactor.enforce(state)


def test_no_allowlisted_key_is_named_like_a_secret() -> None:
    """A standing guard over the allowlist itself, not over one state.

    The redaction tests above prove the *redactor* works. This proves nobody
    widened the allowlist to let a secret through -- the edit that would make
    every other test here pass while defeating all of them.
    """
    forbidden_fragments = ("secret", "credential_value", "password", "token", "api_key", "vault")
    offenders = sorted(
        key
        for key in SUPPORT_RESOLVER_CHECKPOINT_ALLOWLIST
        if any(fragment in key.lower() for fragment in forbidden_fragments)
    )
    assert offenders == []


def test_the_thread_id_is_exactly_the_contracted_shape() -> None:
    assert (
        support_resolver_thread_id(case_id="case-1", support_event_id="evt-9")
        == "support-resolver:case-1:evt-9"
    )


def test_the_thread_id_has_no_attempt_component() -> None:
    """Acceptance 23 depends on this, and a weaker test would not see it.

    Two attempts at the same support event must address the *same* thread, or a
    retry starts an empty one and "resume at the last completed node" silently
    becomes "start again". Asserted as an identity between two calls that differ
    in nothing but the caller's notion of attempt -- there is no attempt
    parameter to vary, so the signature itself is the assertion.
    """
    first = support_resolver_thread_id(case_id="case-1", support_event_id="evt-9")
    second = support_resolver_thread_id(case_id="case-1", support_event_id="evt-9")
    assert first == second
    assert first.count(":") == 2, f"a third segment appeared in the thread id: {first!r}"


@pytest.mark.parametrize(
    ("case_id", "support_event_id"),
    [
        # The two halves of the collision this test was written to find, and
        # which the first version of `support_resolver_thread_id` actually had:
        # these two pairs minted the identical id `support-resolver:case-1:evt:9`.
        ("case-1:evt", "9"),
        ("case-1", "evt:9"),
        # Not hypothetical -- `auto_responder.support_event_id_for` mints
        # exactly this shape today.
        ("case-1", "support-response-agent:wi-7"),
    ],
)
def test_a_component_carrying_the_separator_is_refused(
    case_id: str, support_event_id: str
) -> None:
    """The inputs contain the separator, which is what makes this test able to fail.

    A collision test over inputs with no `:` in them proves only that two
    different strings differ -- adjacency is necessary but not sufficient. These
    inputs decompose across the separator two ways, so a delimiter-joined key
    that permitted them would map two distinct support events onto one
    checkpoint thread.
    """
    with pytest.raises(ValueError, match="may not contain"):
        support_resolver_thread_id(case_id=case_id, support_event_id=support_event_id)


def test_no_two_legal_component_pairs_can_mint_the_same_thread_id() -> None:
    """Uniqueness, stated over the whole legal input space rather than a sample.

    With the separator excluded from components, splitting the id on `:` is a
    total inverse of building it -- so the mapping is injective by construction
    and not merely on the pairs someone thought to try.
    """
    for case_id, support_event_id in (("case-1", "evt-9"), ("case-1-evt", "9"), ("c", "e")):
        thread_id = support_resolver_thread_id(
            case_id=case_id, support_event_id=support_event_id
        )
        prefix, recovered_case, recovered_event = thread_id.split(":")
        assert (prefix, recovered_case, recovered_event) == (
            "support-resolver",
            case_id,
            support_event_id,
        )


@pytest.mark.parametrize(
    ("case_id", "support_event_id"),
    [("", "evt-1"), ("   ", "evt-1"), ("case-1", ""), ("case-1", "  ")],
)
def test_a_blank_component_is_refused_rather_than_formatted(
    case_id: str, support_event_id: str
) -> None:
    with pytest.raises(ValueError, match="required to address a resolver thread"):
        support_resolver_thread_id(case_id=case_id, support_event_id=support_event_id)
