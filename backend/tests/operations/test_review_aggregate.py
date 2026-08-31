"""S2: the review aggregate, one transition at a time.

Contracts.md sect. 6 in full -- the frozen state machine, the edit model with
its per-actor rows and canonical promotion, the conflict marker that a
canonical-edit write clears, and the one atomic transition that touches all
three co-located stores at once: `OPEN -> APPROVING`, which locks the review,
verifies both versions and the `canonical_approved_payload_hash`, freezes the
payload, mints the delivery identity and commits the command with its outbox
row -- or leaves nothing behind at all.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import pytest_asyncio

from return_platform.configuration.settings import Settings
from return_platform.operations.case_commands import (
    CASE_COMMAND_RECORDS,
    CaseCommandKind,
    DurableCaseCommandStore,
    StaleReviewVersionError,
    ensure_case_command_indexes,
)
from return_platform.operations.review_aggregate import (
    CASE_REVIEWS,
    DRAFT_EDIT_ACTOR_INDEX,
    REVIEW_DRAFT_EDITS,
    REVIEW_SCOPE_INDEX,
    SYSTEM_ACTOR,
    TERMINAL_REVIEW_STATES,
    EMPTY_REPLY_BODY_GAP_REASON,
    ApprovedPayloadHashMismatchError,
    EmptyReplyBodyError,
    PendingRevisionError,
    ReservedActorError,
    ReviewAggregateStore,
    ReviewConflictError,
    ReviewKind,
    ReviewState,
    ReviewStateError,
    ReviewVersionMismatchError,
    TemplateReviewParkReason,
    canonical_review_payload,
    ensure_review_indexes,
)
from return_platform.operations.support_events import canonical_payload_digest
from tests.operations.mongo_double import FakeClient, FakeCollection

CASE_ID = "case-9100"
REQUEST_ID = "req-1"
WORKFLOW_ID = "return-case-case-9100"
DRAFT = {"subject": "Return 9100", "body": "Please issue an RMA."}
EDITED = {"subject": "Return 9100", "body": "Please issue an RMA for two items."}
#: A `SUPPORT_REPLY` draft as `reply_gating.py` writes it. Separate from `DRAFT`
#: because the two kinds genuinely have different payload shapes -- a template
#: renders `subject`/`sections`, a reply carries `messageText` -- and because
#: approval now *refuses* a reply with no body, so a reply fixture borrowing the
#: template's payload would be refused for a reason the test is not about.
REPLY_DRAFT = {
    "messageText": "Your RMA is RMA-9100. Please include it on the outside of the parcel.",
    "disclosesAgent": True,
    "supportEventId": "evt-1",
    "intent": "rma_issued",
}


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest.fixture
def reviews(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database][CASE_REVIEWS]


@pytest.fixture
def edits(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database][REVIEW_DRAFT_EDITS]


@pytest.fixture
def commands(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database][CASE_COMMAND_RECORDS]


@pytest.fixture
def outbox(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database]["integration_outbox"]


@pytest_asyncio.fixture
async def store(mongo: FakeClient, test_settings: Settings) -> ReviewAggregateStore:
    database = cast(Any, mongo[test_settings.mongo_database])
    await ensure_case_command_indexes(database)
    await ensure_review_indexes(database)
    await database["integration_outbox"].create_index("idempotencyKey", unique=True)
    commands = DurableCaseCommandStore(cast(Any, mongo), test_settings)
    return ReviewAggregateStore(cast(Any, mongo), test_settings, command_store=commands)


async def _open_review(
    store: ReviewAggregateStore,
    *,
    kind: ReviewKind = ReviewKind.TEMPLATE,
    request_id: str = REQUEST_ID,
    draft_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One open review.

    `draft_payload` defaults to the payload shape its kind actually has, so a
    reply fixture is a reply rather than a template wearing one. Passed
    explicitly where the payload is the subject of the test.
    """
    if draft_payload is None:
        draft_payload = REPLY_DRAFT if kind is ReviewKind.SUPPORT_REPLY else DRAFT
    return await store.create_review(
        case_id=CASE_ID,
        request_id=request_id,
        review_kind=kind,
        draft_payload=draft_payload,
    )


async def _approve(
    store: ReviewAggregateStore,
    review: dict[str, Any],
    *,
    actor_id: str = "associate-1",
    signal_id: str = "sig-approve-1",
    **overrides: Any,
) -> tuple[dict[str, Any], Any]:
    kwargs: dict[str, Any] = {
        "case_id": CASE_ID,
        "review_id": str(review["_id"]),
        "actor_id": actor_id,
        "expected_draft_version": int(review["draftVersion"]),
        "expected_canonical_edit_version": int(review["canonicalEditVersion"]),
        "canonical_approved_payload_hash": canonical_payload_digest(
            canonical_review_payload(review)
        ),
        "workflow_id": WORKFLOW_ID,
        "signal_id": signal_id,
    }
    kwargs.update(overrides)
    return await store.approve(**kwargs)


# --------------------------------------------------------------------------- #
# Creation and scope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_review_opens_once_per_request_and_reopening_returns_the_same_attempt(
    store: ReviewAggregateStore, reviews: FakeCollection
) -> None:
    """Check-then-act, and the partial unique index behind it.

    Two callers drafting the same request must not produce two reviews: one
    request has one answer, and a second open review would be a second answer
    nobody asked for.
    """
    first = await _open_review(store)
    second = await _open_review(store)

    assert first["_id"] == second["_id"]
    assert len(reviews.documents) == 1
    assert first["state"] == ReviewState.OPEN.value
    assert first["draftVersion"] == 1
    assert first["canonicalEditVersion"] == 0


@pytest.mark.asyncio
async def test_the_scope_index_is_partial_on_exactly_the_non_terminal_states(
    store: ReviewAggregateStore, reviews: FakeCollection
) -> None:
    """The uniqueness has to stop at terminality, or redraft could never run.

    A cancelled attempt and its replacement share `(case, request, kind,
    scope)`; only a *live* pair is the contradiction.
    """
    declaration = next(
        options
        for _keys, options in reviews.index_calls
        if options.get("name") == REVIEW_SCOPE_INDEX
    )
    partial = declaration["partialFilterExpression"]
    assert set(partial["state"]["$in"]) == {
        state.value for state in ReviewState if state not in TERMINAL_REVIEW_STATES
    }
    assert declaration["unique"] is True


