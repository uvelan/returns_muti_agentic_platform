"""V1: the panel and the review endpoints (contracts.md sect. 9).

Driven through a real `TestClient` over the shipped routers, with the real
`ReviewAggregateStore` behind them on the Mongo double -- so a 409 is the
store's answer travelling through the handler rather than a raise the test
arranged.

The guarantees this file is here to prove, each with the fault injected in the
ledger:

* **409 on a stale CAS**, carrying the *transition* so the UI can say what
  happened rather than "conflict";
* **two principals, one case → identical body and identical ETag**, which is
  what makes the shared payload principal-independent rather than merely
  claimed to be;
* **304 on a matching `If-None-Match`**, with composition having run anyway;
* **the cache headers**, both of them, on the two surfaces that need different
  ones -- `private, no-cache` on the shared panel and `private, no-store` on
  one actor's edit row;
* **capability and case access**, including that a case in another tenant is a
  404 rather than a 403.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from temporalio.converter import value_to_type

from return_platform.api.case_panel import router as panel_router
from return_platform.api.case_reviews import router as reviews_router
from return_platform.configuration.settings import Settings
from return_platform.operations.case_commands import (
    CASE_COMMAND_RECORDS,
    CaseCommandKind,
    DurableCaseCommandStore,
    ensure_case_command_indexes,
)
from return_platform.operations.case_panel import (
    CasePanelView,
    PanelSectionView,
    clear_panel_sections,
    register_panel_section,
)
from return_platform.operations.review_aggregate import (
    REVIEW_DRAFT_EDITS,
    ReviewAggregateStore,
    ReviewKind,
    ReviewState,
    canonical_review_payload,
    ensure_review_indexes,
)
from return_platform.operations.support_events import canonical_payload_digest
from return_platform.resources import RuntimeResources
from return_platform.security import roles as r
from return_platform.security.capabilities import (
    RETURNS_REVIEW_RECOVERY,
    capabilities_for_roles,
)
from return_platform.security.principal import Principal
from return_platform.workflows.return_case_workflow import TemplateReviewNotice
from tests.operations.mongo_double import FakeClient

CASE_ID = "case-panel-1"
REQUEST_ID = "support:case-panel-1"
REVIEW_ID = "review-panel-1"
TENANT = "tenant-a"

DRAFT: dict[str, Any] = {
    "template_id": "t",
    "variant_id": "default",
    "subject": "Return 9100",
    "text": "stale",
    "sections": [
        {
            "section_id": "order",
            "title": "ORDER",
            "return_record_id": None,
            "fields": [
                {
                    "field_id": "order_number",
                    "label": "Order Number",
                    "value": "SO-1",
                    "source": "case_fact",
                    "source_path": "confirmed_order_reference",
                    "fact_id": "fact-1",
                    "applied_fallback": False,
                }
            ],
        }
    ],
    "gaps": [],
}


#: The case and its RMA, written to the collections the **real**
#: `OperationalRepository` reads. Nothing here is a double: the case-access
#: check and the panel's record projection both go through the shipped
#: repository, so a tenancy answer is the repository's answer.
#:
#: This is the correction the first draft of this file needed. It attached a
#: hand-written `_Repository` to `app.state.operational_repository` and a
#: `_Resources` stand-in to `app.state.resources` -- neither of which either
#: production path consults. `resolve_operational_repository` wants a real
#: `RuntimeResources` *and* `app.state.settings`, and answered 503 to every one
#: of them. Twenty-two of this file's twenty-eight tests were red, and the six
#: that passed were the ones that never made a request.
async def _seed_case(mongo: FakeClient, settings: Settings, *, tenant_id: str = TENANT) -> None:
    database = mongo[settings.mongo_database]
    await database["cases"].insert_one(
        {
            "_id": CASE_ID,
            "caseId": CASE_ID,
            "tenantId": tenant_id,
            "principalId": "associate-a",
            "status": "AWAITING_SUPPORT",
        }
    )
    await database["return_records"].insert_one(
        {
            "_id": "rec-1",
            "caseId": CASE_ID,
            "returnRecordId": "rec-1",
            "returnReference": "RMA-9100",
            "status": "OPEN",
            "returnMethod": "PARCEL",
            "createdAt": datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
            # Moves on writes the panel does not care about. Its absence from
            # the projection is asserted by
            # `test_the_record_projection_carries_nothing_that_ticks`.
            "updatedAt": datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
        }
    )


@pytest.fixture(autouse=True)
def _no_registered_sections() -> Iterator[None]:
    """The section registry is process-global. Emptied around every test so one
    test's contributor cannot change another's ETag."""
    clear_panel_sections()
    yield
    clear_panel_sections()


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest_asyncio.fixture
async def store(mongo: FakeClient, test_settings: Settings) -> ReviewAggregateStore:
    database = mongo[test_settings.mongo_database]
    await ensure_review_indexes(database)
    await ensure_case_command_indexes(cast(Any, database))
    await _seed_case(mongo, test_settings)
    reviews = ReviewAggregateStore(
        cast(Any, mongo),
        test_settings,
        command_store=DurableCaseCommandStore(cast(Any, mongo), test_settings),
    )
    await reviews.create_review(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        review_kind=ReviewKind.TEMPLATE,
        draft_payload=DRAFT,
        review_id=REVIEW_ID,
    )
    return reviews


def _client(
    mongo: FakeClient,
    test_settings: Settings,
    *,
    subject: str = "associate-a",
    roles: frozenset[str] = frozenset({r.RETURN_SUPPORT}),
    tenant_id: str = TENANT,
) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject=subject, roles=roles)
        request.state.tenant_id = tenant_id
        request.state.correlation_id = "corr-1"
        return await call_next(request)

    app.state.resources = RuntimeResources(
        settings=test_settings,
        # No governance catalog. Neither the panel nor any review endpoint
        # reads one, and loading a real one here would imply that one of them
        # does -- `resolve_operational_repository` and `panel_dependencies` want
        # `settings` and `mongo`, and those are real.
        catalog=cast(Any, None),
        mongo=cast(Any, mongo),
        #: No Temporal in this process. The panel must still compose, with the
        #: execution section degraded -- which is the property, not a shortcut.
        temporal=None,
    )
    app.state.settings = test_settings
    app.include_router(panel_router)
    app.include_router(reviews_router)
    return TestClient(app)


def _approve_body(review: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = {
        "draft_version": int(review["draftVersion"]),
        "canonical_edit_version": int(review["canonicalEditVersion"]),
        "canonical_approved_payload_hash": canonical_payload_digest(
            canonical_review_payload(review)
        ),
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# The panel
# --------------------------------------------------------------------------- #


def test_the_panel_carries_the_open_review(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    with _client(mongo, test_settings) as client:
        answer = client.get(f"/api/v1/cases/{CASE_ID}/panel")

    assert answer.status_code == 200
    view = CasePanelView.model_validate(answer.json()["data"])
    assert [review.review_id for review in view.reviews] == [REVIEW_ID]
    assert view.reviews[0].state == ReviewState.OPEN.value
    assert view.reviews[0].conflict_present is False


def test_the_panel_degrades_the_execution_rather_than_failing(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """No Temporal in this process. The reviews are read from Mongo and are
    still true, so the panel renders and says which part it could not read."""
    with _client(mongo, test_settings) as client:
        view = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )

    assert view.execution.status == "degraded"
    assert view.execution.reason == "EXECUTION_NOT_AVAILABLE"
    assert view.reviews, "a degraded execution must not empty the panel"
    assert view.timers.template_review_deadline_iso is None, (
        "a deadline the panel invented would be a countdown to nothing"
    )


def test_the_cache_headers_are_the_ones_the_contract_names(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    with _client(mongo, test_settings) as client:
        answer = client.get(f"/api/v1/cases/{CASE_ID}/panel")

    assert answer.headers["Cache-Control"] == "private, no-cache"
    assert answer.headers["Vary"] == "Authorization"
    assert answer.headers["ETag"].startswith('"')


def test_a_matching_etag_answers_304(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    with _client(mongo, test_settings) as client:
        first = client.get(f"/api/v1/cases/{CASE_ID}/panel")
        second = client.get(
            f"/api/v1/cases/{CASE_ID}/panel",
            headers={"If-None-Match": first.headers["ETag"]},
        )

    assert second.status_code == 304
    assert second.headers["ETag"] == first.headers["ETag"]
    assert second.headers["Cache-Control"] == "private, no-cache"
    assert second.content == b""


def test_the_etag_moves_when_the_review_does(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The other half of the 304, and the one that matters more.

    An ETag that never changed would give a permanently-cached panel and an
    associate watching a review that has already been sent. Asserted against a
    real transition rather than a poke at the model.
    """
    with _client(mongo, test_settings) as client:
        before = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]
        client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/cancel",
            json={"reason": "changed my mind"},
        )
        after = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]

    assert before != after


