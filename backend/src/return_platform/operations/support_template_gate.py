"""The review gate's work, off the workflow thread (contracts.md sect. 6).

The workflow decides *when*; this decides *what*. Four operations, and they are
the four activities the contract names:

* **`record_draft`** renders the template for one support request and opens the
  review over it. Idempotent by construction on two levels -- the caller
  supplies a replay-stable `review_id`, and `create_review` itself returns the
  live attempt rather than minting a second.
* **`record_revision`** puts a reviewer's "do this again" on the fact log.
* **`rerender_draft`** produces the new draft and moves `draft_version`.
* **`deliver_approved`** takes the payload approval froze, posts it on the
  case's one support thread under the stored delivery identity, and moves the
  review to `SENT` or `DELIVERY_FAILED`.

**Everything durable goes through S2's stores.** This module owns no
collection, no index and no transition table; it composes text and calls
`ReviewAggregateStore`. That is the sect. 10 boundary, and it is why a missing
capability here becomes a question for S2 rather than a second write path.

---

**Human-authored text and the outbound message (carry-forward condition 7).**

Composition neutralises exactly four values that a person typed --
`associate_notes` and the three `contact_*` -- through
`support_handoff._safe`, whose `_FRAMING` regex turns a section-heading-shaped
line into `[removed]`. Phase 1 found the cost of dropping that: a note reading
`BAY ASSIGNMENT:` reached the rendered handoff intact and restructured the
message for whoever read it next.

The gate adds a genuinely new path -- a reviewer's *edit* becomes the sent
text -- and it is two paths wearing one name, which is the whole of the rule
here:

* A **field edit** replaces one value inside an agent-authored frame. That is
  structurally identical to `associate_notes`: the reader cannot tell the
  frame from the value, so the value is neutralised. `neutralise_field_edits`
  is applied by the *API* when it builds a canonical payload, and again here
  before the text is composed, because the belt is cheap and the braces are the
  thing that ships.
* A **body edit** replaces the whole message. There is no frame left to
  impersonate: the reviewer is looking at the text they are about to send and
  every line of it is theirs. Neutralising it would delete their own headings
  and would make the review surface lie about what Support will receive.

The distinction is recorded rather than assumed, and
`tests/operations/test_support_template_gate.py` pins both halves.

---

**Approval recomputes who is editing (carry-forward conditions 5a and 8).**

S2's conflict flag is written by `_after_edit_written` *after* the edit row is
inserted, in a second transaction. RV upheld that -- `submit_edit` recomputes
the actor set live from the rows, so auto-promote is guarded by the rows rather
than by the flag -- and narrowed what survives to exactly one path: process
death in the insert-to-flag window, no later autosave, then a **direct
`approve()`**. In that state two associates hold unsubmitted edits, both flags
say "no conflict", and `approve()` freezes `draftPayload` -- the *agent's*
draft -- discarding both edits with no 409 and nothing on screen to explain it.

That path is V1's approval, and both callers of it are here: the endpoint and
`auto_send`. So approval recomputes the actor set from the rows the same way
`submit_edit` does, and refuses with S2's own `ReviewConflictError` -- the same
409 and the same UI affordance -- when several actors hold edits that no
canonical edit covers. It is one read on a path that already does several, and
it cannot be retrofitted once the endpoint has shipped.

`DraftEditRowsPort` is **required**, not defaulted. A missing port would make
the guard silently absent, which is the failure this whole paragraph exists to
prevent, and the platform's own `NotImplementedError`-stub advisory says the
same thing from the other direction: a port that is present but does not work
is worse than one that is absent loudly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol, cast

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.support_gate_configuration import (
    RequestGrouping,
    SupportGateConfiguration,
)
from return_platform.operations import fact_names
from return_platform.operations.case_commands import CaseCommandReceipt
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.review_aggregate import (
    REVIEW_DRAFT_EDITS,
    ReviewAggregateStore,
    ReviewConflictError,
    ReviewKind,
    ReviewNotFoundError,
    ReviewState,
    ReviewStateError,
    canonical_review_payload,
)
from return_platform.operations.support_events import canonical_payload_digest
from return_platform.operations.support_handoff import _safe
from return_platform.operations.support_template_renderer import (
    RenderedTemplate,
    SupportTemplateGraphPort,
    TemplateDraftInput,
    TemplateNotConfiguredError,
    TemplateRenderContext,
    render_support_template,
)

logger = logging.getLogger("return_platform.operations.support_template_gate")

__all__ = [
    "DeliveryOutcome",
    "DraftEditRowsPort",
    "GateDraft",
    "MongoDraftEditRows",
    "SupportTemplateGateService",
    "neutralise_field_edits",
    "payload_of",
    "payload_text",
    "request_ids_for",
    "unresolved_edit_actors",
]

#: The payload keys `approve()` hashes and the panel renders. Named because
#: three modules build one of these and a typo in any of them is a message with
#: a missing section that still passes its own hash check.
#: Which software wrote a gate fact. `agent_id` is required by
#: `append_scoped_case_fact` and answers a different question from the actor:
#: this is the component, the actor is the person.
_GATE_AGENT_ID: Final = "support-template-gate"

PAYLOAD_TEMPLATE_ID: Final = "template_id"
PAYLOAD_VARIANT_ID: Final = "variant_id"
PAYLOAD_SUBJECT: Final = "subject"
PAYLOAD_TEXT: Final = "text"
PAYLOAD_SECTIONS: Final = "sections"
PAYLOAD_GAPS: Final = "gaps"
#: Set by the API when a reviewer replaced the whole body rather than editing
#: fields. See the module docstring: this is the one value that is *not*
#: neutralised, and it is a distinct key so that fact is visible in the stored
#: payload rather than inferred from a diff.
PAYLOAD_BODY_OVERRIDE: Final = "body_override"


# --------------------------------------------------------------------------- #
# Payload shape
# --------------------------------------------------------------------------- #


def payload_of(rendered: RenderedTemplate) -> dict[str, Any]:
    """A `RenderedTemplate` as the JSON the review aggregate stores.

    Plain dicts rather than the frozen dataclasses because this is what gets
    hashed, stored, sent over the wire and edited -- and a shape that only
    round-trips through Python is not the shape the panel reads.
    """
    return {
        PAYLOAD_TEMPLATE_ID: rendered.template_id,
        PAYLOAD_VARIANT_ID: rendered.variant_id,
        PAYLOAD_SUBJECT: rendered.subject,
        PAYLOAD_TEXT: rendered.text,
        PAYLOAD_SECTIONS: [
            {
                "section_id": section.section_id,
                "title": section.title,
                "return_record_id": section.return_record_id,
                "fields": [
                    {
                        "field_id": item.field_id,
                        "label": item.label,
                        "value": item.value,
                        "source": item.source,
                        "source_path": item.source_path,
                        "fact_id": item.fact_id,
                        "applied_fallback": item.applied_fallback,
                    }
                    for item in section.fields
                ],
            }
            for section in rendered.sections
        ],
        PAYLOAD_GAPS: [{"field_id": gap.field_id, "reason": gap.reason} for gap in rendered.gaps],
    }


def payload_text(payload: Mapping[str, Any]) -> str:
    """The body Support will read, composed from the payload's own sections.

    Deliberately *not* `payload["text"]`. That value is what the renderer
    produced; this is what the payload currently says, and after a field edit
    those differ. Recomposing is the only reading under which "the text the
    reviewer approved" and "the text that is sent" are the same string.

    A `body_override` short-circuits it: the reviewer replaced the message.

    The algorithm is the renderer's `_text_of`, and
    `test_the_gate_composes_the_same_body_the_renderer_did` pins the two
    against each other on the shipped default variant -- a duplicated
    formatter that drifted would send Support a differently-shaped message
    than the one the preview screen shows.
    """
    override = payload.get(PAYLOAD_BODY_OVERRIDE)
    if isinstance(override, str) and override.strip():
        return override
    blocks: list[str] = []
    for section in cast(Sequence[Mapping[str, Any]], payload.get(PAYLOAD_SECTIONS) or ()):
        lines: list[str] = []
        title = section.get("title")
        if title is not None:
            lines.append(str(title))
        for item in cast(Sequence[Mapping[str, Any]], section.get("fields") or ()):
            value = str(item.get("value", ""))
            label = item.get("label")
            if label is not None:
                lines.append(f"- {label}: {value}")
            else:
                lines.extend(value.splitlines() or [""])
        if lines:
            blocks.append("\n".join(lines))
    return ("\n\n".join(blocks)).rstrip() + "\n"


def neutralise_field_edits(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Every field value put through composition's own neutralisation.

    Carry-forward condition 7. `_safe` is `support_handoff`'s, imported rather
    than reimplemented: two regexes for one rule is how the rule comes to have
    two meanings. `support_template_draft.py` already imports it the same way,
    so this is the established seam and not a new reach into a private name.

    The `body_override` is untouched -- see the module docstring for why that
    is the rule rather than an omission.
    """
    sections: list[dict[str, Any]] = []
    for section in cast(Sequence[Mapping[str, Any]], payload.get(PAYLOAD_SECTIONS) or ()):
        fields: list[dict[str, Any]] = []
        for item in cast(Sequence[Mapping[str, Any]], section.get("fields") or ()):
            copied = dict(item)
            copied["value"] = _safe(item.get("value")) or ""
            fields.append(copied)
        replaced = dict(section)
        replaced["fields"] = fields
        sections.append(replaced)
    result = dict(payload)
    result[PAYLOAD_SECTIONS] = sections
    result[PAYLOAD_TEXT] = payload_text(result)
    return result


