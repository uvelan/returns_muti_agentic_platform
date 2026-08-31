"""V1: the review gate's work, and the two carry-forward conditions on it.

Contracts.md sect. 6. Three groups, and only the first is about rendering:

* the **payload** is the shape approval hashes, the panel renders and a
  reviewer edits, and the body Support receives is recomposed from it rather
  than taken from the string the renderer produced -- otherwise "what the
  reviewer approved" and "what was sent" are two values;
* **condition 7**, human-authored text in an outbound Channel B message: a
  field edit is neutralised because it sits inside an agent-authored frame, a
  whole-body edit is not because there is no frame left to impersonate;
* **conditions 5a and 8**, the conflict question asked of the edit *rows*
  rather than of the flag, with the torn state built by hand and the guard
  fault-injected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.configuration.support_gate_configuration import RequestGrouping
from return_platform.operations import fact_names
from return_platform.operations.case_commands import (
    DurableCaseCommandStore,
    ensure_case_command_indexes,
)
from return_platform.operations.review_aggregate import (
    REVIEW_DRAFT_EDITS,
    ReviewAggregateStore,
    ReviewConflictError,
    ReviewKind,
    ReviewState,
    canonical_review_payload,
    ensure_review_indexes,
)
from return_platform.operations.support_events import canonical_payload_digest
from return_platform.operations.support_template_draft import SAMPLE_CASE, draft_facts
from return_platform.operations.support_template_gate import (
    PAYLOAD_BODY_OVERRIDE,
    PAYLOAD_SECTIONS,
    PAYLOAD_TEXT,
    MongoDraftEditRows,
    SupportTemplateGateService,
    neutralise_field_edits,
    payload_of,
    payload_text,
    request_ids_for,
    unresolved_edit_actors,
)
from return_platform.operations.support_template_renderer import (
    TemplateDraftInput,
    TemplateRenderContext,
    render_support_template,
)
from tests.operations.mongo_double import FakeClient
from tests.operations.scoped_fact_double import ScopedFactDouble

_async = pytest.mark.asyncio

PRODUCTION_YAML = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"
CASE_ID = "case-gate-1"
REQUEST_ID = "support:case-gate-1"
REVIEW_ID = "review-gate-1"
WORKFLOW_ID = "return-case-case-gate-1"


@pytest.fixture(scope="module")
def configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(PRODUCTION_YAML).configuration


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest_asyncio.fixture
async def reviews(mongo: FakeClient, test_settings: Settings) -> ReviewAggregateStore:
    database = mongo[test_settings.mongo_database]
    await ensure_review_indexes(database)
    await ensure_case_command_indexes(cast(Any, database))
    return ReviewAggregateStore(
        cast(Any, mongo),
        test_settings,
        command_store=DurableCaseCommandStore(cast(Any, mongo), test_settings),
    )


class _Thread:
    def __init__(self, work_item_id: str, thread_id: str, created: bool) -> None:
        self.workItemId = work_item_id  # noqa: N815 - the wire name
        self.threadId = thread_id  # noqa: N815
        self.created = created


class _Post:
    def __init__(self, absorbed: bool) -> None:
        self.absorbed = absorbed


class _Support:
    """The two thread operations, recording what they were handed.

    Not a `NotImplementedError` stub anywhere: a double whose method exists and
    raises is exactly the shape the platform's port advisory warns about, and a
    test double that models it teaches the pattern.
    """

    def __init__(
        self, *, created: bool = False, absorbed: bool = False, fail: bool = False
    ) -> None:
        self._created = created
        self._absorbed = absorbed
        self._fail = fail
        self.ensured: list[dict[str, Any]] = []
        self.posted: list[dict[str, Any]] = []

    async def ensure_case_support_thread(self, **kwargs: Any) -> _Thread:
        self.ensured.append(dict(kwargs))
        return _Thread("wi-1", "th-1", self._created)

    async def post_support_message(self, **kwargs: Any) -> _Post:
        self.posted.append(dict(kwargs))
        if self._fail:
            raise TimeoutError("support is unreachable")
        return _Post(self._absorbed)


def _service(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
    *,
    support: _Support | None = None,
    facts: ScopedFactDouble | None = None,
) -> SupportTemplateGateService:
    return SupportTemplateGateService(
        reviews=reviews,
        edit_rows=MongoDraftEditRows(mongo[test_settings.mongo_database]),
        support_service=support or _Support(),
        configuration=lambda: configuration,
        append_fact=facts or ScopedFactDouble(),
    )


def _render_facts() -> dict[tuple[str | None, str], dict[str, Any]]:
    return draft_facts(**SAMPLE_CASE)


# --------------------------------------------------------------------------- #
# The payload, and the body that comes out of it
# --------------------------------------------------------------------------- #


@_async
async def test_the_gate_composes_the_same_body_the_renderer_did(
    configuration: ReturnPlatformConfiguration,
) -> None:
    """`payload_text` is a second implementation of the renderer's `_text_of`.

    A duplicated formatter that drifted would send Support a differently-shaped
    message than the preview screen shows, and neither side would fail. So the
    two are compared on the **shipped** default variant with a real fact set:
    same string, character for character.
    """
    rendered = await render_support_template(
        configuration.support_template,
        TemplateDraftInput(
            case_id=CASE_ID,
            context=TemplateRenderContext(),
            facts=_render_facts(),
        ),
    )
    assert payload_text(payload_of(rendered)) == rendered.text
    assert rendered.text.strip(), "an empty body would make the comparison vacuous"


@_async
async def test_an_edited_field_changes_the_body(
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The reason the body is recomposed rather than read from `text`.

    Reading `payload["text"]` would send the *original* message after a field
    edit -- the reviewer's change would be visible on the panel and absent from
    Support's copy.
    """
    rendered = await render_support_template(
        configuration.support_template,
        TemplateDraftInput(case_id=CASE_ID, context=TemplateRenderContext(), facts=_render_facts()),
    )
    payload = payload_of(rendered)
    edited = _edit_first_field(payload, "EDITED-BY-A-REVIEWER")

    assert "EDITED-BY-A-REVIEWER" in payload_text(edited)
    assert "EDITED-BY-A-REVIEWER" not in edited[PAYLOAD_TEXT], (
        "the stale `text` key is exactly what must not be sent"
    )