def test_two_principals_get_identical_bodies_and_identical_etags(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Contracts.md sect. 9, and the property a shared cache rests on.

    Byte-for-byte on the `data` half. `meta` carries the correlation id, which
    is per-request by design and is not what the ETag is computed over -- so
    the comparison is on the payload the hash covers.
    """
    with _client(mongo, test_settings, subject="associate-a") as first_client:
        first = first_client.get(f"/api/v1/cases/{CASE_ID}/panel")
    with _client(mongo, test_settings, subject="associate-b") as second_client:
        second = second_client.get(f"/api/v1/cases/{CASE_ID}/panel")

    assert first.json()["data"] == second.json()["data"]
    assert first.headers["ETag"] == second.headers["ETag"]


def test_a_private_edit_never_reaches_the_shared_panel(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """One actor's unfinished draft is not the case's business.

    Autosaving must change neither the shared body nor its ETag: it is one
    person's thinking, and a hash that moved on it would also tell every other
    viewer that somebody is typing.
    """
    with _client(mongo, test_settings, subject="associate-a") as client:
        before = client.get(f"/api/v1/cases/{CASE_ID}/panel")
        saved = client.put(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state",
            json={
                "client_edit_id": "c-1",
                "base_draft_version": 1,
                "payload": {**DRAFT, "subject": "my private wording"},
            },
        )
        after = client.get(f"/api/v1/cases/{CASE_ID}/panel")

    assert saved.status_code == 200
    assert "my private wording" not in after.text
    assert before.headers["ETag"] == after.headers["ETag"]
    assert before.json()["data"] == after.json()["data"]


def test_a_second_actors_edit_shows_as_a_conflict_without_showing_its_contents(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The marker is shared; the contents never are (contracts.md sect. 6)."""
    with _client(mongo, test_settings, subject="associate-a") as first_client:
        first_client.put(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state",
            json={"client_edit_id": "a-1", "base_draft_version": 1, "payload": DRAFT},
        )
    with _client(mongo, test_settings, subject="associate-b") as second_client:
        second_client.put(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state",
            json={
                "client_edit_id": "b-1",
                "base_draft_version": 1,
                "payload": {**DRAFT, "subject": "B's private wording"},
            },
        )
        view = CasePanelView.model_validate(
            second_client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )

    assert view.reviews[0].conflict_present is True
    assert "B's private wording" not in str(view.model_dump())


def test_the_record_projection_carries_nothing_that_ticks(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The case's RMAs reach the panel, and `updatedAt` does not.

    Both halves matter. The records have to be there -- contracts.md sect. 9
    puts the record projection in the shared body and the review sections are
    grouped by it. And the projection has to be narrow: `updatedAt` moves on
    writes the panel does not render, so carrying it would invalidate every
    cached panel on the estate for a change nobody could see.
    """
    with _client(mongo, test_settings) as client:
        answer = client.get(f"/api/v1/cases/{CASE_ID}/panel")

    view = CasePanelView.model_validate(answer.json()["data"])
    assert view.return_records == (
        {
            "return_record_id": "rec-1",
            "return_reference": "RMA-9100",
            "status": "OPEN",
            "return_method": "PARCEL",
        },
    )


@pytest.mark.asyncio
async def test_a_record_write_the_panel_does_not_render_does_not_move_the_etag(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The other half of the projection's narrowness, driven by a real write.

    A record whose `updatedAt` moves is the ordinary case -- an item is added, a
    shipment is booked -- and the panel that renders none of that must not
    invalidate. Asserted against an actual `update_one`, not against the shape
    of the dict.
    """
    with _client(mongo, test_settings) as client:
        before = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]
        await mongo[test_settings.mongo_database]["return_records"].update_one(
            {"_id": "rec-1"},
            {"$set": {"updatedAt": datetime(2027, 1, 1, tzinfo=UTC)}},
        )
        unchanged = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]

        # And the sensitivity in the other direction, so "unchanged" is not the
        # answer a hash over nothing would also give.
        await mongo[test_settings.mongo_database]["return_records"].update_one(
            {"_id": "rec-1"}, {"$set": {"returnReference": "RMA-9101"}}
        )
        moved = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]

    assert before == unchanged
    assert before != moved