def request_ids_for(
    case_id: str, records: Sequence[Any], grouping: RequestGrouping
) -> tuple[str, ...]:
    """Which support requests this case produces, in a stable order.

    `one_per_case` is the shipped rule and the only one that needs no record
    attribute at all, which is why it is also the fallback when a case has no
    records yet -- the handoff that *asks* for the first RMA cannot be grouped
    by a shipping mode nobody has issued.

    Stable order matters beyond tidiness: the workflow's wait map is keyed on
    these ids and its reminder text lists them, so an unstable order would give
    an associate a differently-worded reminder every two hours.
    """
    if grouping is RequestGrouping.ONE_PER_CASE or not records:
        return (f"support:{case_id}",)
    attribute = "returnMethod" if grouping is RequestGrouping.BY_SHIPPING_MODE else "returnLocation"
    keys: list[str] = []
    for record in records:
        raw = getattr(record, attribute, None)
        if raw is None and isinstance(record, Mapping):
            raw = record.get(attribute)
        key = str(raw) if raw is not None else "unspecified"
        if key not in keys:
            keys.append(key)
    return tuple(f"support:{case_id}:{key}" for key in sorted(keys))


# --------------------------------------------------------------------------- #
# Who is editing, recomputed (conditions 5a and 8)
# --------------------------------------------------------------------------- #