def _edit_first_field(payload: dict[str, Any], value: str) -> dict[str, Any]:
    copied = {key: item for key, item in payload.items()}
    sections = [dict(section) for section in copied[PAYLOAD_SECTIONS]]
    for section in sections:
        fields = [dict(item) for item in section["fields"]]
        if fields:
            fields[0]["value"] = value
            section["fields"] = fields
            break
        section["fields"] = fields
    copied[PAYLOAD_SECTIONS] = sections
    return copied


# --------------------------------------------------------------------------- #
# Condition 7: human-authored text in an outbound message
# --------------------------------------------------------------------------- #

#: A value shaped exactly like this template's own section headings. The same
#: shape phase 1 found reaching a rendered handoff intact through
#: `associate_notes`.
IMPERSONATION = "BAY ASSIGNMENT:"


def _payload_with(value: str, *, override: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": "t",
        "variant_id": "default",
        "subject": "Return",
        "text": "stale",
        PAYLOAD_SECTIONS: [
            {
                "section_id": "notes",
                "title": "RETURN DETAILS",
                "return_record_id": None,
                "fields": [
                    {
                        "field_id": "associate_notes_rendered",
                        "label": None,
                        "value": value,
                        "source": "case_fact",
                        "source_path": "associate_notes_rendered",
                        "fact_id": None,
                        "applied_fallback": False,
                    }
                ],
            }
        ],
        "gaps": [],
    }
    if override is not None:
        payload[PAYLOAD_BODY_OVERRIDE] = override
    return payload


def test_a_field_edit_shaped_like_the_framing_is_neutralised() -> None:
    """Condition 7. A field edit sits inside an agent-authored frame, so the
    reader cannot tell one from the other -- exactly `associate_notes`."""
    safe = neutralise_field_edits(_payload_with(IMPERSONATION))

    assert IMPERSONATION not in payload_text(safe)
    assert "[removed]" in payload_text(safe)


