"""`CasePanelView` and the section registry (contracts.md sect. 9, DR-10).

**This module's contract is frozen at merge.** V1 ships the DTO and the
registry and then never touches either again; V2 and V3 contribute their
sections through `register_panel_section` in their own files. That is the whole
point of the seam -- three slices adding fields to one DTO is three slices
editing one file, and the last one to merge wins an argument nobody had.

---

**What the shared payload is, and what it deliberately is not.**

Every field here is *case-scoped and principal-independent*. Two principals
who may both read a case get byte-identical bodies and therefore identical
ETags, which is what makes a shared cache honest and is asserted directly.
`accepted_commands[]` is unfiltered for the same reason: filtering it by actor
would make the hash actor-dependent while looking like a privacy improvement,
and the actor ids in it are already visible in the review's state history.

Per-actor edit state is **not here**. It lives at
`GET .../reviews/{review_id}/edit-state`, `Cache-Control: private, no-store`,
because an autosaved draft is one person's unfinished thinking and putting it
in a shared, cacheable, hashed body would publish it to everyone who can read
the case. The *conflict marker* participates in the hash -- "somebody else is
editing this" is a case-level fact -- while the edit contents never do.

**Absolute instants only.** `deadline_iso` is an instant; there is no
`seconds_remaining`. A server-computed countdown is stale the moment it is
serialized, and worse, it makes the hash change every second so no ETag ever
matches. The browser counts down.

**Degradation is narrow.** A section answers `{status: "degraded", reason}`
only for an expected timeout or a transient failure -- a Temporal query that
timed out, a projection that could not be assembled. An authorization failure,
a contract violation or a broken invariant is a real error and is raised as
one: a panel that renders "degraded" over a 403 is a panel that hides a
security answer behind a spinner. And a degraded section is **never a
`TemplateGap`** -- gaps come only from the renderer, and mean the *case* does
not know something, not that we could not read it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PANEL_SECTION_IDS",
    "AcceptedCommandView",
    "CasePanelView",
    "PanelExecutionView",
    "PanelSectionContributor",
    "PanelSectionView",
    "PanelTimersView",
    "ReviewPanelView",
    "canonical_panel_payload",
    "clear_panel_sections",
    "panel_etag",
    "panel_section_contributors",
    "register_panel_section",
    "sorted_sections",
]


class _Panel(BaseModel):
    """Order-stable and closed. Both matter to the hash.

    `extra="forbid"` so a section cannot smuggle a field past the contract, and
    field order is declaration order, which is what makes the canonical
    serialization below deterministic without a sorting pass over every nested
    object.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class PanelSectionView(_Panel):
    """One contributed section. **V2 and V3 add sections, never fields.**

    The payload is an opaque JSON object rather than a typed sub-model, and
    that is the seam: a typed field per section would put V2's and V3's shapes
    into this file and into every OpenAPI regeneration V1 owns. Each
    contributor owns its own payload's shape and its own tests for it.
    """

    section_id: str
    #: `"ok"` or `"degraded"`. A degraded section still renders -- with its
    #: reason -- rather than vanishing, because a section that disappears looks
    #: to an associate exactly like a case that has nothing to show.
    status: str = "ok"
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class PanelExecutionView(_Panel):
    """The Temporal `execution_state` query, as the panel needs it.

    Degradable: the workflow host being unreachable is an expected transient,
    and the rest of the panel -- reviews, records, the thread digest -- is read
    from Mongo and is still true.
    """

    status: str = "ok"
    reason: str | None = None
    case_status: str | None = None
    work_item_id: str | None = None
    awaiting: tuple[str, ...] = ()
    business_complete: bool = False
    parked_reason: str | None = None


