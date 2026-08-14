"""Adversarial scenario #36: what the associate said first is not asked again.

The audit's scenario is one sentence -- "I need to return the damaged red pump
from order CW273354" -- and it establishes five things before any search runs: a
return reason, a condition, a colour, a product and an order. Discovery used all
of it to search and kept none of it as a fact. The only facts a case ever
received were written at confirmation, from the confirmation itself, so the
reason the associate volunteered unprompted was asked for again later. Scenario
#36 had no test at all.

The other half is the exception list, and it is the half that is easy to get
wrong in the safe-looking direction: an implementation that re-asks whenever it
is unsure passes a naive "does it remember?" test and still annoys every
associate it meets. So the five conditions that *do* justify asking again --
conflicting, invalid, ambiguous, stale, confirmation-required -- are each
asserted, and so is the fact that nothing else does.

Scenario #35 is regression-guarded here rather than rebuilt: a candidate matched
on a misspelled name must stay evidence and never become a fact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.return_configuration import (
    SmartQuestionConfiguration,
    load_return_configuration,
)
from return_platform.dynamic_knowledge.order_agent.contracts import AgentAction, ObservedFact
from return_platform.dynamic_knowledge.order_agent.facts import (
    CapturedFact,
    FactCatalogue,
    FactStatus,
    build_fact_catalogue,
)

REPOSITORY_BACKEND = Path(__file__).parents[2]
TURN_AS_OF = datetime(2026, 8, 14, 9, 30, tzinfo=UTC)

#: The audit's own sentence, decomposed the way a model reports it.
OPENING_TURN = (
    ObservedFact(fact="return_reason", value="damaged", source_message_id="m1"),
    ObservedFact(fact="product_colour", value="red", source_message_id="m1"),
    ObservedFact(fact="product_description", value="pump", source_message_id="m1"),
    ObservedFact(fact="order_number", value="CW273354", source_message_id="m1"),
    ObservedFact(fact="product_presence", value="with the customer", source_message_id="m1"),
)


@pytest.fixture(scope="module")
def clarification_policy() -> SmartQuestionConfiguration:
    return load_return_configuration(
        REPOSITORY_BACKEND / "config/returns/production.yaml"
    ).configuration.clarification_policy


@pytest.fixture(scope="module")
def catalogue(clarification_policy: SmartQuestionConfiguration) -> FactCatalogue:
    return build_fact_catalogue(clarification_policy.fields)


def _capture(
    catalogue: FactCatalogue,
    observed: tuple[ObservedFact, ...],
    *,
    existing: tuple[CapturedFact, ...] = (),
    turn_id: str = "turn-1",
    as_of: datetime = TURN_AS_OF,
) -> tuple[tuple[CapturedFact, ...], tuple[str, ...]]:
    return catalogue.capture(existing, observed, turn_id=turn_id, as_of=as_of)


def _by_name(facts: tuple[CapturedFact, ...]) -> dict[str, CapturedFact]:
    return {fact.name: fact for fact in facts}


# --- the opening sentence -----------------------------------------------------


def test_the_opening_sentence_is_captured_whole(catalogue: FactCatalogue) -> None:
    """Five facts, from one message, before any search has run."""
    captured, unknown = _capture(catalogue, OPENING_TURN)
    facts = _by_name(captured)

    assert unknown == ()
    assert facts["return_reason"].value == "damaged"
    assert facts["product_colour"].value == "red"
    assert facts["product_description"].value == "pump"
    assert facts["order_number"].value == "CW273354"
    assert facts["product_presence"].value == "with the customer"


def test_every_captured_fact_carries_its_own_provenance(catalogue: FactCatalogue) -> None:
    """Provenance is per fact, taken where the fact was stated.

    Not the confirmation's provenance: a record saying every fact was
    established at confirmation is exactly the record in which nobody can see
    that the reason was re-asked in between.
    """
    captured, _ = _capture(catalogue, OPENING_TURN, turn_id="turn-1")

    for fact in captured:
        assert fact.turn_id == "turn-1"
        assert fact.source_message_id == "m1"
        assert fact.acquisition == "STATED"
        assert fact.observed_at == TURN_AS_OF.isoformat()
        assert fact.label


def test_a_captured_fact_is_not_asked_for_again(catalogue: FactCatalogue) -> None:
    """Scenario #36 in one line."""
    captured, _ = _capture(catalogue, OPENING_TURN)

    assert all(not fact.needs_asking_again for fact in captured)
    assert _by_name(captured)["return_reason"].status == FactStatus.USABLE.value


