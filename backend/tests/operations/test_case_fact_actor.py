"""S1 phase 1b -- the server-stamped `actorId` on the fact plane (contracts §4).

Contracts §4's last sentence says command-originated facts carry a
server-stamped `actorId`. Until this branch they could not: neither the
scoped append parameter nor the stored field existed, so two later slices
each invented a different value-level spelling for the same idea and the
provenance stopped being queryable.

What is asserted here is four guarantees, and each one is written so that
breaking the thing it names makes it fail -- the branch's ledger records the
before/after of injecting each fault deliberately:

1. the field persists and reads back, by exact value, through the projection
   consumers actually call;
2. the default keeps every pre-existing fact document valid, pinned against an
   explicit legacy key set rather than one derived from the code under test;
3. fact **identity** is unaffected by the actor -- two facts differing only by
   actor stay one fact, because `actorId` is provenance, not identity;
4. the legacy `append_case_fact` / `_append_fact_once` / `latest_case_facts`
   path writes and reads exactly what it always did.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from return_platform.operations import case_repository as case_repository_module
from return_platform.operations.case_repository import CaseRepository
from return_platform.operations.models import CaseFactView, FactAcquisition, FactChannel
from return_platform.workflows.return_case_activities import (
    SCOPED_FACT_IDENTITY_VERSION,
    ReturnCaseActivities,
)

_NOW = datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC)

#: The exact stored key set of a fact written before `actorId` existed, written
#: out by hand. Deliberately a literal and not `set(append_case_fact(...))`:
#: a pin derived from the code it pins agrees with that code by construction and
#: would keep agreeing while both drifted together. This one is a second,
#: independent statement of what the document is, and
#: `test_the_legacy_write_still_produces_exactly_the_pinned_key_set` makes the
#: shipped legacy writer answer to it.
LEGACY_STORED_KEYS = frozenset(
    {
        "factId",
        "caseId",
        "factName",
        "value",
        "agentId",
        "channel",
        "turnId",
        "sourceSystem",
        "sourcePath",
        "acquisitionMethod",
        "observedAt",
        "recordedAt",
        "supersedesFactId",
        "correlationId",
    }
)


def _legacy_fact_document(**overrides: Any) -> dict[str, Any]:
    """A fact document exactly as it was stored before this branch."""
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


class TestTheLegacyDocumentStaysValid:
    def test_the_helper_here_really_is_the_pinned_pre_actor_key_set(self) -> None:
        """Guards the guard: if this fixture grows a key, every test below that
        claims to be exercising a pre-`actorId` document silently stops doing so."""
        assert set(_legacy_fact_document()) == LEGACY_STORED_KEYS

    def test_a_pre_actor_document_validates_and_reads_as_no_actor(self) -> None:
        """The default is what makes this additive. Remove it and this fails."""
        validated = CaseFactView.model_validate(_legacy_fact_document())
        assert validated.actorId is None

    def test_no_stored_key_is_required_that_was_not_required_before(self) -> None:
        """A stronger statement than "the happy document validates".

        Every field the model does not default is a key an existing document
        must already carry. If `actorId` shipped without a default it would join
        that set, and this exact-set comparison would fail naming it.
        """
        required = {
            name
            for name, field in CaseFactView.model_fields.items()
            if field.is_required()
        }
        assert required == {
            "factId",
            "caseId",
            "factName",
            "agentId",
            "channel",
            "acquisitionMethod",
            "observedAt",
            "recordedAt",
        }
        assert required <= LEGACY_STORED_KEYS

    def test_the_model_did_not_get_loosened_to_make_room(self) -> None:
        """`extra="forbid"` still bites.

        The cheap way to make an unknown key "work" is to stop forbidding
        unknown keys, which would make every assertion in this file vacuous.
        """
        with pytest.raises(ValidationError):
            CaseFactView.model_validate(
                _legacy_fact_document(somethingNobodyDeclared="x")
            )


class TestTheFieldHoldsTheActor:
    def test_an_actor_stamped_document_round_trips_the_exact_value(self) -> None:
        validated = CaseFactView.model_validate(
            _legacy_fact_document(actorId="principal:branch-associate-4471")
        )
        assert validated.actorId == "principal:branch-associate-4471"

    def test_actor_and_agent_are_two_different_fields(self) -> None:
        """`agentId` is which software wrote the fact; `actorId` is on whose
        authority. A field that merely mirrored `agentId` would carry no new
        provenance at all."""
        validated = CaseFactView.model_validate(
            _legacy_fact_document(
                agentId="return-support-activity",
                actorId="principal:branch-associate-4471",
            )
        )
        assert validated.agentId == "return-support-activity"
        assert validated.actorId == "principal:branch-associate-4471"


# ---------------------------------------------------------------------------
# The real CaseRepository and ReturnCaseActivities over in-memory collections
# ---------------------------------------------------------------------------
#
# Same fakes as the phase-1 scoped-fact suite: a unique `factId` (a second
# insert under a held id raises `DuplicateKeyError`) and `recordedAt`-ascending
# reads are the only two storage semantics the code under test leans on.


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
    """A repository whose clock ticks one second per write, so the projections'
    `(recordedAt, factId)` tie-break is decided by time and not by id spelling."""
    ticks = iter(range(1, 1000))
    monkeypatch.setattr(
        case_repository_module,
        "utc_now",
        lambda: _NOW + timedelta(seconds=next(ticks)),
    )
    return _Repository()


def _activities(repository: _Repository) -> ReturnCaseActivities:
    return ReturnCaseActivities(repository=repository, support_service=None)  # type: ignore[arg-type]


def _fact_kwargs(
    name: str = "support_template_revision", value: Any = "revised"
) -> dict[str, Any]:
    """One command-originated observation, as a workflow callsite derives it."""
    return {
        "fact_id": f"{name}-case-1-evt-1",
        "case_id": "case-1",
        "fact_name": name,
        "value": value,
        "agent_id": "return-support",
        "channel": FactChannel.CHANNEL_B,
        "acquisition_method": FactAcquisition.ASSOCIATE_EDIT,
        "source_system": "RETURN_SUPPORT",
        "source_path": "SUPPORT_REVIEW",
    }


@pytest.mark.asyncio
class TestTheActorSurvivesTheWholeWritePath:
    async def test_the_activity_stamps_the_actor_into_the_stored_document(
        self, repository: _Repository
    ) -> None:
        """The parameter that raised `TypeError` before this branch, now landing
        in a field that exists to hold it."""
        assert await _activities(repository).append_scoped_fact_once(
            record_scope="record-1",
            actor_id="principal:branch-associate-4471",
            **_fact_kwargs(),
        )

        (stored,) = repository.case_facts.documents
        assert stored["actorId"] == "principal:branch-associate-4471"

    async def test_the_stored_actor_reads_back_through_the_scoped_projection(
        self, repository: _Repository
    ) -> None:
        """Written *and readable*: the value comes back out of the projection
        consumers call, at its exact value, and through the typed view."""
        await _activities(repository).append_scoped_fact_once(
            record_scope="record-1",
            actor_id="principal:branch-associate-4471",
            **_fact_kwargs(),
        )

        latest = await repository.latest_case_facts_scoped("case-1")
        held = latest[("record-1", "support_template_revision")]
        assert held["actorId"] == "principal:branch-associate-4471"
        assert (
            CaseFactView.model_validate(held).actorId
            == "principal:branch-associate-4471"
        )

    async def test_a_case_level_command_fact_carries_its_actor_too(
        self, repository: _Repository
    ) -> None:
        """Not every command is about one RMA. Scope `None` is still a command."""
        await _activities(repository).append_scoped_fact_once(
            record_scope=None,
            actor_id="principal:supervisor-9",
            **_fact_kwargs(name="support_clarification_answered", value="yes"),
        )

        latest = await repository.latest_case_facts_scoped("case-1")
        assert (
            latest[(None, "support_clarification_answered")]["actorId"]
            == "principal:supervisor-9"
        )

    async def test_an_observation_with_no_actor_stores_an_explicit_none(
        self, repository: _Repository
    ) -> None:
        """`None` means *not command-originated*, and it is recorded rather than
        left absent -- so a reader can tell "no actor" from "field not written",
        which is the difference between provenance and a gap."""
        await _activities(repository).append_scoped_fact_once(
            record_scope="record-1", **_fact_kwargs()
        )

        (stored,) = repository.case_facts.documents
        assert "actorId" in stored
        assert stored["actorId"] is None
        assert CaseFactView.model_validate(stored).actorId is None

    async def test_two_actors_on_one_record_keep_their_own_actors(
        self, repository: _Repository
    ) -> None:
        """Two distinct facts, two distinct actors -- neither overwritten by the
        other, and neither read back as the other's."""
        activities = _activities(repository)
        first = _fact_kwargs(value="draft-revised")
        second = _fact_kwargs(name="support_template_approval", value="approved")
        await activities.append_scoped_fact_once(
            record_scope="record-1", actor_id="principal:associate-1", **first
        )
        await activities.append_scoped_fact_once(
            record_scope="record-1", actor_id="principal:supervisor-2", **second
        )

        latest = await repository.latest_case_facts_scoped("case-1")
        assert latest[("record-1", "support_template_revision")]["actorId"] == (
            "principal:associate-1"
        )
        assert latest[("record-1", "support_template_approval")]["actorId"] == (
            "principal:supervisor-2"
        )