def test_the_etag_survives_a_clock_tick(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Absolute instants only (contracts.md sect. 9), proved by injection.

    Two composes of an unchanged case agree. On its own that proves very little
    -- a hash over an empty payload agrees too -- so the injection is a
    contributed section that carries a *countdown*, which is exactly the field
    the contract forbids the panel to compute. With it registered the ETag moves
    between two composes of the same case, which is the failure a
    `seconds_remaining` on the timers would cause estate-wide.
    """
    ticks = iter(range(1_000))

    async def counting(_context: Any) -> PanelSectionView:
        return PanelSectionView(section_id="tick", payload={"seconds": next(ticks)})

    with _client(mongo, test_settings) as client:
        stable_first = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]
        stable_second = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]
        register_panel_section("tick", counting)
        ticking_first = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]
        ticking_second = client.get(f"/api/v1/cases/{CASE_ID}/panel").headers["ETag"]

    assert stable_first == stable_second
    assert ticking_first != ticking_second, (
        "the hash must be sensitive to a countdown -- otherwise the stability "
        "above is a hash that sees nothing"
    )


# --------------------------------------------------------------------------- #
# The section registry seam
# --------------------------------------------------------------------------- #


def test_a_contributed_section_reaches_the_panel(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The seam V2 and V3 consume. V1 never touches `CasePanelView` again."""
    seen: list[str] = []

    async def contribute(context: Any) -> PanelSectionView:
        seen.append(str(context["case_id"]))
        return PanelSectionView(section_id="ingress", payload={"parked": 2})

    register_panel_section("ingress", contribute)
    with _client(mongo, test_settings) as client:
        view = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )

    assert seen == [CASE_ID]
    assert [section.section_id for section in view.sections] == ["ingress"]
    assert view.sections[0].payload == {"parked": 2}