def test_an_ordinary_field_edit_survives_untouched() -> None:
    """The guard must not be a censor: only heading-shaped lines are removed."""
    safe = neutralise_field_edits(_payload_with("Customer asked for a refund, not a swap."))

    assert "Customer asked for a refund, not a swap." in payload_text(safe)
    assert "[removed]" not in payload_text(safe)


def test_a_whole_body_override_is_the_reviewers_own_message() -> None:
    """The recorded exception, and the reason it is a distinct payload key.

    There is no agent-authored frame left to impersonate: the reviewer is
    looking at the text they are about to send and every line of it is theirs.
    Neutralising it would delete their own headings.
    """
    body = "URGENT:\nPlease reissue the RMA.\n"
    safe = neutralise_field_edits(_payload_with("anything", override=body))

    assert payload_text(safe) == body
    assert "[removed]" not in payload_text(safe)


def test_neutralisation_covers_every_field_of_every_section() -> None:
    """Not just the first, and not just case-level sections.

    A per-record group is where a multi-RMA message would carry two copies of
    an editable field, and a guard that stopped at the first section would
    neutralise one of them.
    """
    payload = _payload_with("clean")
    payload[PAYLOAD_SECTIONS] = [
        dict(payload[PAYLOAD_SECTIONS][0]),
        {
            "section_id": "record",
            "title": "RMA",
            "return_record_id": "rec-2",
            "fields": [
                {
                    "field_id": "note",
                    "label": "Note",
                    "value": IMPERSONATION,
                    "source": "case_fact",
                    "source_path": "note",
                    "fact_id": None,
                    "applied_fallback": False,
                }
            ],
        },
    ]

    assert IMPERSONATION not in payload_text(neutralise_field_edits(payload))


