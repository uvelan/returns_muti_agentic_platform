"""S1 -- record-scoped case facts (contracts.md §4, DR-11).

The fact log grows two additive fields (`record_scope`, `identity_version`)
and two acquisition methods (`ASSOCIATE_EDIT`, `CONTEXT_SUMMARY`). Everything
here is about the additive property: a fact written before S1 existed must
validate, project and dedupe exactly as it did, while the new scoped path gets
a per-record identity of its own.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from return_platform.operations import case_repository as case_repository_module
from return_platform.operations.case_repository import CaseRepository
from return_platform.operations.models import CaseFactView, FactAcquisition, FactChannel
from return_platform.workflows.return_case_activities import (
    SCOPED_FACT_IDENTITY_VERSION,
    ReturnCaseActivities,
)

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
        """Additive means additive: no new key, no new requirement.

        Extended in phase 1b by `actorId`, which joins the same rule rather
        than being exempted from it: the pinned pre-S1 key set above is
        untouched, and the field it does not contain must read as absent.
        """
        validated = CaseFactView.model_validate(_stored_fact())
        assert validated.record_scope is None
        assert validated.identity_version is None
        assert validated.actorId is None

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


# ---------------------------------------------------------------------------
# The real CaseRepository over in-memory collections
# ---------------------------------------------------------------------------
#
# The methods under test are `append_case_fact`, `append_scoped_case_fact`,
# `latest_case_facts` and `latest_case_facts_scoped` -- all of whose logic is
# in the repository itself, not in Mongo. What the fakes reproduce is the two
# storage semantics that logic leans on: the unique `factId` (a second insert
# under a held id raises `DuplicateKeyError`) and `recordedAt`-ascending reads.


class _FactsCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any], session: Any = None) -> None:
        del session
        if any(held["factId"] == document["factId"] for held in self.documents):
            raise DuplicateKeyError("factId")
        self.documents.append(document)

    def find(self, query: dict[str, Any]) -> "_FactsCursor":
        return _FactsCursor(
            [dict(held) for held in self.documents if held["caseId"] == query["caseId"]]
        )


class _FactsCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, key: str, direction: int) -> "_FactsCursor":
        del direction  # ASCENDING is the only caller
        self._documents.sort(key=lambda held: held[key])
        return self

    def __aiter__(self) -> Any:
        async def _iterate() -> Any:
            for document in self._documents:
                yield document

        return _iterate()


class _CasesCollection:
    """Only what `bump_case_revision` touches: counted, never inspected."""

    def __init__(self) -> None:
        self.bumps = 0

    async def update_one(self, query: Any, update: Any, session: Any = None) -> Any:
        del query, update, session
        self.bumps += 1
        return SimpleNamespace(matched_count=1)


class _Repository(CaseRepository):
    """The shipped repository, its collections and transaction swapped out."""

    def __init__(self) -> None:
        self.case_facts = _FactsCollection()  # type: ignore[assignment]
        self.cases = _CasesCollection()  # type: ignore[assignment]

    async def _in_transaction(self, callback: Any) -> Any:
        return await callback(None)


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> _Repository:
    """A repository whose clock ticks one second per write.

    Real wall time is not monotonic at the resolution two consecutive appends
    run at (Windows ticks in ~15ms), and the projections under test break
    ordering ties on `factId` -- so an uncontrolled clock would make
    "newest wins" pass or fail on id spelling. A ticking clock makes
    `recordedAt` the deciding key, which is the property being asserted.
    """
    ticks = iter(range(1, 1000))
    monkeypatch.setattr(
        case_repository_module,
        "utc_now",
        lambda: _NOW + timedelta(seconds=next(ticks)),
    )
    return _Repository()


def _activities(repository: _Repository) -> ReturnCaseActivities:
    return ReturnCaseActivities(repository=repository, support_service=None)  # type: ignore[arg-type]


def _fact_kwargs(name: str = "tracking_reference", value: Any = "TRK-1") -> dict[str, Any]:
    """One support observation, as a workflow callsite would derive it."""
    return {
        "fact_id": f"{name}-case-1-evt-1",
        "case_id": "case-1",
        "fact_name": name,
        "value": value,
        "agent_id": "return-support",
        "channel": FactChannel.CHANNEL_B,
        "acquisition_method": FactAcquisition.OBSERVED,
        "source_system": "RETURN_SUPPORT",
        "source_path": "SUPPORT_REPLY",
    }


@pytest.mark.asyncio
class TestScopedAppendOnce:
    async def test_the_same_fact_about_two_records_is_two_facts(
        self, repository: _Repository
    ) -> None:
        """Identity includes the scope: two RMAs, same fact name, no shadowing."""
        activities = _activities(repository)
        assert await activities.append_scoped_fact_once(
            record_scope="record-1", **_fact_kwargs(value="TRK-1")
        )
        assert await activities.append_scoped_fact_once(
            record_scope="record-2", **_fact_kwargs(value="TRK-2")
        )

        documents = repository.case_facts.documents
        assert len(documents) == 2
        assert documents[0]["factId"] != documents[1]["factId"]
        assert {held["record_scope"] for held in documents} == {"record-1", "record-2"}
        assert all(
            held["identity_version"] == SCOPED_FACT_IDENTITY_VERSION for held in documents
        )
        # The stored shape is the contract's: every scoped document validates.
        for held in documents:
            assert CaseFactView.model_validate(held).record_scope in {"record-1", "record-2"}

    async def test_a_scoped_retry_is_absorbed_not_duplicated(
        self, repository: _Repository
    ) -> None:
        activities = _activities(repository)
        assert await activities.append_scoped_fact_once(
            record_scope="record-1", **_fact_kwargs()
        )
        assert not await activities.append_scoped_fact_once(
            record_scope="record-1", **_fact_kwargs()
        )
        assert len(repository.case_facts.documents) == 1

    async def test_a_pre_deploy_legacy_fact_retried_through_the_new_path_is_no_dup(
        self, repository: _Repository
    ) -> None:
        """The replay guarantee (contracts.md sect. 4, brief item 2).

        The event was recorded through `_append_fact_once` before this path
        deployed; the retry arrives after, through the scoped path, with no
        record scope. Same derived id, so the unique `factId` absorbs it.
        """
        activities = _activities(repository)
        assert await activities._append_fact_once(**_fact_kwargs())  # noqa: SLF001 - the shipped legacy path is the fixture
        assert not await activities.append_scoped_fact_once(
            record_scope=None, **_fact_kwargs()
        )

        documents = repository.case_facts.documents
        assert len(documents) == 1
        # And untouched means untouched: the legacy document kept its shape.
        assert "record_scope" not in documents[0]
        assert "identity_version" not in documents[0]

    async def test_the_scoped_write_moves_the_case_revision(
        self, repository: _Repository
    ) -> None:
        """Plan sect. 6.5 holds on the new path: child write, same-transaction bump."""
        await _activities(repository).append_scoped_fact_once(
            record_scope="record-1", **_fact_kwargs()
        )
        assert repository.cases.bumps == 1


@pytest.mark.asyncio
class TestLatestCaseFactsScoped:
    async def test_partitions_by_scope_then_name(self, repository: _Repository) -> None:
        activities = _activities(repository)
        await activities._append_fact_once(**_fact_kwargs(value="CASE-LEVEL"))  # noqa: SLF001
        await activities.append_scoped_fact_once(
            record_scope="record-1", **_fact_kwargs(value="TRK-1")
        )
        await activities.append_scoped_fact_once(
            record_scope="record-2", **_fact_kwargs(value="TRK-2")
        )

        latest = await repository.latest_case_facts_scoped("case-1")
        assert latest[(None, "tracking_reference")]["value"] == "CASE-LEVEL"
        assert latest[("record-1", "tracking_reference")]["value"] == "TRK-1"
        assert latest[("record-2", "tracking_reference")]["value"] == "TRK-2"
        assert len(latest) == 3

    async def test_newest_wins_within_a_partition(self, repository: _Repository) -> None:
        activities = _activities(repository)
        first = _fact_kwargs(value="TRK-OLD")
        second = _fact_kwargs(value="TRK-NEW")
        second["fact_id"] = "tracking_reference-case-1-evt-2"
        await activities.append_scoped_fact_once(record_scope="record-1", **first)
        await activities.append_scoped_fact_once(record_scope="record-1", **second)

        latest = await repository.latest_case_facts_scoped("case-1")
        assert latest[("record-1", "tracking_reference")]["value"] == "TRK-NEW"
        assert len(latest) == 1

    async def test_legacy_projection_is_untouched_by_scoped_writes(
        self, repository: _Repository
    ) -> None:
        """`latest_case_facts` keeps its per-name shape; its 11 consumers see
        exactly the mapping they always did (the convergence is a registered
        follow-up, not S1)."""
        activities = _activities(repository)
        await activities._append_fact_once(**_fact_kwargs(value="CASE-LEVEL"))  # noqa: SLF001
        await activities.append_scoped_fact_once(
            record_scope="record-1", **_fact_kwargs(value="TRK-1")
        )

        legacy = await repository.latest_case_facts("case-1")
        assert set(legacy) == {"tracking_reference"}