def test_sections_are_ordered_by_id_not_by_registration(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Registration order is import order, and an ETag that changed when a
    module moved would be a cache that misses for a reason nobody can see."""

    async def one(_context: Any) -> PanelSectionView:
        return PanelSectionView(section_id="zulu")

    async def two(_context: Any) -> PanelSectionView:
        return PanelSectionView(section_id="alpha")

    register_panel_section("zulu", one)
    register_panel_section("alpha", two)
    with _client(mongo, test_settings) as client:
        view = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )

    assert [section.section_id for section in view.sections] == ["alpha", "zulu"]


def test_a_failing_contributor_degrades_its_own_section_only(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Somebody else's slice must not be able to blank the screen an associate
    is blocked on."""

    async def broken(_context: Any) -> PanelSectionView:
        raise RuntimeError("V2 is having a bad day")

    register_panel_section("relay", broken)
    with _client(mongo, test_settings) as client:
        answer = client.get(f"/api/v1/cases/{CASE_ID}/panel")

    assert answer.status_code == 200
    view = CasePanelView.model_validate(answer.json()["data"])
    assert view.sections[0].status == "degraded"
    assert view.reviews, "the reviews are still there"


def test_registering_one_id_twice_is_refused() -> None:
    """Two contributors for one id would race, and which won would depend on
    import order."""

    async def contribute(_context: Any) -> PanelSectionView:
        return PanelSectionView(section_id="dup")

    register_panel_section("dup", contribute)
    with pytest.raises(ValueError, match="already registered"):
        register_panel_section("dup", contribute)


# --------------------------------------------------------------------------- #
# The mutations
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_approving_the_current_draft_moves_the_review(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    review = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json=_approve_body(review),
        )

    assert answer.status_code == 200
    assert answer.json()["data"]["state"] == ReviewState.APPROVING.value
    assert answer.json()["data"]["signal_id"]


@pytest.mark.asyncio
async def test_a_stale_draft_version_is_409_with_the_field_named(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The CAS, and the reason it is not optional.

    An associate approves the bytes they read. A draft re-rendered under them
    must fail rather than silently go out, and the 409 has to say *which*
    version moved or the UI can only offer "try again".
    """
    review = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json=_approve_body(review, draft_version=99),
        )

    assert answer.status_code == 409
    detail = answer.json()["detail"]
    assert detail["code"] == "ReviewVersionMismatchError"
    assert detail["field"] == "draft_version"
    assert detail["expected"] == 99


@pytest.mark.asyncio
async def test_a_wrong_payload_hash_is_409(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Approving bytes that are not the store's canonical payload."""
    review = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json=_approve_body(review, canonical_approved_payload_hash="0" * 64),
        )

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "ApprovedPayloadHashMismatchError"


@pytest.mark.asyncio
async def test_approving_twice_surfaces_the_transition(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Contracts.md sect. 6: the UI surfaces *the transition*, not an error.

    "This review is already approving" is actionable; "409 Conflict" is not, and
    an associate who sees the second thing presses the button again.
    """
    review = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    body = _approve_body(review)
    with _client(mongo, test_settings) as client:
        client.post(f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve", json=body)
        second = client.post(f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve", json=body)

    assert second.status_code == 409
    assert second.json()["detail"]["state"] == ReviewState.APPROVING.value


@pytest.mark.asyncio
async def test_approving_with_a_torn_conflict_is_refused_at_the_endpoint(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Carry-forward conditions 5a and 8, at the surface that meets them.

    Two edit rows, no flag -- the process died in `_after_edit_written`'s
    insert-to-flag window. The gate's recompute catches it and the endpoint
    answers 409 with the conflict code, so the panel offers Resolve exactly as
    it does for a flagged conflict.
    """
    edits = mongo[test_settings.mongo_database][REVIEW_DRAFT_EDITS]
    for index, actor in enumerate(("associate-a", "associate-b"), start=1):
        await edits.insert_one(
            {
                "_id": f"edit-{index}",
                "caseId": CASE_ID,
                "reviewId": REVIEW_ID,
                "actorId": actor,
                "editVersion": 1,
                "baseDraftVersion": 1,
                "clientEditId": f"c-{index}",
                "payload": {"subject": f"{actor} wrote this"},
            }
        )
    review = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    assert review["conflictPresent"] is False, "the torn state is the flag being clean"

    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json=_approve_body(review),
        )

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "ReviewConflictError"
    after = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    assert ReviewState(str(after["state"])) is ReviewState.OPEN


def test_revising_then_approving_is_refused(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """A pending revision holds approval until the re-render lands -- otherwise
    the associate would approve a draft they have just asked to be replaced."""
    with _client(mongo, test_settings) as client:
        client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/revise", json={"note": "add the RMA"}
        )
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json={
                "draft_version": 1,
                "canonical_edit_version": 0,
                "canonical_approved_payload_hash": canonical_payload_digest(DRAFT),
            },
        )

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "PendingRevisionError"


