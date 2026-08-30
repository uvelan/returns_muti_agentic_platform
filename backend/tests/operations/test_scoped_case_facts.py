"""S1 -- record-scoped case facts (contracts.md §4, DR-11).

The fact log grows two additive fields (`record_scope`, `identity_version`)
and two acquisition methods (`ASSOCIATE_EDIT`, `CONTEXT_SUMMARY`). Everything
here is about the additive property: a fact written before S1 existed must
validate, project and dedupe exactly as it did, while the new scoped path gets
a per-record identity of its own.
"""

from datetime import UTC, datetime
from typing import Any

from return_platform.operations.models import CaseFactView, FactAcquisition, FactChannel

_NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


def _stored_fact(**overrides: Any) -> dict[str, Any]:
    """A fact document as `CaseRepository.append_case_fact` stores it -- the

    exact pre-S1 key set, so tests that add keys are visibly opting in.
    """
    document: dict[str, Any] = {
        "factId": "fact-1",
        "caseId": "case-1",
        "factName": "order_reference",
        "value": "CW273354",
        "agentId": "order-discovery-agent",
        "channel": FactChannel.CHANNEL_A.value,
        "turnId": None,
        "sourceSystem": None,
        "sourcePath": None,
        "acquisitionMethod": FactAcquisition.STATED.value,
        "observedAt": _NOW,
        "recordedAt": _NOW,
        "supersedesFactId": None,
        "correlationId": None,
    }
    document.update(overrides)
    return document


class TestCaseFactViewScoping:
    def test_a_pre_deploy_fact_validates_as_a_case_level_fact(self) -> None:
        """Additive means additive: no new key, no new requirement."""
        validated = CaseFactView.model_validate(_stored_fact())
        assert validated.record_scope is None
        assert validated.identity_version is None

    def test_a_scoped_fact_round_trips_its_scope_and_identity_version(self) -> None:
        validated = CaseFactView.model_validate(
            _stored_fact(record_scope="record-7", identity_version=2)
        )
        assert validated.record_scope == "record-7"
        assert validated.identity_version == 2


class TestAcquisitionMethodAdditions:
    def test_associate_edit_and_context_summary_are_members(self) -> None:
        assert FactAcquisition("ASSOCIATE_EDIT") is FactAcquisition.ASSOCIATE_EDIT
        assert FactAcquisition("CONTEXT_SUMMARY") is FactAcquisition.CONTEXT_SUMMARY

    def test_a_stored_fact_with_the_new_methods_validates(self) -> None:
        for method in (FactAcquisition.ASSOCIATE_EDIT, FactAcquisition.CONTEXT_SUMMARY):
            validated = CaseFactView.model_validate(
                _stored_fact(acquisitionMethod=method.value)
            )
            assert validated.acquisitionMethod is method

    def test_the_original_four_are_untouched(self) -> None:
        """The trust ladder existing readers reason over keeps its rungs."""
        for name in ("STATED", "OBSERVED", "DERIVED", "INFERRED"):
            assert FactAcquisition(name).value == name