def test_the_model_is_shown_what_is_already_known(catalogue: FactCatalogue) -> None:
    """Remembering a fact the model cannot see is the same as forgetting it."""
    captured, _ = _capture(catalogue, OPENING_TURN)
    described = {entry["fact"]: entry for entry in (fact.describe() for fact in captured)}

    assert described["return_reason"]["value"] == "damaged"
    assert described["return_reason"]["status"] == FactStatus.USABLE.value
    assert "askAgainBecause" not in described["return_reason"]


def test_restating_the_same_fact_is_not_a_conflict(catalogue: FactCatalogue) -> None:
    """Associates repeat themselves; that is not new information."""
    first, _ = _capture(catalogue, OPENING_TURN)
    second, _ = _capture(
        catalogue,
        (ObservedFact(fact="return_reason", value="damaged", source_message_id="m3"),),
        existing=first,
        turn_id="turn-2",
    )

    assert _by_name(second)["return_reason"].status == FactStatus.USABLE.value
    assert not _by_name(second)["return_reason"].needs_asking_again


def test_a_later_turn_keeps_the_earlier_turns_facts(catalogue: FactCatalogue) -> None:
    """Nothing is dropped by a turn that happens to state something else."""
    first, _ = _capture(catalogue, OPENING_TURN)
    second, _ = _capture(
        catalogue,
        (ObservedFact(fact="zip_code", value="75201", source_message_id="m2"),),
        existing=first,
        turn_id="turn-2",
    )

    assert _by_name(second)["return_reason"].value == "damaged"
    assert _by_name(second)["return_reason"].turn_id == "turn-1"
    assert _by_name(second)["zip_code"].turn_id == "turn-2"


# --- the five exceptions, and only those --------------------------------------


def test_a_conflicting_restatement_is_raised_again(catalogue: FactCatalogue) -> None:
    """Two different answers, and choosing between them is not ours to make."""
    first, _ = _capture(catalogue, OPENING_TURN)
    second, _ = _capture(
        catalogue,
        (ObservedFact(fact="return_reason", value="wrong item", source_message_id="m3"),),
        existing=first,
        turn_id="turn-2",
    )
    reason = _by_name(second)["return_reason"]

    assert reason.status == FactStatus.CONFLICTING.value
    assert reason.needs_asking_again
    assert reason.describe()["askAgainBecause"] == FactStatus.CONFLICTING.value


def test_a_fact_the_model_was_unsure_of_is_raised_again(catalogue: FactCatalogue) -> None:
    """Kept and marked rather than discarded, so one question resolves it."""
    captured, _ = _capture(
        catalogue,
        (ObservedFact(fact="return_reason", value="maybe damaged", ambiguous=True),),
    )

    assert _by_name(captured)["return_reason"].status == FactStatus.AMBIGUOUS.value
    assert _by_name(captured)["return_reason"].needs_asking_again


def test_a_value_failing_configured_validation_is_raised_again(
    clarification_policy: SmartQuestionConfiguration,
) -> None:
    """Asked again for a reason that can be named, not silently ignored."""
    payload = clarification_policy.model_dump(mode="json")
    for entry in payload["fields"]:
        if entry["field"] == "zip_code":
            entry["validation_pattern"] = "^[0-9]{5}$"
    catalogue = build_fact_catalogue(SmartQuestionConfiguration.model_validate(payload).fields)
    captured, _ = _capture(catalogue, (ObservedFact(fact="zip_code", value="not-a-zip"),))

    assert _by_name(captured)["zip_code"].status == FactStatus.INVALID.value
    assert _by_name(captured)["zip_code"].needs_asking_again