def test_a_missing_review_is_404_not_409(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    with _client(mongo, test_settings) as client:
        answer = client.post(f"/api/v1/cases/{CASE_ID}/reviews/nope/cancel", json={"reason": "x"})

    assert answer.status_code == 404
    assert answer.json()["detail"]["code"] == "REVIEW_NOT_FOUND"


# --------------------------------------------------------------------------- #
# Telling the execution: the half that was missing
# --------------------------------------------------------------------------- #


async def _commands(mongo: FakeClient, settings: Settings) -> list[dict[str, Any]]:
    collection = mongo[settings.mongo_database][CASE_COMMAND_RECORDS]
    return [dict(document) async for document in collection.find({"caseId": CASE_ID})]


@pytest.mark.asyncio
async def test_cancelling_records_the_command_that_ends_the_wait(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The store move alone leaves the execution waiting on a dead review.

    `ReviewAggregateStore.cancel` records no command -- only `approve` and
    `retry_delivery` do, because those need one inside their own transaction.
    So without this, a review a person cancelled stays `OPEN` in the workflow's
    wait map and the case parks `TEMPLATE_REVIEW_UNANSWERED` at its deadline
    for a decision made much earlier. Asserted on the *command record*, which
    is the only thing that ever reaches the execution.
    """
    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/cancel",
            json={"reason": "support answered another way"},
        )

    assert answer.status_code == 200
    recorded = await _commands(mongo, test_settings)
    assert [command["kind"] for command in recorded] == [CaseCommandKind.TEMPLATE_CANCELLED.value]
    payload = recorded[0]["payload"]
    assert payload["review_id"] == REVIEW_ID
    assert payload["scope_id"] == REQUEST_ID
    assert recorded[0]["actorId"] == "associate-a", "server-stamped, never a body field"
    assert answer.json()["data"]["signal_id"] == payload["signal_id"]


@pytest.mark.asyncio
async def test_cancelling_twice_records_one_command_and_answers_the_same_thing(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The state move and the command are two transactions, so a client that
    lost the first answer must be able to ask again without a 409 over its own
    success -- and without minting a second signal for one decision."""
    with _client(mongo, test_settings) as client:
        first = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/cancel", json={"reason": "x"}
        )
        second = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/cancel", json={"reason": "x"}
        )

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["data"]["signal_id"] == second.json()["data"]["signal_id"]
    assert second.json()["data"]["duplicate"] is True
    assert len(await _commands(mongo, test_settings)) == 1


@pytest.mark.asyncio
async def test_revising_records_the_command_that_carries_the_note(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """`pendingRevision` blocks approval; only the command produces the draft it
    is waiting for. Without it the review is permanently unapprovable.

    The note travels here and nowhere else -- `record_template_revision` already
    took a `note` and neutralises it onto the fact log, and until this command
    existed it was being handed `None` forever.
    """
    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/revise",
            json={"note": "the RMA is missing"},
        )

    assert answer.status_code == 200
    recorded = await _commands(mongo, test_settings)
    assert [command["kind"] for command in recorded] == [CaseCommandKind.TEMPLATE_REVISED.value]
    assert recorded[0]["payload"]["note"] == "the RMA is missing"
    assert recorded[0]["payload"]["draft_version"] == 1
    assert recorded[0]["payload"]["supersedes"] is None