class ReviewPanelView(_Panel):
    """One review, keyed `(review_kind, scope_id)` (contracts.md sect. 9).

    Carries every non-terminal review *including* `APPROVING`,
    `DELIVERY_FAILED` and `HELD_FOR_OPERATIONS` -- a review the associate can
    no longer edit is precisely the one they most need to see -- plus recently
    terminal ones for visibility.

    `draft` is the payload as it currently stands; `gaps` come only from the
    renderer. `conflict_present` is the case-scoped marker's answer for this
    review, never a per-actor fact.
    """

    review_id: str
    review_kind: str
    scope_id: str
    request_id: str
    state: str
    draft_version: int
    canonical_edit_version: int
    conflict_present: bool
    draft: dict[str, Any] = Field(default_factory=dict)
    gaps: tuple[dict[str, Any], ...] = ()
    #: The digest of the payload approval would freeze, as the store holds it
    #: **right now**.
    #:
    #: **Served rather than left for the client to derive, and that is the whole
    #: point of it.** Approval's compare-and-set requires
    #: `canonical_approved_payload_hash` to equal
    #: `canonical_payload_digest(canonical_review_payload(review))` -- over the
    #: store's canonical serialization, of the canonical edit where one exists
    #: and the draft where it does not. A browser computing that would be a
    #: second implementation of the CAS, in another language, and the two would
    #: disagree the first time either side changed how a payload serializes.
    #: Every approval from the console would then answer 409 for a reason no
    #: associate could act on.
    #:
    #: Echoing it back loses nothing the CAS is for: the guarantee is that an
    #: associate approves *the bytes they read*, and a draft that moved between
    #: this panel read and their approval produces a different digest, so the
    #: store still refuses. `None` only when the review is past `OPEN` and there
    #: is nothing left to approve.
    approval_hash: str | None = None
    #: `None` unless the review has been in `APPROVING` or past it. The panel
    #: shows "approved by X, sending" from this, and it is server-stamped.
    approved_by: str | None = None
    approved_at_iso: str | None = None
    #: Present from `DELIVERY_FAILED` on. What the recovery actions act on.
    recovery_status: str | None = None
    last_delivery_error_code: str | None = None
    hold_reason: str | None = None
    abandon_audit: dict[str, Any] | None = None


class AcceptedCommandView(_Panel):
    """A command the platform accepted and has not necessarily applied yet.

    **Unfiltered by actor** -- see the module docstring. It is what makes "I
    pressed Send and nothing happened" answerable: the command is recorded, the
    signal has not landed yet, and the panel can say so instead of showing an
    unchanged review.
    """

    signal_id: str
    kind: str
    actor_id: str
    review_id: str | None = None
    recorded_at_iso: str | None = None
    applied: bool = False


class PanelTimersView(_Panel):
    """Absolute instants. The countdown is the browser's (contracts.md sect. 9)."""

    template_review_deadline_iso: str | None = None
    template_review_reminders_sent: int = 0
    template_review_max_reminders: int = 0
    support_deadline_iso: str | None = None


class CasePanelView(_Panel):
    """The one shape the panel and `CaseOperationsPage` both read.

    **Frozen at merge.** Anything V2 or V3 wants to show goes in `sections`
    through the registry.

    That sentence used to be false for three fields. `support_digest`,
    `clarifications` and `parked_messages` were declared here as top-level
    placeholders for contributing slices to fill -- but a contributor satisfies
    `PanelSectionContributor`, which returns a `PanelSectionView | None` into
    `sections`, and **has no way to write a top-level field**. So the composer
    hardcoded all three empty and no contributor could ever change that. V3
    built a clarifications section against `panel.clarifications`; it would have
    drawn nothing on every real panel while a suite full of hand-built panel
    objects stayed green. AMENDMENT-6 retired them, and this is where that
    lands.

    **Do not re-add a top-level field for a contributing slice.** If a section
    needs to say something, it says it in its own `PanelSectionView.payload`.
    A second parallel path that the seam cannot reach is the defect, not the
    absence of one.
    """

    case_id: str
    execution: PanelExecutionView
    reviews: tuple[ReviewPanelView, ...] = ()
    return_records: tuple[dict[str, Any], ...] = ()
    timers: PanelTimersView = Field(default_factory=PanelTimersView)
    accepted_commands: tuple[AcceptedCommandView, ...] = ()
    #: Contributed sections, **sorted by `section_id`**. Sorted rather than
    #: registration-ordered because registration order depends on import order,
    #: and an ETag that changed when a module moved would be a cache that
    #: misses for a reason nobody can see.
    sections: tuple[PanelSectionView, ...] = ()


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #


class PanelSectionContributor(Protocol):
    """What V2 and V3 implement.

    `context` is deliberately a plain mapping rather than a typed object: the
    contributors live in other slices, and a typed context would be a second
    frozen contract this file owns. What it holds is documented at
    `register_panel_section`.
    """

    async def __call__(self, context: Mapping[str, Any]) -> PanelSectionView | None: ...


_CONTRIBUTORS: dict[str, PanelSectionContributor] = {}

#: Section ids V1 knows about, for the reader. Contributors are **not**
#: restricted to this list -- a registry that had to be edited to accept a new
#: section would be the frozen DTO again, wearing a dict.
PANEL_SECTION_IDS: Final[tuple[str, ...]] = ()


def register_panel_section(section_id: str, contributor: PanelSectionContributor) -> None:
    """Contribute one section to every case panel.

    Called at import time from the owning slice's own module. Registering the
    same id twice is a programming error and raises: two contributors for one
    section would race for the id, and which one won would depend on import
    order.

    The `context` a contributor receives carries at minimum `case_id`,
    `tenant_id`, `principal_id` and `request` (the FastAPI `Request`, so a
    contributor can reach its own app state). A contributor that needs
    something else asks for it here rather than reaching into the panel
    composer, which is what keeps this seam one-directional.

    Returning `None` omits the section. Raising is caught by the composer and
    becomes a **degraded** section -- a contributor must not be able to take
    the whole panel down, because the reviews are the part an associate is
    actually blocked on.
    """
    if section_id in _CONTRIBUTORS:
        raise ValueError(f"panel section {section_id!r} is already registered")
    _CONTRIBUTORS[section_id] = contributor


def panel_section_contributors() -> tuple[tuple[str, PanelSectionContributor], ...]:
    """Every registered contributor, **sorted by id**. See `CasePanelView.sections`."""
    return tuple(sorted(_CONTRIBUTORS.items()))


def clear_panel_sections() -> None:
    """Empty the registry. For tests, and named so that is obvious."""
    _CONTRIBUTORS.clear()


def _canonical(value: Any) -> Any:
    """Order-stable JSON, so one body always hashes to one ETag.

    Mappings are emitted in sorted key order and every other container keeps
    its own order, which is meaningful -- `reviews[]` is oldest-first and
    reordering it would be a different panel. Bytes are refused rather than
    coerced: nothing in this DTO holds them, and a silent `str()` of one would
    hash two different values the same.
    """
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        raise TypeError(
            "the panel payload holds no bytes, and coercing them would hash two "
            "different values the same"
        )
    return value


def canonical_panel_payload(view: CasePanelView) -> dict[str, Any]:
    """The bytes the ETag is computed over."""
    return _canonical(view.model_dump(mode="json"))


def panel_etag(view: CasePanelView, *, digest: Callable[[Mapping[str, Any]], str]) -> str:
    """`ETag` for one composed panel (DR-10).

    The digest function is injected rather than imported so this module stays
    free of the support-event machinery; the composer passes
    `canonical_payload_digest`, which is the same digest the approval hash uses.
    Quoted, because an unquoted ETag is not a valid one and some caches drop it.
    """
    return f'"{digest(canonical_panel_payload(view))}"'


def sorted_sections(sections: Sequence[PanelSectionView]) -> tuple[PanelSectionView, ...]:
    return tuple(sorted(sections, key=lambda section: section.section_id))