@pytest.mark.asyncio
async def test_a_reply_review_mints_its_own_scope_id_server_side(
    store: ReviewAggregateStore,
) -> None:
    """Contracts.md sect. 6: the gating transition mints reply scope, not a client."""
    reply = await _open_review(store, kind=ReviewKind.SUPPORT_REPLY, request_id="req-reply")

    assert reply["reviewKind"] == ReviewKind.SUPPORT_REPLY.value
    assert reply["scopeId"] and reply["scopeId"] != "req-reply"


@pytest.mark.asyncio
async def test_a_template_review_scopes_itself_to_its_request(
    store: ReviewAggregateStore,
) -> None:
    review = await _open_review(store)
    assert review["scopeId"] == REQUEST_ID


# --------------------------------------------------------------------------- #
# The reserved actor
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_system_is_reserved_on_every_client_facing_mutation(
    store: ReviewAggregateStore,
) -> None:
    """`SYSTEM` is how the platform approves; a request may never wear it.

    If a client could submit as `SYSTEM`, the audit trail would stop telling
    the difference between a person's decision and the platform's.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])

    for call in (
        store.request_revision(case_id=CASE_ID, review_id=review_id, actor_id=SYSTEM_ACTOR),
        store.upsert_draft_edit(
            case_id=CASE_ID,
            review_id=review_id,
            actor_id=SYSTEM_ACTOR,
            client_edit_id="c1",
            base_draft_version=1,
            payload=EDITED,
        ),
        store.submit_edit(case_id=CASE_ID, review_id=review_id, actor_id=SYSTEM_ACTOR),
        store.cancel(case_id=CASE_ID, review_id=review_id, actor_id=SYSTEM_ACTOR, reason="no"),
        store.abandon(case_id=CASE_ID, review_id=review_id, actor_id=SYSTEM_ACTOR, reason="no"),
        store.resume_from_hold(case_id=CASE_ID, review_id=review_id, actor_id=SYSTEM_ACTOR),
    ):
        with pytest.raises(ReservedActorError):
            await call


@pytest.mark.asyncio
async def test_approval_refuses_the_system_actor_unless_auto_send_asked_for_it(
    store: ReviewAggregateStore,
) -> None:
    """`auto_send` is the one caller allowed to be `SYSTEM`, and it says so."""
    review = await _open_review(store)

    with pytest.raises(ReservedActorError):
        await _approve(store, review, actor_id=SYSTEM_ACTOR)

    approved, receipt = await _approve(store, review, actor_id=SYSTEM_ACTOR, allow_system=True)
    assert approved["state"] == ReviewState.APPROVING.value
    assert approved["approvedBy"] == SYSTEM_ACTOR
    assert receipt.duplicate is False


# --------------------------------------------------------------------------- #
# The edit model
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_autosaves_coalesce_in_one_row_per_actor(
    store: ReviewAggregateStore, edits: FakeCollection
) -> None:
    """Autosaves are not facts: they overwrite in place and bump `edit_version`."""
    review = await _open_review(store)
    review_id = str(review["_id"])

    first = await store.upsert_draft_edit(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-1",
        client_edit_id="c1",
        base_draft_version=1,
        payload=DRAFT,
    )
    second = await store.upsert_draft_edit(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-1",
        client_edit_id="c2",
        base_draft_version=1,
        payload=EDITED,
    )

    assert len(edits.documents) == 1
    assert first["editVersion"] == 1
    assert second["editVersion"] == 2
    assert second["payload"] == EDITED


@pytest.mark.asyncio
async def test_a_replayed_client_edit_id_is_a_no_op_not_a_version_bump(
    store: ReviewAggregateStore,
) -> None:
    """The retried autosave -- a flaky connection, not a new decision."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    kwargs: dict[str, Any] = {
        "case_id": CASE_ID,
        "review_id": review_id,
        "actor_id": "associate-1",
        "client_edit_id": "c1",
        "base_draft_version": 1,
        "payload": EDITED,
    }

    first = await store.upsert_draft_edit(**kwargs)
    replay = await store.upsert_draft_edit(**kwargs)

    assert first["editVersion"] == replay["editVersion"] == 1


@pytest.mark.asyncio
async def test_an_autosave_against_a_stale_base_draft_version_is_refused(
    store: ReviewAggregateStore,
) -> None:
    """The draft moved under the editor; their base is no longer the draft."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    await store.record_draft_revision(
        case_id=CASE_ID, review_id=review_id, draft_payload=EDITED, expected_draft_version=1
    )

    with pytest.raises(ReviewVersionMismatchError) as raised:
        await store.upsert_draft_edit(
            case_id=CASE_ID,
            review_id=review_id,
            actor_id="associate-1",
            client_edit_id="c1",
            base_draft_version=1,
            payload=EDITED,
        )
    assert raised.value.field == "base_draft_version"


@pytest.mark.asyncio
async def test_autosave_after_approving_is_a_409_and_the_row_survives(
    store: ReviewAggregateStore,
) -> None:
    """Definition of done: the UI surfaces the transition, and drops no work."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    await store.upsert_draft_edit(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-1",
        client_edit_id="c1",
        base_draft_version=1,
        payload=EDITED,
    )
    await store.submit_edit(case_id=CASE_ID, review_id=review_id, actor_id="associate-1")
    fresh = await store.get_review(case_id=CASE_ID, review_id=review_id)
    await _approve(store, fresh)

    with pytest.raises(ReviewStateError) as raised:
        await store.upsert_draft_edit(
            case_id=CASE_ID,
            review_id=review_id,
            actor_id="associate-1",
            client_edit_id="c2",
            base_draft_version=1,
            payload=DRAFT,
        )
    assert raised.value.state is ReviewState.APPROVING

    kept = await store.get_edit_state(case_id=CASE_ID, review_id=review_id, actor_id="associate-1")
    assert kept is not None and kept["payload"] == EDITED