def test_a_fact_past_its_configured_lifetime_is_raised_again(
    clarification_policy: SmartQuestionConfiguration,
) -> None:
    """Staleness arrives by time passing rather than by anyone saying anything.

    So it is recomputed against the turn's as-of rather than stored -- a fact
    captured as usable can become stale while nothing about it changes.
    """
    payload = clarification_policy.model_dump(mode="json")
    for entry in payload["fields"]:
        if entry["field"] == "product_presence":
            entry["answer_ttl_seconds"] = 3600
    catalogue = build_fact_catalogue(SmartQuestionConfiguration.model_validate(payload).fields)
    captured, _ = _capture(catalogue, (ObservedFact(fact="product_presence", value="in the van"),))
    assert _by_name(captured)["product_presence"].status == FactStatus.USABLE.value

    later = catalogue.refresh(captured, as_of=TURN_AS_OF + timedelta(hours=2))

    assert _by_name(later)["product_presence"].status == FactStatus.STALE.value
    assert _by_name(later)["product_presence"].needs_asking_again


def test_a_fact_requiring_read_back_is_raised_again(
    clarification_policy: SmartQuestionConfiguration,
) -> None:
    """However clearly it was stated, some answers must be confirmed."""
    payload = clarification_policy.model_dump(mode="json")
    for entry in payload["fields"]:
        if entry["field"] == "return_reason":
            entry["confirmation_required"] = True
    catalogue = build_fact_catalogue(SmartQuestionConfiguration.model_validate(payload).fields)
    captured, _ = _capture(catalogue, OPENING_TURN)

    assert _by_name(captured)["return_reason"].status == FactStatus.CONFIRMATION_REQUIRED.value
    assert _by_name(captured)["return_reason"].needs_asking_again


def test_nothing_outside_the_five_conditions_is_raised_again(
    catalogue: FactCatalogue,
) -> None:
    """The half that is easy to fail in the safe-looking direction.

    An implementation that re-asks whenever it is unsure passes "does it
    remember?" and still asks the associate everything twice.
    """
    captured, _ = _capture(catalogue, OPENING_TURN)
    reaskable = {fact.name for fact in captured if fact.needs_asking_again}

    assert reaskable == set()


def test_a_fact_no_configured_field_claims_is_reported_not_stored(
    catalogue: FactCatalogue,
) -> None:
    """An unowned value must not reach a case's permanent record.

    A fact nobody configured has no label, no validation and no re-ask rule, so
    storing it would leave it unowned for the rest of its life.
    """
    captured, unknown = _capture(catalogue, (ObservedFact(fact="favourite_colour", value="teal"),))

    assert unknown == ("favourite_colour",)
    assert captured == ()


# --- scenario #35, regression-guarded ----------------------------------------


def test_a_search_result_never_becomes_a_stated_fact(catalogue: FactCatalogue) -> None:
    """A misspelled name resolves to a candidate, never to a fact.

    Scenario #35 is already satisfied by the search path -- fuzzy hits carry
    `customer_name_fuzzy` and a score below every confirmed signal, and only the
    associate confirming a candidate resolves them. This guards the boundary
    from the fact side: capture takes what someone *stated*, and nothing here
    can promote a ranked row into the case record.
    """
    captured, unknown = _capture(
        catalogue,
        (
            ObservedFact(fact="customer_name", value="Jhon Smi", source_message_id="m1"),
            ObservedFact(fact="return_reason", value="damaged", source_message_id="m1"),
        ),
    )
    facts = _by_name(captured)

    # What the associate *said* is a fact, with STATED provenance -- it is not
    # a claim that any customer named Jhon Smi exists.
    assert facts["customer_name"].value == "Jhon Smi"
    assert facts["customer_name"].acquisition == "STATED"
    assert unknown == ()


def test_an_action_carries_facts_on_any_action_type(catalogue: FactCatalogue) -> None:
    """The opening sentence is rarely a confirmation.

    Accepting facts only on CONFIRM_ORDER is how a return reason stated in turn
    one went unrecorded until turn six, by which point it had been asked for
    again.
    """
    action = AgentAction(
        business_capability="order-discovery",
        action_type="ORDER_SEARCH",
        decision_summary="Search for the order the associate named.",
        search_intent={"orderNumbers": ["CW273354"]},
        observed_facts=OPENING_TURN,
    )
    captured, _ = _capture(catalogue, action.observed_facts)

    assert len(captured) == len(OPENING_TURN)