@pytest.mark.asyncio
async def test_a_redraft_names_the_attempt_it_replaces(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """`supersedes`, and why a redraft is unreachable without it.

    Redraft cancels one attempt and mints another with a **new** `review_id`.
    The workflow's wait map still holds the old one, so a revision notice naming
    only the new id is discarded as "not this case's attempt" and the request
    sits unanswerable behind a review that has already been cancelled. The
    end-to-end proof is in `tests/test_support_template_review_gate.py`; this is
    the producer's half.
    """
    with _client(mongo, test_settings) as client:
        answer = client.post(f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/template-review/redraft")

    assert answer.status_code == 200
    new_id = answer.json()["data"]["review_id"]
    assert new_id != REVIEW_ID, "a redraft is a new attempt, not the same one"
    revisions = [
        command
        for command in await _commands(mongo, test_settings)
        if command["kind"] == CaseCommandKind.TEMPLATE_REVISED.value
    ]
    assert len(revisions) == 1
    assert revisions[0]["payload"]["review_id"] == new_id
    assert revisions[0]["payload"]["supersedes"] == REVIEW_ID
    assert revisions[0]["payload"]["request_id"] == REQUEST_ID


@pytest.mark.asyncio
async def test_the_approval_command_decodes_into_the_notice_the_workflow_takes(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """**The signal payload and the signal handler have to agree.**

    Nothing else in either suite asks this question: the workflow tests hand
    `template_approved` a `TemplateReviewNotice` they built themselves, and the
    aggregate's tests assert on the stored command document. Between them sits
    `handle.signal(name, payload_dict)`, where Temporal decodes the dict into
    the handler's parameter type -- and a required field the producer does not
    send makes that decode fail *at the worker*, silently, with the command
    sitting in the outbox and the case waiting to its deadline for an approval
    somebody already gave.

    `approve` builds its payload from contracts.md sect. 7's required pair
    (`review_id`, `scope_id`) and does not carry `request_id` or `actor`, which
    is why neither is required on the notice and why the workflow routes on the
    review id through its own map instead.
    """
    review = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json=_approve_body(review),
        )
    assert answer.status_code == 200

    recorded = await _commands(mongo, test_settings)
    outbound = [
        command
        for command in recorded
        if command["kind"] == CaseCommandKind.TEMPLATE_APPROVED.value
    ]
    assert len(outbound) == 1
    # The bytes the dispatcher actually signals with -- `payload["signal"]` in
    # the outbox row is this same dict.
    notice = value_to_type(TemplateReviewNotice, outbound[0]["payload"])
    assert notice.review_id == REVIEW_ID
    assert notice.signal_id == answer.json()["data"]["signal_id"]
    assert notice.scope_id == REQUEST_ID


def test_the_notice_really_would_refuse_a_missing_required_field() -> None:
    """The other half, so the decode test above is not vacuous.

    If `value_to_type` filled every absent field with a default regardless, the
    test above would pass against a notice that required `request_id` -- which
    is the shape that was actually broken. It does not: a field with no default
    and no value in the payload is refused.
    """

    @dataclass(frozen=True, slots=True)
    class _Demanding:
        review_id: str
        request_id: str

    with pytest.raises(Exception):  # noqa: B017 - the type is temporalio's own
        value_to_type(_Demanding, {"review_id": "r1"})

    # And extra keys are tolerated, which is why the notice may carry fields
    # `approve`'s payload never mentions.
    assert value_to_type(_Demanding, {"review_id": "r", "request_id": "q", "extra": 1}) == (
        _Demanding(review_id="r", request_id="q")
    )


# --------------------------------------------------------------------------- #
# Condition 7 at the write surface
# --------------------------------------------------------------------------- #


def test_an_autosaved_field_edit_is_neutralised_before_it_is_stored(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Carry-forward condition 7, at write time rather than at send time.

    Neutralising here rather than only on the way out is what makes the
    re-render honest: what the associate sees after their save is what Support
    will receive, and the diff against the agent's draft says the truth.
    """
    payload = {
        **DRAFT,
        "sections": [
            {
                **DRAFT["sections"][0],
                "fields": [{**DRAFT["sections"][0]["fields"][0], "value": "BAY ASSIGNMENT:"}],
            }
        ],
    }
    with _client(mongo, test_settings) as client:
        answer = client.put(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state",
            json={"client_edit_id": "c-1", "base_draft_version": 1, "payload": payload},
        )

    stored = answer.json()["data"]["payload"]
    assert stored["sections"][0]["fields"][0]["value"] == "[removed]"
    assert "BAY ASSIGNMENT:" not in answer.text


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #


async def _failed_delivery(
    store: ReviewAggregateStore, mongo: FakeClient, settings: Settings
) -> dict[str, Any]:
    """Approve, then fail the send -- the state the recovery surface exists for."""
    review = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    with _client(mongo, settings) as client:
        assert (
            client.post(
                f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
                json=_approve_body(review),
            ).status_code
            == 200
        )
    return await store.mark_delivery_failed(
        case_id=CASE_ID, review_id=REVIEW_ID, error_code="SUPPORT_UNREACHABLE"
    )


@pytest.mark.asyncio
async def test_a_retry_reuses_the_delivery_identity_it_was_given(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """A redelivery of one message, never a second message that says the same.

    The stored `logical_operation_id` and `delivery_id` ride the retry command
    verbatim, which is what makes the receiver's dedupe able to absorb it -- and
    an absorbed retry still reaches `SENT` (contracts.md sect. 7; the delivery
    half is proved against the thread ops in
    `tests/operations/test_support_template_gate.py`). Asserted by comparing the
    command's identity against the **review's own frozen identity**, not against
    a value this test chose.
    """
    failed = await _failed_delivery(store, mongo, test_settings)
    assert failed["deliveryId"], "approval freezes the delivery identity"

    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/recovery/retry",
            json={"reason": "support came back up"},
        )

    assert answer.status_code == 200
    assert answer.json()["data"]["state"] == ReviewState.APPROVING.value
    retries = [
        command
        for command in await _commands(mongo, test_settings)
        if command["kind"] == CaseCommandKind.REVIEW_DELIVERY_RETRY.value
    ]
    assert len(retries) == 1
    assert retries[0]["payload"]["delivery_id"] == failed["deliveryId"]
    assert retries[0]["payload"]["logical_operation_id"] == failed["logicalOperationId"]


@pytest.mark.asyncio
async def test_a_failed_delivery_shows_its_recovery_status_on_the_panel(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """A review the associate can no longer edit is the one they most need to
    see (contracts.md sect. 9)."""
    await _failed_delivery(store, mongo, test_settings)
    with _client(mongo, test_settings) as client:
        view = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )

    assert view.reviews[0].state == ReviewState.DELIVERY_FAILED.value
    assert view.reviews[0].recovery_status == ReviewState.DELIVERY_FAILED.value
    assert view.reviews[0].last_delivery_error_code == "SUPPORT_UNREACHABLE"
    assert view.reviews[0].approved_by == "associate-a"


@pytest.mark.asyncio
async def test_an_abandoned_review_stays_visible_with_its_audit(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Terminal, audited, panel-visible -- all three (contracts.md sect. 6)."""
    await _failed_delivery(store, mongo, test_settings)
    with _client(mongo, test_settings) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/recovery/abandon",
            json={"reason": "support resolved it on the phone"},
        )
        view = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )

    assert answer.status_code == 200
    assert view.reviews[0].state == ReviewState.ABANDONED.value
    assert view.reviews[0].abandon_audit == {
        "actor_id": "associate-a",
        "reason": "support resolved it on the phone",
        "at_iso": view.reviews[0].abandon_audit["at_iso"],  # type: ignore[index]
    }
    assert view.reviews[0].abandon_audit["at_iso"], "an unstamped audit is not audit"  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Editing, the rest of it
# --------------------------------------------------------------------------- #


def test_an_edit_against_a_draft_that_moved_is_409_not_a_merge(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Silently rebasing somebody's words onto different facts is how a message
    comes to say something nobody wrote."""
    with _client(mongo, test_settings) as client:
        answer = client.put(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state",
            json={"client_edit_id": "c-1", "base_draft_version": 99, "payload": DRAFT},
        )

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "ReviewVersionMismatchError"


@pytest.mark.asyncio
async def test_resolving_a_conflict_clears_it_and_lets_the_approval_through(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The whole conflict path, end to end, through the endpoints only.

    Two actors edit, the panel shows the conflict, approval is refused, the
    resolution lands, the panel shows it cleared and approval succeeds. Each
    step is asserted -- a test that only checked the last one would pass against
    a conflict marker that was never set.
    """
    with _client(mongo, test_settings, subject="associate-a") as first:
        first.put(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state",
            json={"client_edit_id": "a-1", "base_draft_version": 1, "payload": DRAFT},
        )
    with _client(mongo, test_settings, subject="associate-b") as second:
        second.put(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state",
            json={"client_edit_id": "b-1", "base_draft_version": 1, "payload": DRAFT},
        )
        conflicted = CasePanelView.model_validate(
            second.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )
        assert conflicted.reviews[0].conflict_present is True

        refused = second.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json={
                "draft_version": conflicted.reviews[0].draft_version,
                "canonical_edit_version": conflicted.reviews[0].canonical_edit_version,
                "canonical_approved_payload_hash": "0" * 64,
            },
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "ReviewConflictError"

        resolved = second.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state/resolve",
            json={
                "canonical_payload": {**DRAFT, "subject": "agreed wording"},
                "resolved_from_actor_edit_ids": [],
            },
        )
        assert resolved.status_code == 200

        cleared = CasePanelView.model_validate(
            second.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )
        assert cleared.reviews[0].conflict_present is False

    # The approval hash is over the **store's** canonical payload, so it is read
    # from the store rather than rebuilt here -- rebuilding it would compare two
    # things that are equal by construction and would not notice the canonical
    # edit failing to reach the payload at all.
    settled = await store.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    assert settled["canonicalEditVersion"] > 0, "the resolution moved the canonical version"

    with _client(mongo, test_settings, subject="associate-b") as second:
        approved = second.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json=_approve_body(settled),
        )

    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["state"] == ReviewState.APPROVING.value


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #


def test_reading_the_panel_needs_the_read_capability(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    with _client(mongo, test_settings, roles=frozenset({"nobody"})) as client:
        assert client.get(f"/api/v1/cases/{CASE_ID}/panel").status_code == 403


def test_a_reader_may_not_approve(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """`RETURNS_SESSION_READ` looks; `RETURNS_SUPPORT_ACT` decides."""
    with _client(mongo, test_settings, roles=frozenset({r.CONSOLE_VIEWER})) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json={
                "draft_version": 1,
                "canonical_edit_version": 0,
                "canonical_approved_payload_hash": "0" * 64,
            },
        )

    assert answer.status_code == 403


def test_support_act_alone_does_not_grant_recovery(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The new capability earns its existence here.

    `RETURN_ASSOCIATE` may work a return and may not abandon a message the
    platform already committed to sending. If this ever passes, the capability
    has quietly become an alias for `RETURNS_SUPPORT_ACT`.
    """
    assert RETURNS_REVIEW_RECOVERY not in capabilities_for_roles(frozenset({r.RETURN_ASSOCIATE}))
    with _client(mongo, test_settings, roles=frozenset({r.RETURN_ASSOCIATE})) as client:
        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/recovery/abandon",
            json={"reason": "not going out"},
        )

    assert answer.status_code == 403


def test_the_support_desk_holds_the_recovery_capability() -> None:
    """The other half: a capability nobody holds is a route nobody can use."""
    assert RETURNS_REVIEW_RECOVERY in capabilities_for_roles(frozenset({r.RETURN_SUPPORT}))
    assert RETURNS_REVIEW_RECOVERY in capabilities_for_roles(frozenset({r.CONSOLE_ADMIN}))


def test_a_service_account_may_not_abandon() -> None:
    """Abandonment is an audited human judgement; a service account answering
    for one would be an unattributed close."""
    assert RETURNS_REVIEW_RECOVERY not in capabilities_for_roles(
        frozenset({r.RETURN_PLATFORM_SERVICE})
    )


def test_another_tenants_case_is_404_not_403(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """Absent, not forbidden. A 403 on a guessed id confirms it exists.

    The caller's tenant is the one that moves, not the case's: the case is the
    seeded one and the principal is somebody else's, which is the direction an
    enumerating attacker actually comes from.
    """
    with _client(mongo, test_settings, tenant_id="tenant-b") as client:
        assert client.get(f"/api/v1/cases/{CASE_ID}/panel").status_code == 404
        assert (
            client.post(
                f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/cancel", json={"reason": "x"}
            ).status_code
            == 404
        )


def test_the_edit_state_read_is_never_stored(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """`no-store`, not `no-cache`. `no-cache` still permits storage, and an
    autosaved draft is one person's unfinished thinking."""
    with _client(mongo, test_settings) as client:
        answer = client.get(f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state")

    assert answer.status_code == 200
    assert answer.headers["Cache-Control"] == "private, no-store"
    assert answer.json()["data"]["payload"] is None


def test_one_actor_never_reads_anothers_edit(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    with _client(mongo, test_settings, subject="associate-a") as first_client:
        first_client.put(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state",
            json={
                "client_edit_id": "a-1",
                "base_draft_version": 1,
                "payload": {**DRAFT, "subject": "A's wording"},
            },
        )
    with _client(mongo, test_settings, subject="associate-b") as second_client:
        answer = second_client.get(f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/edit-state")

    assert answer.json()["data"]["payload"] is None
    assert "A's wording" not in answer.text


# --------------------------------------------------------------------------- #
# The approval hash the panel serves
# --------------------------------------------------------------------------- #


def test_the_panel_serves_a_hash_that_actually_approves(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The console cannot derive this, so the panel has to serve it.

    The CAS compares against `canonical_payload_digest(canonical_review_payload(...))`
    -- the store's canonical serialization, of the canonical edit where there is
    one and the draft where there is not. A browser computing that would be a
    second implementation of a compare-and-set in another language, and the two
    would disagree the first time either side changed how a payload serializes.
    Every approval from the console would then answer 409 for a reason no
    associate could act on.

    Asserted end to end and in the strongest available direction: the hash is
    taken **off the panel** and posted **to the endpoint**, so a serialization
    change on either side fails here rather than in a branch.
    """
    with _client(mongo, test_settings) as client:
        view = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )
        review = view.reviews[0]
        assert review.approval_hash is not None

        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json={
                "draft_version": review.draft_version,
                "canonical_edit_version": review.canonical_edit_version,
                "canonical_approved_payload_hash": review.approval_hash,
            },
        )

    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["state"] == ReviewState.APPROVING.value


@pytest.mark.asyncio
async def test_a_hash_from_a_panel_read_before_the_draft_moved_is_refused(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The other half, and the one the CAS exists for.

    Echoing the served hash would be worthless if it made approval unconditional.
    It does not: a draft that moved between the panel read and the approval
    produces a different digest, and the store refuses -- which is exactly
    "an associate approves the bytes they read".
    """
    with _client(mongo, test_settings) as client:
        stale = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        ).reviews[0]

    # The draft is re-rendered under the reader, as `rerender_template_draft`
    # does when a revision lands.
    await store.record_draft_revision(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        draft_payload={**DRAFT, "subject": "Return 9100 (corrected)"},
        expected_draft_version=stale.draft_version,
    )

    with _client(mongo, test_settings) as client:
        fresh = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        ).reviews[0]
        assert fresh.approval_hash != stale.approval_hash, (
            "a re-rendered draft must hash differently, or the CAS is decorative"
        )

        answer = client.post(
            f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/approve",
            json={
                "draft_version": stale.draft_version,
                "canonical_edit_version": stale.canonical_edit_version,
                "canonical_approved_payload_hash": stale.approval_hash,
            },
        )

    assert answer.status_code == 409
    assert answer.json()["detail"]["code"] == "ReviewVersionMismatchError"


def test_a_review_past_open_serves_no_approval_hash(
    store: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """A value nothing can use would ride in every panel body, and in its hash,
    for the life of the case."""
    with _client(mongo, test_settings) as client:
        client.post(f"/api/v1/cases/{CASE_ID}/reviews/{REVIEW_ID}/cancel", json={"reason": "x"})
        view = CasePanelView.model_validate(
            client.get(f"/api/v1/cases/{CASE_ID}/panel").json()["data"]
        )

    assert view.reviews[0].state == ReviewState.CANCELLED.value
    assert view.reviews[0].approval_hash is None