@pytest.mark.asyncio
class TestTheActorIsNotPartOfIdentity:
    async def test_the_same_fact_from_two_actors_is_still_one_fact(
        self, repository: _Repository
    ) -> None:
        """The load-bearing negative result, asserted positively.

        `actorId` is provenance, not identity. If it entered the `factId`
        derivation, a retry that re-stamped a different principal -- or the same
        event arriving on two authenticated paths -- would become a second fact
        where the log should hold one, and the append-once guarantee would be
        gone. So: same derived id, different actor, second call absorbed, one
        document, and the *first* writer's actor is the one that stands.
        """
        activities = _activities(repository)
        assert await activities.append_scoped_fact_once(
            record_scope="record-1", actor_id="principal:associate-1", **_fact_kwargs()
        )
        assert not await activities.append_scoped_fact_once(
            record_scope="record-1", actor_id="principal:associate-2", **_fact_kwargs()
        )

        (stored,) = repository.case_facts.documents
        assert stored["actorId"] == "principal:associate-1"

    async def test_the_stored_fact_id_is_exactly_the_scoped_derivation(
        self, repository: _Repository
    ) -> None:
        """Pins the derivation itself, by exact value rather than by shape.

        A "does not contain the actor" assertion would pass against a derivation
        that hashed the actor in. This states the whole id, so any extra
        ingredient at all fails it.
        """
        await _activities(repository).append_scoped_fact_once(
            record_scope="record-1",
            actor_id="principal:branch-associate-4471",
            **_fact_kwargs(),
        )

        (stored,) = repository.case_facts.documents
        assert stored["factId"] == "support_template_revision-case-1-evt-1::record-1"
        assert stored["identity_version"] == SCOPED_FACT_IDENTITY_VERSION

    async def test_an_unscoped_actor_fact_keeps_the_legacy_derived_id_verbatim(
        self, repository: _Repository
    ) -> None:
        """The phase-1 replay guarantee, re-asserted with an actor present: an
        event first written by the legacy path and retried through the scoped
        path with an actor stamp must still meet its own `factId`."""
        activities = _activities(repository)
        assert await activities._append_fact_once(**_fact_kwargs())  # noqa: SLF001 - the shipped legacy path is the fixture
        assert not await activities.append_scoped_fact_once(
            record_scope=None, actor_id="principal:associate-1", **_fact_kwargs()
        )

        (stored,) = repository.case_facts.documents
        assert stored["factId"] == "support_template_revision-case-1-evt-1"