class DraftEditRowsPort(Protocol):
    """One review's per-actor edit rows, read live.

    A read-only port over S2's draft-edit store. Deliberately *not* a second
    write path and deliberately not optional -- see the module docstring.
    """

    async def edit_rows(self, *, review_id: str) -> Sequence[Mapping[str, Any]]: ...


class MongoDraftEditRows:
    """The shipped `DraftEditRowsPort`, over S2's own collection.

    The collection name comes from S2's exported `REVIEW_DRAFT_EDITS` rather
    than a literal, and the two field names this reads -- `_id` and `actorId`
    -- are pinned by `test_the_recompute_reads_what_upsert_draft_edit_writes`,
    which runs S2's *writer* and this reader against one store. A rename in S2
    then breaks that test rather than silently emptying the guard.
    """

    def __init__(self, database: Any) -> None:
        self._edits = database[REVIEW_DRAFT_EDITS]

    async def edit_rows(self, *, review_id: str) -> Sequence[Mapping[str, Any]]:
        return [dict(document) async for document in self._edits.find({"reviewId": review_id})]


def unresolved_edit_actors(
    rows: Sequence[Mapping[str, Any]], review: Mapping[str, Any]
) -> frozenset[str]:
    """Actors holding an edit that no canonical edit accounts for.

    Empty in the three states approval must not refuse: nobody edited, one
    actor edited (their submit auto-promotes, and an unsubmitted sole edit is
    not a *conflict* -- it is a draft they chose not to send), and several
    actors edited but a canonical edit resolved every row.

    Non-empty in the one state approval must refuse and the flag can miss:
    several actors hold rows, and the canonical edit -- if there is one at all
    -- does not cover them.

    **A row is covered two ways, and the second one is not decoration.** Named
    in `resolved_from_actor_edit_ids`, or written *before* the canonical edit
    was resolved. Contracts.md sect. 6 offers three resolutions -- select, merge
    and **discard** -- and a discard names no row at all: the canonical payload
    came from nobody's draft, which is a real resolution and a deliberate one.
    Coverage by the id list alone made every discarded conflict permanently
    unapprovable: the marker cleared, the panel said "resolved", and this
    recompute refused the approval on rows the resolution had already answered.
    Recency is the relation the resolution actually establishes; the id list
    says which drafts it was *made from*, which is an audit question.

    Timestamps are used only when both sides have one. A row or a canonical edit
    written before either carried one falls back to the id list rather than
    being silently treated as stale.
    """
    actors = {str(row["actorId"]) for row in rows if row.get("actorId") is not None}
    if len(actors) < 2:
        return frozenset()
    canonical = review.get("canonicalEdit")
    covered: set[str] = set()
    resolved_at: Any = None
    if isinstance(canonical, Mapping):
        covered = {
            str(item)
            for item in cast(Sequence[Any], canonical.get("resolved_from_actor_edit_ids") or ())
        }
        resolved_at = canonical.get("resolved_at")
    outstanding = {
        str(row["actorId"])
        for row in rows
        if str(row.get("_id", "")) not in covered
        and not _answered_by(row.get("updatedAt"), resolved_at)
    }
    return frozenset(outstanding) if len(outstanding) > 1 else frozenset()


