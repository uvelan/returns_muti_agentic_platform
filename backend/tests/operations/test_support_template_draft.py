"""The template's binding vocabulary has one home, and it is enforced.

RV finding F2: the shipped `production.yaml` bound five `case_fact:` names that
existed nowhere in `backend/src`, and the seam that would produce them was a
docstring. The failure mode was silent by construction -- a phase-2 assembler
spelling one of them differently leaves the field on its `fallback`, raises no
gap, and drops the line out of a message a person then acts on. Nothing in the
suite would have failed.

These tests are what make that impossible. They compare the shipped yaml
against `support_template_draft` in **both** directions, and they check the
fact-log half against the activity that actually writes it rather than against
a comment claiming it does.
"""

from pathlib import Path

import pytest
import yaml

from return_platform.configuration.support_template_configuration import binding_source
from return_platform.operations.support_handoff import (
    SupportHandoffBay,
    SupportHandoffCustomer,
    SupportHandoffOrder,
    SupportHandoffPolicy,
    SupportHandoffReturn,
)
from return_platform.operations.support_template_draft import (
    FACT_LOG_KEYS,
    SNAPSHOT_KEYS,
    TEMPLATE_CASE_FACT_KEYS,
    snapshot_as_facts,
    support_template_snapshot,
)

_BACKEND = Path(__file__).resolve().parents[2]
_PRODUCTION_YAML = _BACKEND / "config" / "returns" / "production.yaml"
_DRAFT_ACTIVITY = _BACKEND / "src" / "return_platform" / "workflows" / "return_case_activities.py"


def _shipped_case_fact_names() -> set[str]:
    """Every `case_fact:` name the shipped template binds, across all variants."""
    document = yaml.safe_load(_PRODUCTION_YAML.read_text(encoding="utf-8"))
    template = document["support_template"]
    names: set[str] = set()
    for variant in template["variants"]:
        for section in variant["sections"]:
            for field in section.get("fields", ()):
                source, path = binding_source(field["source_binding"])
                if source == "case_fact":
                    names.add(path)
    return names


class TestTheVocabularyIsSingleSourced:
    def test_every_shipped_binding_is_declared(self) -> None:
        # The direction that catches a template naming a fact nothing produces.
        undeclared = _shipped_case_fact_names() - TEMPLATE_CASE_FACT_KEYS
        assert undeclared == set(), (
            "production.yaml binds case_fact names that support_template_draft does not "
            "declare; declare them (and produce them) or stop binding them"
        )

    def test_every_declared_name_is_bound(self) -> None:
        # The other direction: vocabulary nothing uses is vocabulary nothing
        # enforces, and contracts.md sect. 4 forbids shipping one.
        unused = TEMPLATE_CASE_FACT_KEYS - _shipped_case_fact_names()
        assert unused == set(), (
            "support_template_draft declares names the shipped template never binds"
        )

    def test_the_two_halves_do_not_overlap(self) -> None:
        # A name cannot be both a fact-log entry and a draft-time snapshot: the
        # provenance a rendered field reports would then depend on which
        # producer happened to win the merge.
        assert SNAPSHOT_KEYS & FACT_LOG_KEYS == set()

    @pytest.mark.parametrize("name", sorted(FACT_LOG_KEYS))
    def test_every_fact_log_name_is_one_the_draft_activity_reads(self, name: str) -> None:
        """Proved against the source, not asserted in a comment.

        `draft_support_request` reads each of these off the fact log today with
        `_stated(facts, "<name>")`. If one is renamed there and not here, the
        template would bind a name nothing writes -- which is F2 exactly, from
        the other end.
        """
        source = _DRAFT_ACTIVITY.read_text(encoding="utf-8")
        assert f'_stated(facts, "{name}")' in source, (
            f"{name} is declared as a fact-log binding but draft_support_request "
            f"does not read it"
        )


class TestTheSnapshotProducer:
    @staticmethod
    def _case(**overrides: object) -> dict:
        case: dict = {
            "case_id": "case-1",
            "work_item_id": "wi-1",
            "created_at": None,
            "workflow_status": "AWAITING_SUPPORT_HANDOFF",
            "customer": SupportHandoffCustomer(name="Rivera"),
            "order": SupportHandoffOrder(reference="CQ1"),
            "return_details": SupportHandoffReturn(),
            "bay": SupportHandoffBay(status="RECOMMENDED", bay_reference="BAY-1"),
            "policy": SupportHandoffPolicy(),
            "order_confirmed": True,
            "required_details_complete": True,
        }
        case.update(overrides)
        return case

    def test_it_produces_only_declared_names(self) -> None:
        produced = set(support_template_snapshot(**self._case()))
        assert produced <= SNAPSHOT_KEYS

    def test_a_recommended_bay_omits_both_of_its_conditional_lines(self) -> None:
        snapshot = support_template_snapshot(**self._case())
        assert "bay_unresolved_reason" not in snapshot
        assert "manual_bay_action_line" not in snapshot

    def test_an_unresolved_bay_carries_both(self) -> None:
        snapshot = support_template_snapshot(
            **self._case(bay=SupportHandoffBay(status="NO_BAY_FREE", unresolved_reason="No bay"))
        )
        assert snapshot["bay_unresolved_reason"] == "No bay"
        assert snapshot["manual_bay_action_line"].startswith("- Resolve manual bay assignment")

    def test_the_contact_arms_are_mutually_exclusive(self) -> None:
        associate = support_template_snapshot(
            **self._case(customer=SupportHandoffCustomer(contact_name="Dana"))
        )
        assert associate["contact_associate_name"] == "Dana"
        # The named associate has no email on file: the line still appears, and
        # says so, because the composed path prints all three or none.
        assert associate["contact_associate_email"] == "Not available"
        assert "contact_customer_phone" not in associate

        customer = support_template_snapshot(
            **self._case(customer=SupportHandoffCustomer(customer_phone="555-0199"))
        )
        assert customer["contact_customer_phone"] == "555-0199"
        assert customer["contact_customer_notice"].startswith("Not recorded")
        assert "contact_associate_name" not in customer

    def test_associate_notes_are_neutralised_before_they_reach_the_template(self) -> None:
        # The template binds the rendered notes, never the raw fact: a note
        # containing this message's own framing would otherwise restructure it
        # for whoever reads it next.
        snapshot = support_template_snapshot(
            **self._case(
                return_details=SupportHandoffReturn(associate_notes="ok\nBAY ASSIGNMENT:\nrush")
            )
        )
        assert "BAY ASSIGNMENT:" not in snapshot["associate_notes_rendered"]
        assert "[removed]" in snapshot["associate_notes_rendered"]

    def test_an_unreadable_case_state_never_reads_as_nothing_outstanding(self) -> None:
        unknown = support_template_snapshot(
            **self._case(support_state_known=False, outstanding_support_dimensions=("RMA",))
        )
        assert unknown["awaiting_from_support"].startswith("UNKNOWN")
        settled = support_template_snapshot(**self._case(support_state_known=True))
        assert "awaiting_from_support" not in settled

    def test_snapshot_values_carry_no_fact_id(self) -> None:
        # The absence *is* the provenance: a rendered field with no fact id is
        # one the draft derived rather than one the fact log recorded.
        facts = snapshot_as_facts(support_template_snapshot(**self._case()))
        assert all("factId" not in entry for entry in facts.values())
        assert all(scope is None for scope, _ in facts)