@pytest.mark.asyncio
class TestTheLegacyPathIsUntouched:
    async def test_the_legacy_write_still_produces_exactly_the_pinned_key_set(
        self, repository: _Repository
    ) -> None:
        """The shipped `append_case_fact` answers to the hand-written pin.

        This is the assertion that catches "threaded the field through by
        editing the legacy writer": it is an exact set equality against a
        literal declared at the top of this file, so an added key fails it and
        names itself in the diff.
        """
        await _activities(repository)._append_fact_once(**_fact_kwargs())  # noqa: SLF001

        (stored,) = repository.case_facts.documents
        assert set(stored) == LEGACY_STORED_KEYS

    async def test_the_legacy_writer_rejects_an_actor_and_that_is_correct(
        self, repository: _Repository
    ) -> None:
        """`actor_id` is not a legacy parameter and must not become one.

        The legacy path is byte-untouched by construction here, and the way to
        keep it that way is to make the attempt to use it fail loudly rather
        than quietly do nothing.
        """
        with pytest.raises(TypeError):
            await repository.append_case_fact(
                actor_id="principal:associate-1", **_fact_kwargs()
            )

    async def test_the_legacy_projection_still_returns_the_documents_it_always_did(
        self, repository: _Repository
    ) -> None:
        activities = _activities(repository)
        await activities._append_fact_once(**_fact_kwargs())  # noqa: SLF001

        legacy = await repository.latest_case_facts("case-1")
        assert set(legacy) == {"support_template_revision"}
        assert set(legacy["support_template_revision"]) == LEGACY_STORED_KEYS