@pytest.mark.asyncio
async def test_the_draft_edit_row_is_unique_per_review_and_actor(
    store: ReviewAggregateStore, edits: FakeCollection
) -> None:
    declaration = next(
        options
        for _keys, options in edits.index_calls
        if options.get("name") == DRAFT_EDIT_ACTOR_INDEX
    )
    assert declaration["unique"] is True


# --------------------------------------------------------------------------- #
# Conflicts and the canonical edit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_second_actors_edit_raises_the_conflict_marker(
    store: ReviewAggregateStore,
) -> None:
    """The marker is case-scoped, versioned, and says only *that* there is one.

    Private edit contents never reach the shared panel hash; the fact of a
    conflict must, or the second associate's work would vanish silently.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])
    before = await store.conflict_marker(CASE_ID)
    assert before["present"] is False

    for actor in ("associate-1", "associate-2"):
        await store.upsert_draft_edit(
            case_id=CASE_ID,
            review_id=review_id,
            actor_id=actor,
            client_edit_id=f"c-{actor}",
            base_draft_version=1,
            payload=EDITED,
        )

    marker = await store.conflict_marker(CASE_ID)
    assert marker["present"] is True
    assert marker["reviewIds"] == [review_id]
    assert marker["version"] > before["version"]


@pytest.mark.asyncio
async def test_the_canonical_edit_write_clears_the_marker_and_bumps_its_version(
    store: ReviewAggregateStore,
) -> None:
    """Contracts.md sect. 6: cleared *by the canonical-edit write*, nothing else."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    written = []
    for actor in ("associate-1", "associate-2"):
        written.append(
            await store.upsert_draft_edit(
                case_id=CASE_ID,
                review_id=review_id,
                actor_id=actor,
                client_edit_id=f"c-{actor}",
                base_draft_version=1,
                payload=EDITED,
            )
        )
    conflicted = await store.conflict_marker(CASE_ID)

    resolved = await store.resolve_canonical_edit(
        case_id=CASE_ID,
        review_id=review_id,
        resolved_by="associate-1",
        canonical_payload=EDITED,
        resolved_from_actor_edit_ids=[str(row["_id"]) for row in written],
    )

    marker = await store.conflict_marker(CASE_ID)
    assert marker["present"] is False
    assert marker["version"] > conflicted["version"]
    assert resolved["canonicalEditVersion"] == 1
    assert resolved["canonicalEdit"]["canonical_payload"] == EDITED
    assert len(resolved["canonicalEdit"]["resolved_from_actor_edit_ids"]) == 2
    assert resolved["canonicalEdit"]["resolved_by"] == "associate-1"