def test_an_action_without_facts_captures_nothing(catalogue: FactCatalogue) -> None:
    """Facts are optional. A turn that states none must not invent any."""
    action = AgentAction(
        business_capability="order-discovery",
        action_type="RESPOND",
        decision_summary="Answer the associate.",
        response={
            "status": "DISCOVERY_COMPLETE",
            "business_capability": "order-discovery",
            "statements": [],
        },
    )
    captured, unknown = _capture(catalogue, action.observed_facts)

    assert captured == ()
    assert unknown == ()


# --- what reaches the case ----------------------------------------------------


class _RecordingRepository:
    """Enough of the operational repository to see what a confirmation writes."""

    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []
        self.cases: dict[str, dict[str, Any]] = {}

    async def find_case_by_confirmation(self, confirmation_key: str) -> dict[str, Any] | None:
        return self.cases.get(confirmation_key)

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        return {}

    async def create_case(self, **fields: Any) -> dict[str, Any]:
        record = {"caseId": fields["case_id"]}
        self.cases[str(fields["confirmation_key"])] = record
        return record

    async def append_case_fact(self, **fields: Any) -> dict[str, Any]:
        self.facts.append(fields)
        return fields


async def _confirm(repository: _RecordingRepository, observed: tuple[dict[str, Any], ...]) -> None:
    from return_platform.dynamic_knowledge.integration.case_store import RepositoryCaseStore
    from return_platform.dynamic_knowledge.order_agent.contracts import OrderConfirmation

    await RepositoryCaseStore(repository).confirm_case(
        tenant_id="t1",
        principal_id="p1",
        branch_ids=("b1",),
        conversation_id="c1",
        confirmation=OrderConfirmation(
            candidate_set_id="cs1",
            candidate_id="CW273354",
            order_reference="CW273354",
        ),
        configuration_release_id="release-1",
        graph_generation_id="generation-1",
        observed_facts=observed,
    )


@pytest.mark.asyncio
async def test_first_turn_facts_reach_the_case_with_the_provenance_they_were_stated_under(
    catalogue: FactCatalogue,
) -> None:
    """The point of capturing them at all.

    WF-01 is closed, so the case path beyond confirmation is live and this is
    now observable rather than theoretical.
    """
    captured, _ = _capture(catalogue, OPENING_TURN, turn_id="turn-1")
    repository = _RecordingRepository()

    await _confirm(repository, tuple(fact.to_state() for fact in captured))

    written = {fact["fact_name"]: fact for fact in repository.facts}
    assert "return_reason" in written
    assert written["return_reason"]["value"] == "damaged"
    assert written["return_reason"]["turn_id"] == "turn-1"
    assert written["return_reason"]["source_path"] == "CONVERSATION_MESSAGE:m1"
    assert written["return_reason"]["channel"].value == "CHANNEL_A"
    assert written["return_reason"]["acquisition_method"].value == "STATED"
    # The confirmation's own facts are still written -- this adds to the audited
    # provenance rather than replacing it.
    assert "confirmed_order_reference" in written


@pytest.mark.asyncio
async def test_a_fact_still_owing_a_question_is_not_written_to_the_case(
    catalogue: FactCatalogue,
) -> None:
    """A conflict is not something the case knows; it is something still owed."""
    first, _ = _capture(catalogue, OPENING_TURN)
    conflicted, _ = _capture(
        catalogue,
        (ObservedFact(fact="return_reason", value="wrong item"),),
        existing=first,
        turn_id="turn-2",
    )
    repository = _RecordingRepository()

    await _confirm(repository, tuple(fact.to_state() for fact in conflicted))

    written = {fact["fact_name"] for fact in repository.facts}
    assert "return_reason" not in written
    assert "product_colour" in written


@pytest.mark.asyncio
async def test_a_retried_confirmation_does_not_double_write_the_facts(
    catalogue: FactCatalogue,
) -> None:
    """`append_case_fact` is append-only, so a second flush would claim the
    associate stated the reason twice."""
    captured, _ = _capture(catalogue, OPENING_TURN)
    repository = _RecordingRepository()
    payload = tuple(fact.to_state() for fact in captured)

    await _confirm(repository, payload)
    first_count = len(repository.facts)
    await _confirm(repository, payload)

    assert len(repository.facts) == first_count