def _answered_by(edited_at: Any, resolved_at: Any) -> bool:
    """Whether a resolution at `resolved_at` already accounts for this row."""
    if not isinstance(edited_at, datetime) or not isinstance(resolved_at, datetime):
        return False
    return edited_at <= resolved_at


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class GateDraft:
    """One review, as the workflow needs to see it.

    `template_available` is `False` for a release with no variants -- the
    defaulted, pre-template state. The workflow then takes the composed path,
    which is exactly what an un-patched history does, so a deployment that has
    not published a template is not a deployment whose cases park.
    """

    request_id: str
    review_id: str | None
    state: str
    draft_version: int
    canonical_edit_version: int
    gap_field_ids: tuple[str, ...] = ()
    subject: str = ""
    text: str = ""
    template_available: bool = True

    @property
    def blocked_by_gap(self) -> bool:
        return bool(self.gap_field_ids)


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """What one send came to. `absorbed` is a success (contracts.md sect. 7)."""

    review_id: str
    state: str
    delivery_id: str | None = None
    absorbed: bool = False
    work_item_id: str | None = None
    error_code: str | None = None


@dataclass
class _RenderInputs:
    """The two halves of a render's input, assembled once per draft."""

    facts: dict[tuple[str | None, str], Mapping[str, Any]] = field(default_factory=dict)
    records: tuple[Any, ...] = ()
    context: TemplateRenderContext = field(default_factory=TemplateRenderContext)


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