@_async
async def test_delivery_neutralises_even_a_payload_that_was_stored_raw(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """The braces to the API's belt.

    Suppose the endpoint's neutralisation is removed, bypassed, or a payload is
    written by some future path that forgets. The message that actually leaves
    the platform is composed here, and it is composed from neutralised fields.
    """
    support = _Support()
    service = _service(reviews, mongo, test_settings, configuration, support=support)
    await _approved_review(reviews, service, payload=_payload_with(IMPERSONATION))

    await service.deliver_approved(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        tenant_id="default",
        principal_id="p-1",
        fact_id_seed="seed-1",
    )

    sent = support.posted[0]["message_text"]
    assert IMPERSONATION not in sent
    assert "[removed]" in sent


@_async
async def test_delivery_sends_the_frozen_canonical_edit_not_the_draft(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """What a person approved is what leaves the platform.

    Every other delivery test in this file approves a review that has **no
    canonical edit**, so `canonical_review_payload` returns the draft and the
    two candidate sources are byte-identical -- the choice between them cannot
    be observed, and a delivery that read `draftPayload` directly would pass
    all of them. (ACC3 category-B audit: INJ-B11 did exactly that and left
    5,235 backend tests green.)

    Here they differ. The draft says the return was rejected; the associate
    resolved a canonical edit saying it was approved, and *that* is the text
    the gate froze at approval and verified by hash. If delivery re-reads the
    draft, Support is told the opposite of what the associate signed off --
    silently, with a valid approval receipt behind it.
    """
    support = _Support()
    service = _service(reviews, mongo, test_settings, configuration, support=support)
    await reviews.create_review(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        review_kind=ReviewKind.TEMPLATE,
        draft_payload=_payload_with("REJECTED -- do not refund"),
        review_id=REVIEW_ID,
    )
    await reviews.resolve_canonical_edit(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        resolved_by="approver-1",
        canonical_payload=_payload_with("APPROVED -- refund issued"),
        resolved_from_actor_edit_ids=[],
    )
    review = await reviews.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    frozen = canonical_review_payload(review)
    # The premise of the test, asserted rather than assumed: the two sources
    # really are different here. Without this the test would silently decay
    # back into the shape it exists to replace if the fixture ever stopped
    # writing a canonical edit.
    assert frozen != dict(review["draftPayload"])

    await service.approve(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        actor_id="approver-1",
        expected_draft_version=int(review["draftVersion"]),
        expected_canonical_edit_version=int(review["canonicalEditVersion"]),
        canonical_approved_payload_hash=canonical_payload_digest(frozen),
        workflow_id=WORKFLOW_ID,
        signal_id="sig-1",
    )
    await service.deliver_approved(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        tenant_id="default",
        principal_id="p-1",
        fact_id_seed="seed-1",
    )

    sent = support.posted[0]["message_text"]
    assert "APPROVED -- refund issued" in sent
    assert "REJECTED" not in sent, "the superseded draft must not be what is sent"


# --------------------------------------------------------------------------- #
# Conditions 5a and 8: the conflict question, asked of the rows
# --------------------------------------------------------------------------- #


async def _approved_review(
    reviews: ReviewAggregateStore,
    service: SupportTemplateGateService,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload if payload is not None else _payload_with("clean")
    await reviews.create_review(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        review_kind=ReviewKind.TEMPLATE,
        draft_payload=body,
        review_id=REVIEW_ID,
    )
    review = await reviews.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    approved, _ = await service.approve(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        actor_id="approver-1",
        expected_draft_version=int(review["draftVersion"]),
        expected_canonical_edit_version=int(review["canonicalEditVersion"]),
        canonical_approved_payload_hash=canonical_payload_digest(canonical_review_payload(review)),
        workflow_id=WORKFLOW_ID,
        signal_id="sig-1",
    )
    return approved


async def _torn_two_actor_state(
    reviews: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """S2's F3 residue, built by hand.

    Two actors hold edit rows and **no flag was ever set** -- the process died
    between `_after_edit_written`'s insert and its flag transaction, and no
    later autosave re-ran the check. Written directly to the collection rather
    than through `upsert_draft_edit`, because going through the writer would
    run the very flag transaction this state is defined by the absence of.
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
                "payload": {"text": f"{actor} wrote this"},
            }
        )
    review = await reviews.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    assert review["conflictPresent"] is False, "the torn state is defined by the flag being clean"
    assert (await reviews.conflict_marker(CASE_ID))["present"] is False


@_async
async def test_approval_refuses_a_torn_two_actor_state_the_flag_missed(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """Conditions 5a and 8, the whole point.

    Before this guard: `approve()` succeeds, freezes `draftPayload` -- the
    *agent's* draft -- and both associates' edits are discarded with no 409 and
    nothing on screen. After: S2's own `ReviewConflictError`, so the endpoint
    answers 409 and the panel offers Resolve exactly as it does for a flagged
    conflict.
    """
    service = _service(reviews, mongo, test_settings, configuration)
    await reviews.create_review(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        review_kind=ReviewKind.TEMPLATE,
        draft_payload=_payload_with("clean"),
        review_id=REVIEW_ID,
    )
    await _torn_two_actor_state(reviews, mongo, test_settings)
    review = await reviews.get_review(case_id=CASE_ID, review_id=REVIEW_ID)

    with pytest.raises(ReviewConflictError):
        await service.approve(
            case_id=CASE_ID,
            review_id=REVIEW_ID,
            actor_id="approver-1",
            expected_draft_version=int(review["draftVersion"]),
            expected_canonical_edit_version=int(review["canonicalEditVersion"]),
            canonical_approved_payload_hash=canonical_payload_digest(
                canonical_review_payload(review)
            ),
            workflow_id=WORKFLOW_ID,
            signal_id="sig-1",
        )

    after = await reviews.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    assert ReviewState(str(after["state"])) is ReviewState.OPEN
    assert after["approvedPayload"] is None


@_async
async def test_the_bare_store_approves_the_same_torn_state(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
) -> None:
    """The fault injection, kept in the suite rather than run once.

    This is the *unguarded* path -- `ReviewAggregateStore.approve` straight --
    and it proves the state above is genuinely dangerous rather than something
    S2 already refuses. If S2 later closes it, this test fails and the guard
    above becomes provably redundant, which is a good day and a real signal.
    """
    await reviews.create_review(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        review_kind=ReviewKind.TEMPLATE,
        draft_payload=_payload_with("clean"),
        review_id=REVIEW_ID,
    )
    await _torn_two_actor_state(reviews, mongo, test_settings)
    review = await reviews.get_review(case_id=CASE_ID, review_id=REVIEW_ID)

    approved, _ = await reviews.approve(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        actor_id="approver-1",
        expected_draft_version=int(review["draftVersion"]),
        expected_canonical_edit_version=int(review["canonicalEditVersion"]),
        canonical_approved_payload_hash=canonical_payload_digest(canonical_review_payload(review)),
        workflow_id=WORKFLOW_ID,
        signal_id="sig-2",
    )

    assert ReviewState(str(approved["state"])) is ReviewState.APPROVING
    assert approved["approvedPayload"] is not None


@_async
async def test_the_recompute_reads_what_upsert_draft_edit_writes(
    reviews: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The field-name pin.

    `MongoDraftEditRows` reads `reviewId`, `actorId` and `_id` off S2's
    collection. Asserting those names against my own fixture would prove
    nothing; this runs S2's **writer** and then the reader, so a rename in S2
    breaks this test rather than silently emptying the approval guard.
    """
    await reviews.create_review(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        review_kind=ReviewKind.TEMPLATE,
        draft_payload=_payload_with("clean"),
        review_id=REVIEW_ID,
    )
    await reviews.upsert_draft_edit(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        actor_id="associate-a",
        client_edit_id="c-1",
        base_draft_version=1,
        payload={"text": "mine"},
    )

    rows = await MongoDraftEditRows(mongo[test_settings.mongo_database]).edit_rows(
        review_id=REVIEW_ID
    )

    assert [str(row["actorId"]) for row in rows] == ["associate-a"]
    assert all(row.get("_id") for row in rows)


def test_a_sole_actors_unsubmitted_edit_is_not_a_conflict() -> None:
    """One person's draft they chose not to send is not two people disagreeing,
    and refusing it would make the guard block the ordinary case."""
    rows = [{"_id": "e1", "actorId": "a"}]
    assert unresolved_edit_actors(rows, {"canonicalEdit": None}) == frozenset()


def test_no_edits_at_all_is_not_a_conflict() -> None:
    assert unresolved_edit_actors([], {"canonicalEdit": None}) == frozenset()


def test_a_resolved_canonical_edit_clears_the_recompute() -> None:
    """Two actors, resolved: approval must proceed. Otherwise conflict
    resolution would leave the review permanently unapprovable."""
    rows = [{"_id": "e1", "actorId": "a"}, {"_id": "e2", "actorId": "b"}]
    review = {"canonicalEdit": {"resolved_from_actor_edit_ids": ["e1", "e2"]}}
    assert unresolved_edit_actors(rows, review) == frozenset()


def test_an_edit_added_after_resolution_reopens_the_recompute() -> None:
    """A third actor arriving after the merge is a new disagreement.

    S2's flag path notices this too; the recompute agreeing with it is what
    makes the two mechanisms one answer rather than two.
    """
    rows = [
        {"_id": "e1", "actorId": "a"},
        {"_id": "e2", "actorId": "b"},
        {"_id": "e3", "actorId": "c"},
    ]
    review = {"canonicalEdit": {"resolved_from_actor_edit_ids": ["e1"]}}
    assert unresolved_edit_actors(rows, review) == frozenset({"b", "c"})


_EARLIER = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
_RESOLVED = datetime(2026, 8, 30, 9, 5, tzinfo=UTC)
_LATER = datetime(2026, 8, 30, 9, 10, tzinfo=UTC)


def test_a_discarded_resolution_names_no_row_and_still_clears_the_recompute() -> None:
    """Contracts.md sect. 6 offers **discard** as a resolution, and it names no
    edit row -- the canonical payload came from nobody's draft.

    Covered by the id list alone, this state refused forever: the marker
    cleared, the panel said "resolved", and approval answered `409
    ReviewConflictError` on rows the resolution had already answered. Found by
    driving the endpoints end to end rather than by reading the function.
    """
    rows = [
        {"_id": "e1", "actorId": "a", "updatedAt": _EARLIER},
        {"_id": "e2", "actorId": "b", "updatedAt": _EARLIER},
    ]
    review = {"canonicalEdit": {"resolved_from_actor_edit_ids": [], "resolved_at": _RESOLVED}}
    assert unresolved_edit_actors(rows, review) == frozenset()


def test_two_actors_editing_again_after_a_resolution_reopen_it() -> None:
    """The other direction, so recency is not a blanket amnesty.

    Both rows are written *after* the resolution and neither is named, so the
    canonical edit answers neither of them and approval must refuse again.
    """
    rows = [
        {"_id": "e1", "actorId": "a", "updatedAt": _LATER},
        {"_id": "e2", "actorId": "b", "updatedAt": _LATER},
    ]
    review = {"canonicalEdit": {"resolved_from_actor_edit_ids": [], "resolved_at": _RESOLVED}}
    assert unresolved_edit_actors(rows, review) == frozenset({"a", "b"})


def test_a_lone_late_editor_after_a_resolution_is_not_a_conflict_here() -> None:
    """One person typing over a resolved draft is the ordinary case, not a
    disagreement -- the module's own rule for a sole actor.

    **This is a behaviour change from the untimestamped answer above**, and it
    is not a hole: S2's flag path sets `conflictPresent` the moment a second
    actor holds a row, and the recompute only ever *adds* a refusal to a review
    whose flag reads clean. The state below is flagged, so `approve` refuses on
    the flag whatever this function says. What changes is that the recompute no
    longer disagrees with the resolution about rows the resolution answered.
    """
    rows = [
        {"_id": "e1", "actorId": "a", "updatedAt": _EARLIER},
        {"_id": "e2", "actorId": "b", "updatedAt": _EARLIER},
        {"_id": "e3", "actorId": "c", "updatedAt": _LATER},
    ]
    review = {"canonicalEdit": {"resolved_from_actor_edit_ids": [], "resolved_at": _RESOLVED}}
    assert unresolved_edit_actors(rows, review) == frozenset()


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #


@_async
async def test_an_absorbed_redelivery_still_reaches_sent(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """Contracts.md sect. 7: absorption *is* delivery.

    The receiver already holds a message under this delivery id. Treating that
    as a failure would leave the review `DELIVERY_FAILED` beside a message
    Support has actually read, and an operator would retry it forever.
    """
    facts = ScopedFactDouble()
    support = _Support(absorbed=True)
    service = _service(reviews, mongo, test_settings, configuration, support=support, facts=facts)
    await _approved_review(reviews, service)

    outcome = await service.deliver_approved(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        tenant_id="default",
        principal_id="p-1",
        fact_id_seed="seed-1",
    )

    assert outcome.state == ReviewState.SENT.value
    assert outcome.absorbed is True
    review = await reviews.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    assert ReviewState(str(review["state"])) is ReviewState.SENT
    snapshot = facts.named(fact_names.SUPPORT_SENT_SNAPSHOT_REF)
    assert len(snapshot) == 1
    assert snapshot[0]["value"]["absorbed"] is True


@_async
async def test_opening_the_thread_is_the_message(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """`created=True` means this call opened the conversation with the draft in
    it. Posting again would put the same text on the thread twice, which is the
    one duplicate the delivery identity does not cover."""
    support = _Support(created=True)
    service = _service(reviews, mongo, test_settings, configuration, support=support)
    await _approved_review(reviews, service)

    outcome = await service.deliver_approved(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        tenant_id="default",
        principal_id="p-1",
        fact_id_seed="seed-1",
    )

    assert outcome.state == ReviewState.SENT.value
    assert support.posted == [], "the opening request is the message"


@_async
async def test_a_send_that_fails_lands_on_delivery_failed(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    support = _Support(fail=True)
    service = _service(reviews, mongo, test_settings, configuration, support=support)
    await _approved_review(reviews, service)

    outcome = await service.deliver_approved(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        tenant_id="default",
        principal_id="p-1",
        fact_id_seed="seed-1",
    )

    assert outcome.state == ReviewState.DELIVERY_FAILED.value
    assert outcome.error_code == "TimeoutError"
    review = await reviews.get_review(case_id=CASE_ID, review_id=REVIEW_ID)
    assert ReviewState(str(review["state"])) is ReviewState.DELIVERY_FAILED
    assert review["deliveryId"] is not None, "the identity is kept for the retry"


@_async
async def test_delivering_twice_posts_once(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """Replay safety. The second call sees `SENT` and does nothing at all."""
    support = _Support()
    facts = ScopedFactDouble()
    service = _service(reviews, mongo, test_settings, configuration, support=support, facts=facts)
    await _approved_review(reviews, service)

    first = await service.deliver_approved(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        tenant_id="default",
        principal_id="p-1",
        fact_id_seed="seed-1",
    )
    second = await service.deliver_approved(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        tenant_id="default",
        principal_id="p-1",
        fact_id_seed="seed-1",
    )

    assert first.state == second.state == ReviewState.SENT.value
    assert len(support.posted) == 1
    assert len(facts.named(fact_names.SUPPORT_SENT_SNAPSHOT_REF)) == 1


# --------------------------------------------------------------------------- #
# Drafting, and the grouping
# --------------------------------------------------------------------------- #


@_async
async def test_recording_a_draft_opens_one_review_and_logs_it(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    facts = ScopedFactDouble()
    service = _service(reviews, mongo, test_settings, configuration, facts=facts)

    draft = await service.record_draft(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        review_id=REVIEW_ID,
        fact_id_seed="seed-1",
        facts=_render_facts(),
    )

    assert draft.review_id == REVIEW_ID
    assert draft.state == ReviewState.OPEN.value
    assert draft.template_available is True
    assert draft.text.strip()
    assert len(facts.named(fact_names.SUPPORT_TEMPLATE_DRAFT)) == 1
    assert len(facts.named(fact_names.TEMPLATE_DRAFT_READY)) == 1


@_async
async def test_recording_the_same_draft_twice_opens_one_review(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """Replay. Two attempts over one request would be two answers to one
    question, and the reviewer would see the second while the workflow waited
    on the first."""
    service = _service(reviews, mongo, test_settings, configuration)
    args: dict[str, Any] = {
        "case_id": CASE_ID,
        "request_id": REQUEST_ID,
        "review_id": REVIEW_ID,
        "fact_id_seed": "seed-1",
        "facts": _render_facts(),
    }

    first = await service.record_draft(**args)
    second = await service.record_draft(**args)

    assert first.review_id == second.review_id
    assert len(await reviews.list_reviews(CASE_ID)) == 1


@_async
async def test_a_release_with_no_template_reports_it_rather_than_gapping(
    reviews: ReviewAggregateStore, mongo: FakeClient, test_settings: Settings
) -> None:
    """The defaulted, pre-template release. The caller falls back to the
    composed path -- which is exactly what an un-patched history does -- so a
    deployment that has published no template is not one whose cases park."""
    service = SupportTemplateGateService(
        reviews=reviews,
        edit_rows=MongoDraftEditRows(mongo[test_settings.mongo_database]),
        support_service=_Support(),
        configuration=lambda: None,
        append_fact=ScopedFactDouble(),
    )

    draft = await service.record_draft(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        review_id=REVIEW_ID,
        fact_id_seed="seed-1",
        facts={},
    )

    assert draft.template_available is False
    assert draft.review_id is None
    assert await reviews.list_reviews(CASE_ID) == []


def test_one_per_case_is_one_request_whatever_the_records_say() -> None:
    assert request_ids_for(CASE_ID, [], RequestGrouping.ONE_PER_CASE) == (f"support:{CASE_ID}",)
    assert request_ids_for(
        CASE_ID,
        [{"returnMethod": "PARCEL"}, {"returnMethod": "LTL"}],
        RequestGrouping.ONE_PER_CASE,
    ) == (f"support:{CASE_ID}",)


def test_grouping_by_shipping_mode_is_stable_and_deduplicated() -> None:
    """Stable order is not tidiness: the wait map is keyed on these and the
    reminder text lists them, so an unstable order re-words every reminder."""
    records = [
        {"returnMethod": "PARCEL"},
        {"returnMethod": "LTL"},
        {"returnMethod": "PARCEL"},
    ]
    ids = request_ids_for(CASE_ID, records, RequestGrouping.BY_SHIPPING_MODE)

    assert ids == (f"support:{CASE_ID}:LTL", f"support:{CASE_ID}:PARCEL")
    assert (
        request_ids_for(CASE_ID, list(reversed(records)), RequestGrouping.BY_SHIPPING_MODE) == ids
    )


def test_a_case_with_no_records_never_groups() -> None:
    """The handoff that *asks* for the first RMA cannot be grouped by a
    shipping mode nobody has issued."""
    assert request_ids_for(CASE_ID, [], RequestGrouping.BY_SHIPPING_MODE) == (f"support:{CASE_ID}",)


# --------------------------------------------------------------------------- #
# The fact writes, against the signature that actually receives them
# --------------------------------------------------------------------------- #


@_async
async def test_every_gate_fact_write_fits_the_repository_it_is_handed_to(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """All five writes, driven through the paths that make them.

    Not a unit test of one call: a kwarg is wrong per *call site*, so exercising
    one would say nothing about the other four.
    """
    facts = ScopedFactDouble()
    support = _Support()
    gate = _service(reviews, mongo, test_settings, configuration, support=support, facts=facts)

    await gate.record_draft(
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        facts=_render_facts(),
        fact_id_seed="seed-1",
        review_id=REVIEW_ID,
    )
    await gate.record_revision(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        actor_id="associate-a",
        note="please add the RMA",
        fact_id_seed="seed-2",
    )

    # Draft, ready, and the revision at minimum. Asserted by name so a write
    # that silently stopped happening is not read as a pass.
    assert facts.named(fact_names.SUPPORT_TEMPLATE_DRAFT)
    assert facts.named(fact_names.TEMPLATE_DRAFT_READY)
    assert facts.named(fact_names.SUPPORT_TEMPLATE_REVISION)


@_async
async def test_the_revision_fact_carries_the_actor_under_the_agreed_key(
    reviews: ReviewAggregateStore,
    mongo: FakeClient,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
) -> None:
    """Contracts sect. 4: a command-originated fact carries a **server-stamped
    `actorId`**, and S1 phase 1b shipped the real top-level field.

    Asserted on the **stored** document key, not on the parameter name. Those
    are different questions, and only one of them is the audit guarantee: a test
    that checked `actor_id` would pass against a repository that accepted the
    argument and dropped it on the floor — the endpoint would look fine and the
    fact log still could not say who decided.

    The three negative assertions are the migration itself. This slice carried
    the actor inside `value["actorId"]` while the document had nowhere to put
    it; if that key survived alongside the parameter, both spellings would
    coexist and the migration would never have happened. `answeredBy` is V3's
    pre-agreement spelling and is checked for the same reason.
    """
    facts = ScopedFactDouble()
    gate = _service(reviews, mongo, test_settings, configuration, facts=facts)

    await gate.record_revision(
        case_id=CASE_ID,
        review_id=REVIEW_ID,
        actor_id="associate-a",
        # A header **on its own line**, which is what impersonating the
        # framing means: `_FRAMING` matches a heading that occupies a whole
        # line, and "BAY ASSIGNMENT: not really" is a sentence that happens to
        # start with capitals -- not a section a reader would mistake for the
        # message's own. Getting that distinction wrong in the fixture is how
        # this assertion would have looked green and meant nothing.
        note="please read this\nBAY ASSIGNMENT:\nsend it to bay 4",
        fact_id_seed="seed-1",
    )

    stored = facts.stored(fact_names.SUPPORT_TEMPLATE_REVISION)
    assert len(stored) == 1
    # The document key, which is what an auditor reads.
    assert stored[0]["actorId"] == "associate-a"

    value = stored[0]["value"]
    assert "actorId" not in value, "the stopgap key is gone, not merely joined"
    assert "actor_id" not in value
    assert "answeredBy" not in value

    # Which software, beside which person. Collapsing them would make an audit
    # unable to tell a reviewer's revision from the platform's own.
    assert stored[0]["agent_id"] == "support-template-gate"
    # Condition 7 still holds on the same value: a note cannot impersonate the
    # message's own framing on its way onto the log.
    assert "BAY ASSIGNMENT:" not in str(value["note"])