@pytest.mark.asyncio
async def test_the_marker_and_the_review_flag_are_raised_as_one(
    store: ReviewAggregateStore, reviews: FakeCollection
) -> None:
    """Contracts sect. 6: the pair is one write, and a torn pair is unrecoverable.

    Torn this way -- review flagged, marker clear -- `approve()` refuses with
    `ReviewConflictError` while the panel shows nothing wrong: a 409 with no
    visible cause and nothing an associate can resolve.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])
    await store.upsert_draft_edit(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-1",
        client_edit_id="c1",
        base_draft_version=1,
        payload=EDITED,
    )

    original = store._conflicts.update_one  # noqa: SLF001

    async def marker_write_fails(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("the marker leg failed mid-pair")

    store._conflicts.update_one = marker_write_fails  # type: ignore[method-assign]  # noqa: SLF001
    try:
        with pytest.raises(RuntimeError, match="marker leg"):
            await store.upsert_draft_edit(
                case_id=CASE_ID,
                review_id=review_id,
                actor_id="associate-2",
                client_edit_id="c2",
                base_draft_version=1,
                payload=EDITED,
            )
    finally:
        store._conflicts.update_one = original  # type: ignore[method-assign]  # noqa: SLF001

    # Neither leg committed: the two agree, and they agree on "no conflict".
    assert reviews.documents[review_id]["conflictPresent"] is not True
    assert (await store.conflict_marker(CASE_ID))["present"] is False


@pytest.mark.asyncio
async def test_a_failed_marker_raise_still_leaves_the_conflict_discoverable(
    store: ReviewAggregateStore,
) -> None:
    """The flag pair is transactional; the *edit rows* are the durable truth.

    `_after_edit_written` recomputes the actor set from the edit rows, so a
    pass that aborted still finds the conflict next time somebody writes. This
    is why wrapping the flag/marker pair alone is the right scope: that pair
    cannot heal itself, and the rows can.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])
    for actor, client_edit in (("associate-1", "c1"), ("associate-2", "c2")):
        original = store._conflicts.update_one  # noqa: SLF001

        async def marker_write_fails(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            raise RuntimeError("the marker leg failed mid-pair")

        store._conflicts.update_one = marker_write_fails  # type: ignore[method-assign]  # noqa: SLF001
        try:
            await store.upsert_draft_edit(
                case_id=CASE_ID,
                review_id=review_id,
                actor_id=actor,
                client_edit_id=client_edit,
                base_draft_version=1,
                payload=EDITED,
            )
        except RuntimeError:
            pass
        finally:
            store._conflicts.update_one = original  # type: ignore[method-assign]  # noqa: SLF001

    # A later successful write finds the same two actors and raises the pair.
    await store.upsert_draft_edit(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-2",
        client_edit_id="c3",
        base_draft_version=1,
        payload=DRAFT,
    )
    await store._after_edit_written(CASE_ID, review_id)  # noqa: SLF001

    assert (await store.conflict_marker(CASE_ID))["present"] is True


@pytest.mark.asyncio
async def test_the_marker_and_the_canonical_edit_are_cleared_as_one(
    store: ReviewAggregateStore, reviews: FakeCollection
) -> None:
    """Torn the other way, the panel shows a phantom conflict for ever.

    `conflict_marker()` reads the stored flags rather than recomputing, so
    nothing repairs it: the associate is told to resolve a conflict that no
    longer exists, and resolving again is a no-op against a clean review.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])
    written = []
    for actor in ("associate-1", "associate-2"):
        written.append(
            await store.upsert_draft_edit(
                case_id=CASE_ID,
                review_id=review_id,
                actor_id=actor,
                client_edit_id=f"c-{actor}",
                base_draft_version=1,
                payload=EDITED,
            )
        )
    assert (await store.conflict_marker(CASE_ID))["present"] is True

    original = store._conflicts.update_one  # noqa: SLF001

    async def marker_clear_fails(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("the marker leg failed mid-pair")

    store._conflicts.update_one = marker_clear_fails  # type: ignore[method-assign]  # noqa: SLF001
    try:
        with pytest.raises(RuntimeError, match="marker leg"):
            await store.resolve_canonical_edit(
                case_id=CASE_ID,
                review_id=review_id,
                resolved_by="associate-1",
                canonical_payload=EDITED,
                resolved_from_actor_edit_ids=[str(row["_id"]) for row in written],
            )
    finally:
        store._conflicts.update_one = original  # type: ignore[method-assign]  # noqa: SLF001

    # The canonical edit did not commit either, so the pair still agrees --
    # and, agreeing, it is still resolvable.
    stored = reviews.documents[review_id]
    assert stored["canonicalEditVersion"] == 0
    assert stored["canonicalEdit"] is None
    assert stored["conflictPresent"] is True
    assert (await store.conflict_marker(CASE_ID))["present"] is True

    resolved = await store.resolve_canonical_edit(
        case_id=CASE_ID,
        review_id=review_id,
        resolved_by="associate-1",
        canonical_payload=EDITED,
        resolved_from_actor_edit_ids=[str(row["_id"]) for row in written],
    )
    assert resolved["canonicalEditVersion"] == 1
    assert (await store.conflict_marker(CASE_ID))["present"] is False


@pytest.mark.asyncio
async def test_a_lost_version_cas_clears_nothing(
    store: ReviewAggregateStore, reviews: FakeCollection
) -> None:
    """The abort inside the transaction, pinned.

    The transaction itself is covered above; this covers the mechanism *inside*
    it. If the CAS misses and the code merely reports the mismatch afterwards
    without aborting, the marker clear still commits -- and the marker is then
    clear for a canonical edit that was never written, which is exactly half of
    the tear the transaction exists to prevent, reintroduced from within.

    The race is a cancel landing between this caller's read and its write.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])
    written = []
    for actor in ("associate-1", "associate-2"):
        written.append(
            await store.upsert_draft_edit(
                case_id=CASE_ID,
                review_id=review_id,
                actor_id=actor,
                client_edit_id=f"c-{actor}",
                base_draft_version=1,
                payload=EDITED,
            )
        )
    assert (await store.conflict_marker(CASE_ID))["present"] is True

    # The snapshot this caller read before anything moved.
    stale = await store.get_review(case_id=CASE_ID, review_id=review_id)
    # Somebody cancels the review in the window. The CAS filter names
    # `state: OPEN`, so this caller's write will match nothing.
    await store.cancel(
        case_id=CASE_ID, review_id=review_id, actor_id="associate-2", reason="withdrawn"
    )

    async def stale_read(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return dict(stale)

    original = store.get_review
    store.get_review = stale_read  # type: ignore[method-assign]
    try:
        with pytest.raises(ReviewVersionMismatchError):
            await store.resolve_canonical_edit(
                case_id=CASE_ID,
                review_id=review_id,
                resolved_by="associate-1",
                canonical_payload=EDITED,
                resolved_from_actor_edit_ids=[str(row["_id"]) for row in written],
            )
    finally:
        store.get_review = original  # type: ignore[method-assign]

    # Nothing was written, so nothing was cleared. The conflict is still there
    # to be resolved, and the review still says so.
    assert (await store.conflict_marker(CASE_ID))["present"] is True
    stored = reviews.documents[review_id]
    assert stored["canonicalEdit"] is None
    assert stored["canonicalEditVersion"] == 0
    assert stored["conflictPresent"] is True


@pytest.mark.asyncio
async def test_a_sole_actors_submit_auto_promotes_to_canonical(
    store: ReviewAggregateStore,
) -> None:
    """Nobody to disagree with: the submit *is* the resolution."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    await store.upsert_draft_edit(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-1",
        client_edit_id="c1",
        base_draft_version=1,
        payload=EDITED,
    )

    promoted = await store.submit_edit(case_id=CASE_ID, review_id=review_id, actor_id="associate-1")
    assert promoted["canonicalEditVersion"] == 1
    assert canonical_review_payload(promoted) == EDITED


@pytest.mark.asyncio
async def test_submit_refuses_while_a_second_actor_holds_an_edit(
    store: ReviewAggregateStore,
) -> None:
    review = await _open_review(store)
    review_id = str(review["_id"])
    for actor in ("associate-1", "associate-2"):
        await store.upsert_draft_edit(
            case_id=CASE_ID,
            review_id=review_id,
            actor_id=actor,
            client_edit_id=f"c-{actor}",
            base_draft_version=1,
            payload=EDITED,
        )

    with pytest.raises(ReviewConflictError):
        await store.submit_edit(case_id=CASE_ID, review_id=review_id, actor_id="associate-1")


# --------------------------------------------------------------------------- #
# Approval: the atomic transition
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_approval_freezes_the_payload_and_commits_command_and_outbox_together(
    store: ReviewAggregateStore, commands: FakeCollection, outbox: FakeCollection
) -> None:
    """One transaction: review lock, command record, outbox row.

    The frozen payload is what will be sent -- not what the draft says at send
    time, which is the whole point of freezing it here.
    """
    review = await _open_review(store)
    approved, receipt = await _approve(store, review)

    assert approved["state"] == ReviewState.APPROVING.value
    assert approved["approvedPayload"] == DRAFT
    assert approved["approvingCommandId"] == receipt.command_id
    assert approved["logicalOperationId"] == f"review-delivery:{receipt.command_id}"
    assert approved["deliveryId"]
    assert approved["contentHash"] == canonical_payload_digest(DRAFT)
    assert len(commands.documents) == 1
    assert len(outbox.documents) == 1
    command = next(iter(commands.documents.values()))
    assert command["kind"] == CaseCommandKind.TEMPLATE_APPROVED.value
    assert command["payload"]["review_id"] == str(review["_id"])
    assert command["payload"]["scope_id"] == REQUEST_ID


@pytest.mark.asyncio
async def test_a_reply_review_approves_onto_the_reply_command_kind(
    store: ReviewAggregateStore, commands: FakeCollection
) -> None:
    """Both kinds, one aggregate, two signals (contracts.md sect. 7)."""
    reply = await _open_review(store, kind=ReviewKind.SUPPORT_REPLY, request_id="req-reply")
    await _approve(store, reply)

    command = next(iter(commands.documents.values()))
    assert command["kind"] == CaseCommandKind.REPLY_APPROVED.value


@pytest.mark.asyncio
async def test_a_failed_approval_leaves_neither_command_nor_outbox_row(
    store: ReviewAggregateStore,
    reviews: FakeCollection,
    commands: FakeCollection,
    outbox: FakeCollection,
) -> None:
    """Definition of done: the transaction aborts as one thing.

    The review is moved out from under the approval between its read and its
    lock; the command must not survive that, or the workflow would be signalled
    to send a payload the review never froze.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])
    original_lock = store._reviews.find_one_and_update  # noqa: SLF001

    async def lock_that_loses(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    store._reviews.find_one_and_update = lock_that_loses  # type: ignore[method-assign]  # noqa: SLF001
    try:
        with pytest.raises((ReviewStateError, ReviewVersionMismatchError)):
            await _approve(store, review)
    finally:
        store._reviews.find_one_and_update = original_lock  # type: ignore[method-assign]  # noqa: SLF001

    assert commands.documents == {}
    assert outbox.documents == {}
    assert reviews.documents[review_id]["state"] == ReviewState.OPEN.value


@pytest.mark.asyncio
async def test_a_failing_outbox_leg_rolls_the_locked_review_back_to_open(
    store: ReviewAggregateStore,
    reviews: FakeCollection,
    commands: FakeCollection,
    outbox: FakeCollection,
) -> None:
    """The other direction of the approval transaction.

    The lock-loses direction is covered above. This is the one that matters if
    somebody ever moves the command/outbox insert back outside the transaction:
    the review would be left in `APPROVING` -- locked, frozen, uneditable --
    with no command ever dispatched and no path back, and the review would wait
    on a send that nobody was ever asked to make.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])
    original = outbox.insert_one

    async def outbox_leg_fails(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("the outbox leg failed after the review was locked")

    outbox.insert_one = outbox_leg_fails  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="outbox leg"):
            await _approve(store, review)
    finally:
        outbox.insert_one = original  # type: ignore[method-assign]

    stored = reviews.documents[review_id]
    assert stored["state"] == ReviewState.OPEN.value
    assert stored["approvedPayload"] is None
    assert stored["deliveryId"] is None
    assert stored["approvingCommandId"] is None
    assert commands.documents == {}
    assert outbox.documents == {}

    # And the review is still approvable, which is the point of rolling back.
    approved, _ = await _approve(store, review)
    assert approved["state"] == ReviewState.APPROVING.value


@pytest.mark.asyncio
async def test_approval_refuses_a_stale_draft_version(store: ReviewAggregateStore) -> None:
    review = await _open_review(store)

    with pytest.raises(ReviewVersionMismatchError) as raised:
        await _approve(store, review, expected_draft_version=99)
    assert raised.value.field == "draft_version"


@pytest.mark.asyncio
async def test_approval_refuses_a_stale_canonical_edit_version(
    store: ReviewAggregateStore,
) -> None:
    review = await _open_review(store)

    with pytest.raises(ReviewVersionMismatchError) as raised:
        await _approve(store, review, expected_canonical_edit_version=7)
    assert raised.value.field == "canonical_edit_version"


@pytest.mark.asyncio
async def test_approval_refuses_a_hash_of_bytes_the_store_does_not_hold(
    store: ReviewAggregateStore,
) -> None:
    """The approver saw a payload. If it is not this payload, they did not approve it."""
    review = await _open_review(store)

    with pytest.raises(ApprovedPayloadHashMismatchError):
        await _approve(store, review, canonical_approved_payload_hash="f" * 64)


@pytest.mark.asyncio
async def test_approval_refuses_a_pending_revision(store: ReviewAggregateStore) -> None:
    """A revision was asked for; approving now would approve the old draft."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    await store.request_revision(case_id=CASE_ID, review_id=review_id, actor_id="associate-1")
    fresh = await store.get_review(case_id=CASE_ID, review_id=review_id)

    with pytest.raises(PendingRevisionError):
        await _approve(store, fresh)


@pytest.mark.asyncio
async def test_approval_refuses_an_unresolved_conflict(
    store: ReviewAggregateStore,
) -> None:
    review = await _open_review(store)
    review_id = str(review["_id"])
    for actor in ("associate-1", "associate-2"):
        await store.upsert_draft_edit(
            case_id=CASE_ID,
            review_id=review_id,
            actor_id=actor,
            client_edit_id=f"c-{actor}",
            base_draft_version=1,
            payload=EDITED,
        )
    fresh = await store.get_review(case_id=CASE_ID, review_id=review_id)

    with pytest.raises(ReviewConflictError):
        await _approve(store, fresh)


# --------------------------------------------------------------------------- #
# An empty reply body is not approvable
# --------------------------------------------------------------------------- #
#
# The defect was reported as two panes disagreeing about how to describe an
# empty reply draft. The copy divergence is the symptom; the defect is that the
# draft was approvable, so an associate could approve nothing, the send path
# would post nothing, and Support would receive nothing -- with a delivery
# receipt saying it went fine.
#
# The refusal lives on `OPEN -> APPROVING` because that is the one transition
# both an associate's approval and `auto_send` (`actor=SYSTEM`) pass through,
# and because sect. 6 already refuses unresolved conflicts and pending
# revisions there for the same reason: a review that cannot be acted on must
# not be approvable.


@pytest.mark.parametrize(
    ("body", "why"),
    [
        pytest.param("", "born empty", id="empty-string"),
        pytest.param("   \n\t ", "whitespace only", id="whitespace"),
        pytest.param(None, "explicitly null", id="null"),
    ],
)
@pytest.mark.asyncio
async def test_approval_refuses_an_empty_reply_body(
    store: ReviewAggregateStore,
    commands: FakeCollection,
    reviews: FakeCollection,
    body: str | None,
    why: str,
) -> None:
    """Every shape of "nothing to send" refuses, not only the empty string.

    Whitespace counts because a blank message is delivered and read as an agent
    with nothing to say, which is the outcome the refusal exists to prevent.
    """
    payload: dict[str, Any] = dict(REPLY_DRAFT)
    payload["messageText"] = body
    reply = await _open_review(
        store,
        kind=ReviewKind.SUPPORT_REPLY,
        request_id="req-reply",
        draft_payload=payload,
    )

    with pytest.raises(EmptyReplyBodyError) as raised:
        await _approve(store, reply)

    # The reason travels, so the 409 can say *why* rather than only "no".
    assert raised.value.gap_reason == EMPTY_REPLY_BODY_GAP_REASON, why
    # Nothing was written. A refusal that still planned a command would leave
    # the workflow a signal to send the message this transition just refused.
    assert commands.documents == {}
    stored = reviews.documents[str(reply["_id"])]
    assert stored["state"] == ReviewState.OPEN.value


@pytest.mark.asyncio
async def test_a_missing_message_text_key_refuses_too(store: ReviewAggregateStore) -> None:
    """Absent and empty are the same answer here: neither can be delivered.

    Distinguished from the parametrised cases above because a payload with no
    `messageText` at all is the shape a *different* producer would write, and
    an `isinstance` check that only handled `None` would let it through.
    """
    reply = await _open_review(
        store,
        kind=ReviewKind.SUPPORT_REPLY,
        request_id="req-reply",
        draft_payload={"supportEventId": "evt-1", "intent": "rma_issued"},
    )

    with pytest.raises(EmptyReplyBodyError):
        await _approve(store, reply)


@pytest.mark.asyncio
async def test_a_non_empty_reply_still_approves(
    store: ReviewAggregateStore, commands: FakeCollection
) -> None:
    """The other direction. A guard that refused everything would pass the test
    above and break the feature, so the refusal has to be shown to be
    conditional on the thing it claims to be conditional on."""
    reply = await _open_review(store, kind=ReviewKind.SUPPORT_REPLY, request_id="req-reply")

    approved, _ = await _approve(store, reply)

    assert approved["state"] == ReviewState.APPROVING.value
    command = next(iter(commands.documents.values()))
    assert command["kind"] == CaseCommandKind.REPLY_APPROVED.value


@pytest.mark.asyncio
async def test_an_empty_template_body_still_approves(
    store: ReviewAggregateStore, commands: FakeCollection
) -> None:
    """The refusal is reply-kind only, and that is deliberate rather than an
    oversight. A `TEMPLATE` review's emptiness is already governed by
    `TemplateGap` on the fields it is rendered from; `messageText` is not part
    of its payload shape at all, so reading one here would refuse every
    template ever approved."""
    template = await _open_review(store, draft_payload={"subject": "Return 9100", "sections": []})

    approved, _ = await _approve(store, template)

    assert approved["state"] == ReviewState.APPROVING.value
    command = next(iter(commands.documents.values()))
    assert command["kind"] == CaseCommandKind.TEMPLATE_APPROVED.value


@pytest.mark.asyncio
async def test_a_canonical_edit_that_empties_the_body_refuses(
    store: ReviewAggregateStore,
) -> None:
    """The check reads the *frozen* payload, not the draft.

    This is the case a draft-only check would miss entirely, and it is the one
    an associate can actually cause: the reply was composed with text, somebody
    cleared the box, and the canonical edit -- which is what approval freezes
    and sends -- is empty while `draftPayload` still reads fine.
    """
    reply = await _open_review(store, kind=ReviewKind.SUPPORT_REPLY, request_id="req-reply")
    review_id = str(reply["_id"])
    emptied = dict(REPLY_DRAFT)
    emptied["messageText"] = ""
    await store.upsert_draft_edit(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-1",
        client_edit_id="c-1",
        base_draft_version=1,
        payload=emptied,
    )
    # An autosave is not a decision: `upsert_draft_edit` stores the row, and it
    # is `submit_edit` that promotes a sole actor's edit to the canonical one.
    # (Asserted below rather than assumed -- the first draft of this test
    # skipped the submit and passed the *untouched* draft to approval, where it
    # would have proved nothing.)
    await store.submit_edit(case_id=CASE_ID, review_id=review_id, actor_id="associate-1")
    fresh = await store.get_review(case_id=CASE_ID, review_id=review_id)
    # Guard the guard: the canonical payload really is the emptied one, and the
    # draft really is not, so the refusal below can only come from the frozen
    # payload.
    assert canonical_review_payload(fresh)["messageText"] == ""
    assert fresh["draftPayload"]["messageText"] == REPLY_DRAFT["messageText"]

    with pytest.raises(EmptyReplyBodyError):
        await _approve(store, fresh)


@pytest.mark.asyncio
async def test_the_system_actor_is_refused_by_the_same_condition(
    store: ReviewAggregateStore,
) -> None:
    """`auto_send` is this transition with `actor=SYSTEM`, and sect. 6 says it
    is refused by exactly the same rejections. An empty body that held a person
    and let the platform through would be the worst of both."""
    payload = dict(REPLY_DRAFT)
    payload["messageText"] = ""
    reply = await _open_review(
        store,
        kind=ReviewKind.SUPPORT_REPLY,
        request_id="req-reply",
        draft_payload=payload,
    )

    with pytest.raises(EmptyReplyBodyError):
        await _approve(store, reply, actor_id=SYSTEM_ACTOR, allow_system=True)


@pytest.mark.asyncio
async def test_the_re_rendered_revision_clears_the_flag_and_moves_the_draft_version(
    store: ReviewAggregateStore,
) -> None:
    review = await _open_review(store)
    review_id = str(review["_id"])
    await store.request_revision(case_id=CASE_ID, review_id=review_id, actor_id="associate-1")

    rerendered = await store.record_draft_revision(
        case_id=CASE_ID,
        review_id=review_id,
        draft_payload=EDITED,
        expected_draft_version=1,
    )
    assert rerendered["pendingRevision"] is False
    assert rerendered["draftVersion"] == 2
    assert rerendered["draftPayload"] == EDITED

    approved, _ = await _approve(store, rerendered)
    assert approved["state"] == ReviewState.APPROVING.value


@pytest.mark.asyncio
async def test_a_replayed_approval_signal_is_the_same_command_not_a_second(
    store: ReviewAggregateStore, commands: FakeCollection
) -> None:
    """Dedupe on `signal_id`: the retried approval must not send twice.

    The state guard normally stops a replay first; here the review is put back
    to `OPEN` behind the store's back so the replay reaches the command
    identity underneath -- the layer that has to hold when the guard cannot,
    because two API workers can be past it at the same instant.
    """
    review = await _open_review(store)
    _, first = await _approve(store, review)
    await store._reviews.update_one(  # noqa: SLF001
        {"_id": str(review["_id"])}, {"$set": {"state": ReviewState.OPEN.value}}
    )
    fresh = await store.get_review(case_id=CASE_ID, review_id=str(review["_id"]))

    _, replay = await _approve(store, fresh, signal_id="sig-approve-1")

    assert replay.duplicate is True
    assert replay.command_id == first.command_id
    assert len(commands.documents) == 1


@pytest.mark.asyncio
async def test_a_second_approval_of_the_same_frozen_versions_is_a_409(
    store: ReviewAggregateStore,
) -> None:
    """The frozen CAS key, enforced by the database, not by whoever read last.

    A different `signal_id` claiming the same `(review, draft_version,
    canonical_edit_version)` slot is a second approval of one decision.
    """
    review = await _open_review(store)
    await _approve(store, review)
    # Put the review back to OPEN behind the store's back so the second
    # approval reaches the command CAS rather than stopping at the state guard.
    await store._reviews.update_one(  # noqa: SLF001
        {"_id": str(review["_id"])}, {"$set": {"state": ReviewState.OPEN.value}}
    )
    fresh = await store.get_review(case_id=CASE_ID, review_id=str(review["_id"]))

    with pytest.raises(StaleReviewVersionError):
        await _approve(store, fresh, signal_id="sig-approve-2")


@pytest.mark.asyncio
async def test_edit_cancel_and_redraft_all_refuse_from_approving_on(
    store: ReviewAggregateStore,
) -> None:
    """Contracts.md sect. 6: from `APPROVING` on, these are 409s."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    await _approve(store, review)

    for call in (
        store.cancel(case_id=CASE_ID, review_id=review_id, actor_id="associate-1", reason="late"),
        store.request_revision(case_id=CASE_ID, review_id=review_id, actor_id="associate-1"),
        store.redraft(
            case_id=CASE_ID,
            review_id=review_id,
            actor_id="associate-1",
            draft_payload=EDITED,
        ),
    ):
        with pytest.raises(ReviewStateError) as raised:
            await call
        assert raised.value.state is ReviewState.APPROVING


# --------------------------------------------------------------------------- #
# The rest of the frozen transition table
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_open_cancels(store: ReviewAggregateStore) -> None:
    review = await _open_review(store)
    cancelled = await store.cancel(
        case_id=CASE_ID,
        review_id=str(review["_id"]),
        actor_id="associate-1",
        reason="customer withdrew",
    )
    assert cancelled["state"] == ReviewState.CANCELLED.value


@pytest.mark.asyncio
async def test_approving_reaches_sent(store: ReviewAggregateStore) -> None:
    """Including a retry the receiver deduped: absorption is delivery."""
    review = await _open_review(store)
    await _approve(store, review)
    sent = await store.mark_sent(case_id=CASE_ID, review_id=str(review["_id"]))
    assert sent["state"] == ReviewState.SENT.value


@pytest.mark.asyncio
async def test_approving_reaches_delivery_failed_and_then_abandoned(
    store: ReviewAggregateStore,
) -> None:
    """The first `ABANDONED` arrow, audited."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    await _approve(store, review)
    failed = await store.mark_delivery_failed(
        case_id=CASE_ID, review_id=review_id, error_code="TRANSPORT_REFUSED"
    )
    assert failed["state"] == ReviewState.DELIVERY_FAILED.value
    assert failed["lastDeliveryErrorCode"] == "TRANSPORT_REFUSED"

    abandoned = await store.abandon(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="operator-1",
        reason="carrier will never accept this",
    )
    assert abandoned["state"] == ReviewState.ABANDONED.value
    assert abandoned["abandonAudit"]["actorId"] == "operator-1"
    assert abandoned["abandonAudit"]["reason"] == "carrier will never accept this"
    assert abandoned["abandonAudit"]["at"] is not None


@pytest.mark.asyncio
async def test_held_for_operations_returns_to_open_or_is_abandoned(
    store: ReviewAggregateStore,
) -> None:
    """Both arrows out of the hold, and the versions survive the round trip."""
    review = await _open_review(store)
    review_id = str(review["_id"])
    await _approve(store, review)
    held = await store.hold_for_operations(
        case_id=CASE_ID,
        review_id=review_id,
        reason=TemplateReviewParkReason.TEMPLATE_REVIEW_GUARD_BLOCKED,
    )
    assert held["state"] == ReviewState.HELD_FOR_OPERATIONS.value
    assert held["holdReason"] == "TEMPLATE_REVIEW_GUARD_BLOCKED"

    reopened = await store.resume_from_hold(
        case_id=CASE_ID, review_id=review_id, actor_id="operator-1"
    )
    assert reopened["state"] == ReviewState.OPEN.value
    assert reopened["holdReason"] is None
    assert reopened["draftVersion"] == review["draftVersion"]

    # Second arrow out of the hold: abandoned.
    await store._reviews.update_one(  # noqa: SLF001
        {"_id": review_id},
        {"$set": {"state": ReviewState.HELD_FOR_OPERATIONS.value}},
    )
    abandoned = await store.abandon(
        case_id=CASE_ID, review_id=review_id, actor_id="operator-1", reason="stale"
    )
    assert abandoned["state"] == ReviewState.ABANDONED.value


@pytest.mark.asyncio
async def test_abandoned_is_terminal(store: ReviewAggregateStore) -> None:
    review = await _open_review(store)
    review_id = str(review["_id"])
    await _approve(store, review)
    await store.mark_delivery_failed(case_id=CASE_ID, review_id=review_id, error_code="X")
    await store.abandon(case_id=CASE_ID, review_id=review_id, actor_id="operator-1", reason="done")

    with pytest.raises(ReviewStateError):
        await store.mark_sent(case_id=CASE_ID, review_id=review_id)
    with pytest.raises(ReviewStateError):
        await store.abandon(
            case_id=CASE_ID, review_id=review_id, actor_id="operator-1", reason="again"
        )


@pytest.mark.asyncio
async def test_an_open_review_cannot_be_marked_sent(store: ReviewAggregateStore) -> None:
    """Everything outside the frozen table is a 409, including the tempting ones."""
    review = await _open_review(store)
    with pytest.raises(ReviewStateError):
        await store.mark_sent(case_id=CASE_ID, review_id=str(review["_id"]))


# --------------------------------------------------------------------------- #
# Recovery: retry keeps the delivery identity
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_retry_reuses_the_whole_delivery_identity(
    store: ReviewAggregateStore, commands: FakeCollection
) -> None:
    """Contracts.md sect. 6-7: the same logical operation, the same delivery.

    A retry that mints a new `delivery_id` is a second message wearing the
    first one's name -- exactly what receiver dedupe is there to prevent, and
    it can only prevent it if the id does not move.
    """
    review = await _open_review(store)
    review_id = str(review["_id"])
    approved, _ = await _approve(store, review)
    await store.mark_delivery_failed(case_id=CASE_ID, review_id=review_id, error_code="TIMEOUT")

    retried, receipt = await store.retry_delivery(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="operator-1",
        workflow_id=WORKFLOW_ID,
        signal_id="sig-retry-1",
    )

    assert retried["state"] == ReviewState.APPROVING.value
    assert retried["deliveryId"] == approved["deliveryId"]
    assert retried["logicalOperationId"] == approved["logicalOperationId"]
    assert retried["contentHash"] == approved["contentHash"]
    assert retried["approvedPayload"] == approved["approvedPayload"]

    command = commands.documents[receipt.command_id]
    assert command["kind"] == CaseCommandKind.REVIEW_DELIVERY_RETRY.value
    assert command["payload"]["delivery_id"] == approved["deliveryId"]
    assert command["payload"]["logical_operation_id"] == approved["logicalOperationId"]

    sent = await store.mark_sent(case_id=CASE_ID, review_id=review_id)
    assert sent["state"] == ReviewState.SENT.value


@pytest.mark.asyncio
async def test_a_replayed_retry_signal_is_the_same_command(
    store: ReviewAggregateStore, commands: FakeCollection
) -> None:
    review = await _open_review(store)
    review_id = str(review["_id"])
    await _approve(store, review)
    await store.mark_delivery_failed(case_id=CASE_ID, review_id=review_id, error_code="T")
    _, first = await store.retry_delivery(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="operator-1",
        workflow_id=WORKFLOW_ID,
        signal_id="sig-retry-1",
    )
    # Back to DELIVERY_FAILED, then the same retry arrives again.
    await store.mark_delivery_failed(case_id=CASE_ID, review_id=review_id, error_code="T")

    _, replay = await store.retry_delivery(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="operator-1",
        workflow_id=WORKFLOW_ID,
        signal_id="sig-retry-1",
    )

    assert replay.duplicate is True
    assert replay.command_id == first.command_id
    assert len(commands.documents) == 2  # the approval and the one retry


@pytest.mark.asyncio
async def test_retry_refuses_from_any_state_but_delivery_failed(
    store: ReviewAggregateStore,
) -> None:
    review = await _open_review(store)
    with pytest.raises(ReviewStateError):
        await store.retry_delivery(
            case_id=CASE_ID,
            review_id=str(review["_id"]),
            actor_id="operator-1",
            workflow_id=WORKFLOW_ID,
            signal_id="sig-retry-1",
        )


# --------------------------------------------------------------------------- #
# Redraft
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_redraft_cancels_the_open_attempt_and_mints_a_new_one_in_scope(
    store: ReviewAggregateStore, reviews: FakeCollection
) -> None:
    """A new attempt under the same `(case_id, request_id)` scope."""
    first = await _open_review(store)
    second = await store.redraft(
        case_id=CASE_ID,
        review_id=str(first["_id"]),
        actor_id="associate-1",
        draft_payload=EDITED,
    )

    assert second["_id"] != first["_id"]
    assert second["scopeId"] == first["scopeId"]
    assert second["requestId"] == first["requestId"]
    assert second["state"] == ReviewState.OPEN.value
    assert second["draftPayload"] == EDITED
    assert reviews.documents[str(first["_id"])]["state"] == ReviewState.CANCELLED.value
    assert len(reviews.documents) == 2


@pytest.mark.asyncio
async def test_redraft_after_a_terminal_attempt_needs_no_cancel(
    store: ReviewAggregateStore,
) -> None:
    first = await _open_review(store)
    review_id = str(first["_id"])
    await store.cancel(
        case_id=CASE_ID, review_id=review_id, actor_id="associate-1", reason="withdrawn"
    )

    second = await store.redraft(
        case_id=CASE_ID,
        review_id=review_id,
        actor_id="associate-1",
        draft_payload=EDITED,
    )
    assert second["state"] == ReviewState.OPEN.value