class SupportTemplateGateService:
    """Renders, opens, revises and delivers. Owns nothing durable of its own."""

    def __init__(
        self,
        *,
        reviews: ReviewAggregateStore,
        edit_rows: DraftEditRowsPort,
        support_service: Any,
        configuration: Callable[[], ReturnPlatformConfiguration | None],
        append_fact: Callable[..., Any],
        graph: SupportTemplateGraphPort | None = None,
    ) -> None:
        self._reviews = reviews
        # Required, positionally impossible to forget, and never defaulted to a
        # stub: an approval guard that is present and inert is worse than one
        # that is absent loudly.
        self._edit_rows = edit_rows
        self._support = support_service
        self._configuration = configuration
        self._append_fact = append_fact
        self._graph = graph

    # --------------------------------------------------------------- approval

    async def approve(
        self,
        *,
        case_id: str,
        review_id: str,
        actor_id: str,
        expected_draft_version: int,
        expected_canonical_edit_version: int,
        canonical_approved_payload_hash: str,
        workflow_id: str,
        signal_id: str,
        allow_system: bool = False,
        correlation_id: str | None = None,
    ) -> tuple[dict[str, Any], CaseCommandReceipt]:
        """`OPEN -> APPROVING`, with the conflict question asked of the rows.

        The **only** approval path V1 has -- the endpoint and `auto_send` both
        come through here, which is what makes conditions 5a and 8 closed
        rather than closed-at-one-of-two-callers. Everything after the
        recompute is S2's transition, unchanged and unwrapped.
        """
        review = await self._reviews.get_review(case_id=case_id, review_id=review_id)
        outstanding = unresolved_edit_actors(
            await self._edit_rows.edit_rows(review_id=review_id), review
        )
        if outstanding and not review.get("conflictPresent"):
            # The flag says clean and the rows disagree. S2's own error type, so
            # the endpoint answers 409 and the panel offers "resolve" exactly as
            # it does for a flagged conflict -- an associate must not have to
            # know which of two mechanisms noticed.
            logger.warning(
                "review_conflict_recomputed_from_rows",
                extra={
                    "case_id": case_id,
                    "review_id": review_id,
                    "actor_count": len(outstanding),
                },
            )
            raise ReviewConflictError(review_id)
        return await self._reviews.approve(
            case_id=case_id,
            review_id=review_id,
            actor_id=actor_id,
            expected_draft_version=expected_draft_version,
            expected_canonical_edit_version=expected_canonical_edit_version,
            canonical_approved_payload_hash=canonical_approved_payload_hash,
            workflow_id=workflow_id,
            signal_id=signal_id,
            allow_system=allow_system,
            correlation_id=correlation_id,
        )

    # ----------------------------------------------------------------- config

    def gate(self) -> SupportGateConfiguration:
        configuration = self._configuration()
        return (
            configuration.support_gate if configuration is not None else SupportGateConfiguration()
        )

    # ------------------------------------------------------------------ draft

    async def record_draft(
        self,
        *,
        case_id: str,
        request_id: str,
        review_id: str,
        fact_id_seed: str,
        facts: Mapping[tuple[str | None, str], Mapping[str, Any]],
        records: Sequence[Any] = (),
        context: TemplateRenderContext | None = None,
    ) -> GateDraft:
        """Render one request's draft and open the review over it."""
        configuration = self._configuration()
        template = configuration.support_template if configuration is not None else None
        if template is None or not template.variants:
            # Not an error and not a gap: the release simply has no template.
            # The caller falls back to the composed path, which is the same
            # thing an un-patched history does.
            return GateDraft(
                request_id=request_id,
                review_id=None,
                state=ReviewState.OPEN.value,
                draft_version=0,
                canonical_edit_version=0,
                template_available=False,
            )
        try:
            rendered = await render_support_template(
                template,
                TemplateDraftInput(
                    case_id=case_id,
                    context=context or TemplateRenderContext(),
                    facts=facts,
                    return_records=tuple(records),
                    graph=self._graph,
                ),
            )
        except TemplateNotConfiguredError:
            return GateDraft(
                request_id=request_id,
                review_id=None,
                state=ReviewState.OPEN.value,
                draft_version=0,
                canonical_edit_version=0,
                template_available=False,
            )

        payload = payload_of(rendered)
        review = await self._reviews.create_review(
            case_id=case_id,
            request_id=request_id,
            review_kind=ReviewKind.TEMPLATE,
            draft_payload=payload,
            review_id=review_id,
        )
        stored_id = str(review["_id"])
        await self._record_draft_facts(
            case_id=case_id,
            review_id=stored_id,
            fact_id_seed=fact_id_seed,
            rendered=rendered,
        )
        return self._draft_of(request_id, review)

    async def rerender_draft(
        self,
        *,
        case_id: str,
        request_id: str,
        review_id: str,
        fact_id_seed: str,
        facts: Mapping[tuple[str | None, str], Mapping[str, Any]],
        records: Sequence[Any] = (),
        context: TemplateRenderContext | None = None,
    ) -> GateDraft:
        """Produce the draft again and move `draft_version`.

        Check-then-act: a review that has left `OPEN` is reported as it stands
        rather than re-rendered, because a re-render into an approving review
        would move the version the approval is holding.
        """
        review = await self._reviews.get_review(case_id=case_id, review_id=review_id)
        if ReviewState(str(review["state"])) is not ReviewState.OPEN:
            return self._draft_of(request_id, review)

        configuration = self._configuration()
        template = configuration.support_template if configuration is not None else None
        if template is None or not template.variants:
            return self._draft_of(request_id, review)

        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id=case_id,
                context=context or TemplateRenderContext(),
                facts=facts,
                return_records=tuple(records),
                graph=self._graph,
            ),
        )
        payload = payload_of(rendered)
        if not review.get("pendingRevision") and payload == dict(review.get("draftPayload") or {}):
            # A retry of the same re-render. Bumping `draft_version` here would
            # invalidate a version the reviewer is holding, for no change.
            return self._draft_of(request_id, review)
        updated = await self._reviews.record_draft_revision(
            case_id=case_id,
            review_id=review_id,
            draft_payload=payload,
            expected_draft_version=int(review["draftVersion"]),
        )
        await self._record_draft_facts(
            case_id=case_id,
            review_id=review_id,
            fact_id_seed=fact_id_seed,
            rendered=rendered,
        )
        return self._draft_of(request_id, updated)

    async def record_revision(
        self,
        *,
        case_id: str,
        review_id: str,
        actor_id: str,
        note: str | None,
        fact_id_seed: str,
    ) -> None:
        """A reviewer asked for the draft again. Logged before it is produced.

        The note is a person's free text about a message to Support, so it is
        neutralised on the way onto the log -- it never enters an outbound
        message today, and the cheapest moment to make that safe is before
        anybody decides it should.
        """
        await self._append_fact(
            record_scope=review_id,
            case_id=case_id,
            fact_id=f"{fact_id_seed}:{fact_names.SUPPORT_TEMPLATE_REVISION}",
            fact_name=fact_names.SUPPORT_TEMPLATE_REVISION,
            # **`actorId`, in the value, and this is the agreed spelling.**
            # Contracts sect. 4 says a command-originated fact carries a
            # server-stamped `actorId`, and the persisted fact document has no
            # such field -- `append_scoped_case_fact` takes `agent_id` (which
            # software this was) and nothing for *which person decided*. The
            # orchestrator has reopened S1 to add the top-level field; until it
            # lands, every slice writes the actor under this exact key so the
            # migration is a mechanical rename rather than a hunt across three
            # vocabularies (V3 shipped `answeredBy` before this was settled).
            #
            # **Move this to the real parameter when S1 ships it**, and delete
            # the key from the value in the same change.
            value={"review_id": review_id, "actorId": actor_id, "note": _safe(note)},
            agent_id=_GATE_AGENT_ID,
            acquisition_method=FactAcquisition.ASSOCIATE_EDIT,
            channel=FactChannel.SYSTEM,
            observed_at=datetime.now(UTC),
        )

    # --------------------------------------------------------------- delivery

    async def deliver_approved(
        self,
        *,
        case_id: str,
        review_id: str,
        tenant_id: str,
        principal_id: str,
        fact_id_seed: str,
        queue: str | None = None,
    ) -> DeliveryOutcome:
        """Post the frozen payload on the case thread and settle the review.

        Idempotent in three independent ways, and all three are needed because
        they fail at different moments: the review's own state check absorbs a
        replay after `SENT`; `ensure_case_support_thread` is idempotent on the
        case; and `post_support_message` is deduped on the stored `delivery_id`
        so a retry that the receiver already holds comes back `absorbed=True`
        -- **which is a success, and the review still reaches `SENT`**.
        """
        review = await self._reviews.get_review(case_id=case_id, review_id=review_id)
        state = ReviewState(str(review["state"]))
        if state is ReviewState.SENT:
            return DeliveryOutcome(
                review_id=review_id,
                state=state.value,
                delivery_id=_text(review.get("deliveryId")),
                absorbed=True,
            )
        if state is not ReviewState.APPROVING:
            raise ReviewStateError(review_id, state, "deliver")

        payload = canonical_review_payload(review)
        # Belt to the API's braces (condition 7): whatever built this payload,
        # the field values that are about to be composed into a frame have been
        # through composition's neutralisation.
        payload = neutralise_field_edits(payload)
        body = payload_text(payload)
        subject = _text(payload.get(PAYLOAD_SUBJECT)) or None
        delivery_id = _text(review.get("deliveryId"))

        thread = await self._support.ensure_case_support_thread(
            case_id=case_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            support_draft=body,
            idempotency_key=f"support:{case_id}",
            business_payload=dict(payload),
            subject=subject,
            queue=queue,
        )
        try:
            if thread.created:
                # This call opened the conversation, and the opening request
                # *is* the message: posting again would put the same text on
                # the thread twice. `created` is decided by the insert winner,
                # so exactly one caller per case takes this branch.
                post = None
            else:
                post = await self._support.post_support_message(
                    work_item_id=thread.workItemId,
                    message_text=body,
                    delivery_id=delivery_id,
                    business_payload=dict(payload),
                )
        except Exception as error:  # noqa: BLE001 - classified, then recorded
            code = type(error).__name__[:128]
            logger.warning(
                "review_delivery_failed",
                extra={"case_id": case_id, "review_id": review_id, "error_code": code},
                exc_info=True,
            )
            await self._reviews.mark_delivery_failed(
                case_id=case_id, review_id=review_id, error_code=code
            )
            return DeliveryOutcome(
                review_id=review_id,
                state=ReviewState.DELIVERY_FAILED.value,
                delivery_id=delivery_id,
                work_item_id=thread.workItemId,
                error_code=code,
            )

        await self._reviews.mark_sent(case_id=case_id, review_id=review_id)
        await self._append_fact(
            record_scope=review_id,
            case_id=case_id,
            fact_id=f"{fact_id_seed}:{fact_names.SUPPORT_SENT_SNAPSHOT_REF}",
            fact_name=fact_names.SUPPORT_SENT_SNAPSHOT_REF,
            value={
                "review_id": review_id,
                "delivery_id": delivery_id,
                "content_hash": _text(review.get("contentHash")),
                "work_item_id": thread.workItemId,
                "thread_id": thread.threadId,
                "opened_thread": bool(thread.created),
                "absorbed": bool(post.absorbed) if post is not None else False,
                "payload_hash": canonical_payload_digest(payload),
            },
            agent_id=_GATE_AGENT_ID,
            acquisition_method=FactAcquisition.DERIVED,
            channel=FactChannel.CHANNEL_B,
            observed_at=datetime.now(UTC),
        )
        return DeliveryOutcome(
            review_id=review_id,
            state=ReviewState.SENT.value,
            delivery_id=delivery_id,
            absorbed=bool(post.absorbed) if post is not None else False,
            work_item_id=thread.workItemId,
        )

    # ---------------------------------------------------------------- reading

    async def review(self, *, case_id: str, review_id: str) -> dict[str, Any]:
        """One review document, straight through.

        Here rather than reaching for `._reviews` at the call sites, so the
        activity layer and the API layer read the aggregate through one door
        and a future change of store is one edit.
        """
        return await self._reviews.get_review(case_id=case_id, review_id=review_id)

    async def state_of(self, *, case_id: str, review_id: str, request_id: str) -> GateDraft:
        """One review as the wait loop reads it. A vanished review is `None`d
        rather than raised: the loop's job is to stop waiting, not to crash."""
        try:
            review = await self._reviews.get_review(case_id=case_id, review_id=review_id)
        except ReviewNotFoundError:
            return GateDraft(
                request_id=request_id,
                review_id=None,
                state=ReviewState.CANCELLED.value,
                draft_version=0,
                canonical_edit_version=0,
            )
        return self._draft_of(request_id, review)

    # ---------------------------------------------------------------- helpers

    async def _record_draft_facts(
        self,
        *,
        case_id: str,
        review_id: str,
        fact_id_seed: str,
        rendered: RenderedTemplate,
    ) -> None:
        payload = payload_of(rendered)
        await self._append_fact(
            record_scope=review_id,
            case_id=case_id,
            fact_id=f"{fact_id_seed}:{fact_names.SUPPORT_TEMPLATE_DRAFT}",
            fact_name=fact_names.SUPPORT_TEMPLATE_DRAFT,
            value={
                "review_id": review_id,
                "template_id": rendered.template_id,
                "variant_id": rendered.variant_id,
                "content_hash": canonical_payload_digest(payload),
            },
            agent_id=_GATE_AGENT_ID,
            acquisition_method=FactAcquisition.DERIVED,
            channel=FactChannel.SYSTEM,
            observed_at=datetime.now(UTC),
        )
        for gap in rendered.gaps:
            await self._append_fact(
                record_scope=review_id,
                case_id=case_id,
                fact_id=f"{fact_id_seed}:{fact_names.SUPPORT_TEMPLATE_GAP}:{gap.field_id}",
                fact_name=fact_names.SUPPORT_TEMPLATE_GAP,
                value={"review_id": review_id, "field_id": gap.field_id, "reason": gap.reason},
                agent_id=_GATE_AGENT_ID,
                acquisition_method=FactAcquisition.DERIVED,
                channel=FactChannel.SYSTEM,
                observed_at=datetime.now(UTC),
            )
        await self._append_fact(
            record_scope=None,
            case_id=case_id,
            fact_id=f"{fact_id_seed}:{fact_names.TEMPLATE_DRAFT_READY}",
            fact_name=fact_names.TEMPLATE_DRAFT_READY,
            value=review_id,
            agent_id=_GATE_AGENT_ID,
            acquisition_method=FactAcquisition.DERIVED,
            channel=FactChannel.SYSTEM,
            observed_at=datetime.now(UTC),
        )

    @staticmethod
    def _draft_of(request_id: str, review: Mapping[str, Any]) -> GateDraft:
        payload = cast(Mapping[str, Any], review.get("draftPayload") or {})
        gaps = cast(Sequence[Mapping[str, Any]], payload.get(PAYLOAD_GAPS) or ())
        return GateDraft(
            request_id=request_id,
            review_id=str(review["_id"]),
            state=str(review["state"]),
            draft_version=int(review.get("draftVersion", 0)),
            canonical_edit_version=int(review.get("canonicalEditVersion", 0)),
            gap_field_ids=tuple(str(gap.get("field_id", "")) for gap in gaps),
            subject=str(payload.get(PAYLOAD_SUBJECT, "")),
            text=payload_text(payload),
        )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
