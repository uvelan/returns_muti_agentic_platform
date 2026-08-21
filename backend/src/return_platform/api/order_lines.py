"""The order's lines, and the selection that holds quantity against them.

```text
GET  /api/cases/{case_id}/order-lines
POST /api/cases/{case_id}/selected-items
```

**Case-bound, and that is the whole design (plan sect. 12.1).** There is
deliberately no `/api/orders/{orderReference}/lines`. A naked order reference is
ambiguous across source systems, tenants, business units and graph generations,
and it is not an authorization boundary -- anyone who guessed a number would be
reading somebody's customer's order. The case *is* the boundary: it records
which order the associate confirmed, under which tenant and which principal, and
these routes resolve the order from it under exactly the scoping `get_case`
applies. Somebody else's case is **404, not 403**, for the reason `api/cases.py`
gives: a 403 on a guessed id confirms the id exists.

**A separate module from `api/cases.py`, and not because that file is long.**
`cases.py` serves one resource -- the case projection -- plus the two operator
actions that act on the case document itself. These two routes serve a
*different* resource that happens to be addressed through the case: the order's
lines come from the source sales document by way of the active schema, and the
quantity ledger spans every case in the tenant that touched the same line.
Neither of those dependencies belongs in the module whose stated job is "load the
state, check who is asking, hand it to `project_case`". The prefix stays
`/api/cases/{case_id}` because that is the authorization boundary, and FastAPI
mounts two routers on one prefix without either knowing about the other.

**Nothing here computes a quantity.** The four terms of plan sect. 12.2 are
derived in `operations/order_lines/availability.py` and re-derived by the writer
inside its own transaction. This module reads the case, resolves the order,
converts, and serializes -- the same division `api/cases.py` keeps against
`project_case`.

**Two shapes in one payload, and the split is the design.** `items` is per-line
and is a replace set with a quantity hold behind it. `contact` -- the branch
associate a carrier needs in order to produce a UPS label or an LTL bill of
lading -- is case-level and goes to the append-only fact log, which is where
every other case-level return detail already lives and the one place
`draft_support_request` reads. They travel together because they are one
operator action on one screen, and are stored apart because they are not one
kind of thing.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    ReturnPlatformConfiguration,
    SelectionVocabularyConfiguration,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import resolve_active_schema
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.order_lines import (
    DataInconsistency,
    LineAlreadyAuthorizedError,
    LineAvailability,
    LineSelection,
    OrderLineSchemaUnavailableError,
    QuantityUnavailableError,
    SelectionOutcome,
    SourceOrderLine,
    load_source_order_lines,
    resolve_product_colours,
)
from return_platform.operations.repository import resolve_operational_repository
from return_platform.operations.seed_manifest import (
    SOURCE_PRODUCTS_DATASET,
    SOURCE_SALES_DATASET,
)
from return_platform.resources import RuntimeResources
from return_platform.security.authorization import require_associate_roles, require_read_roles
from return_platform.security.principal import Principal
from return_platform.shared.contracts import APIResponse, ResponseMeta

logger = logging.getLogger("return_platform.api.order_lines")

router = APIRouter(prefix="/api/cases", tags=["Order Lines"])

#: The most lines one request may name. Well above any real order -- the largest
#: in the reference extract is nowhere near it -- and present so a malformed
#: client cannot make one request open a hundred thousand reservations.
_MAX_SELECTED_LINES = 200


class OrderLineView(BaseModel):
    """One line of the confirmed order, with the arithmetic behind its availability.

    **The four terms travel with the answer.** A pane that showed only
    `returnableQuantity` could not tell an associate why a line of eight shows
    two, and the difference between "six were already returned" and "six are
    held by another counter right now" is the difference between explaining and
    waiting.

    `unitPrice` is a **decimal string**, never a JSON number. A refund basis
    that round-trips through binary floating point disagrees with the invoice by
    a cent on some orders and not others, and the client has no way to tell
    which. Absent when the source carries no price -- never `"0.00"`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    lineReference: str
    sku: str | None = None
    description: str | None = None
    #: The product's colour, from the catalogue rather than from the line, and
    #: `None` when the deployment binds no colour path or the catalogue does not
    #: describe one for this product. A caller renders that absence explicitly;
    #: it is never inferred from `description`.
    colour: str | None = None
    orderedQuantity: int | None = None
    unitPrice: str | None = None
    productReference: str | None = None
    returnableQuantity: int = Field(ge=0)
    completedReturnQuantity: int = Field(ge=0)
    openAuthorizedQuantity: int = Field(ge=0)
    #: Held right now by *other* cases. This case's own hold is reported below
    #: and is excluded from the subtraction, so editing a selection upward does
    #: not have to fight the hold it is replacing.
    activeReservationQuantity: int = Field(ge=0)
    selfReservedQuantity: int = Field(ge=0)
    #: Set when the arithmetic could not be trusted -- most importantly when the
    #: commitments exceed what was ordered, which surfaces as `0` here *and* as
    #: a flag, never as a negative and never as a silent clamp.
    dataInconsistency: DataInconsistency | None = None


class OrderLines(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    caseId: str
    #: Echoed so a screen can say which order it is showing rather than only
    #: that it asked a case for one.
    orderReference: str
    lines: tuple[OrderLineView, ...] = ()


class SelectedItemRequest(BaseModel):
    """One line the associate is naming. Server-derived fields are absent.

    No product reference and no price: both are properties of the order line,
    resolved from the source, and a client that could state them could record a
    return against a product the order does not contain.
    """

    model_config = ConfigDict(extra="forbid")

    orderLineReference: str = Field(min_length=1, max_length=128)
    quantity: int = Field(ge=1, le=10_000)
    #: Length-bounded here and checked against the released catalogue in
    #: `_reject_unpublished_terms`, which is where plan sect. 12.4's "reason and
    #: condition vocabularies come from return configuration" is enforced. The
    #: catalogue is deliberately not restated as a `Literal` on this model: it is
    #: operator-owned and changes in a release, and a schema that had to be
    #: recompiled to add a reason would be the hardcoded vocabulary the phase
    #: removes.
    reason: str | None = Field(default=None, max_length=128)
    condition: str | None = Field(default=None, max_length=128)
    packageReference: str | None = Field(default=None, max_length=128)


#: What an address has to look like to be an address. Deliberately weak -- a
#: local part, an `@`, a host with a dot -- because the strong forms of this
#: check are all wrong in the same direction: RFC 5322 admits addresses no
#: carrier portal accepts, and a tighter pattern refuses `o'brien@x.co.uk` and
#: the newer TLDs. It exists to catch a phone number typed into the email box,
#: which is the failure that produces a label request nobody can answer.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class ReturnContactRequest(BaseModel):
    """The branch associate a carrier can reach about this return.

    **Case-level, and a sibling of `items` rather than a field on one.** One
    associate raises one return; a contact per line would let a two-line
    selection carry two different people and give the label writer no rule for
    picking between them. The shape says so, so the wrong thing is unsayable.

    **Every field optional, by operator instruction**, exactly as the branch
    number is. A return whose associate is not recorded is an ordinary return
    and must not be blocked, and none of these may ever acquire a default: an
    invented associate email routes a UPS label to nobody, which is the same
    failure `ItemSelectionMode` refuses a default branch for.

    **Shape is checked; existence is not.** An email that is not an email is
    refused here as well as at entry -- the console cannot be the only thing
    standing between a typo and a carrier -- while a name and a phone are only
    length-bounded. There is no honest format rule for either: branch phone
    numbers in the reference extract are written half a dozen ways, and a
    pattern invented here would reject the ones a carrier can actually dial.

    The empty string is accepted and is not a null. It is how a recorded value
    is retracted: the fact log is append-only, so an associate correcting a
    contact they entered by mistake appends an empty value over it rather than
    deleting anything. `null` -- the field simply absent -- says this
    submission makes no statement about it at all.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)

    @field_validator("name", "email", "phone")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        """Surrounding whitespace is typing, not content."""
        return None if value is None else value.strip()

    @field_validator("email")
    @classmethod
    def _addressable(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if _EMAIL_SHAPE.fullmatch(value) is None:
            raise ValueError(
                "the branch associate email is not an address a carrier could send a label "
                "to. Leave it empty rather than recording something that cannot be reached."
            )
        return value


class SelectedItemsRequest(BaseModel):
    """The **whole** selection, not an addition to it.

    Replace-set semantics, stated in the shape: a line the associate removed is
    a line absent from `items`, and the writer releases its hold in the same
    transaction that records the ones they kept. An append endpoint would leave
    withdrawn lines holding quantity nobody was going to return.

    **`contact` is deliberately not part of that replace set.** The selection is
    a ledger with a hold behind it, so the whole of it has to arrive at once;
    the contact is a statement about the case, kept on the append-only fact log
    where every other case-level detail lives. Omitting it therefore means "this
    submission says nothing about the associate", and leaves whatever the case
    already holds standing -- never "clear it". Retraction is an explicit empty
    value; see `ReturnContactRequest`.
    """

    model_config = ConfigDict(extra="forbid")

    items: tuple[SelectedItemRequest, ...] = Field(default=(), max_length=_MAX_SELECTED_LINES)
    contact: ReturnContactRequest | None = None


class SelectedItemView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    returnItemId: str
    orderLineReference: str
    productReference: str | None = None
    quantity: int | None = None
    reason: str | None = None
    condition: str | None = None
    packageReference: str | None = None


class SelectedItems(BaseModel):
    """What the case now holds, and what the order now has left.

    The refreshed lines travel with the write deliberately. The next thing the
    client does after selecting is render the line list, and a response that
    made it poll for that would leave the pane showing the availability the
    associate selected *against* for as long as the round trip took.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    caseId: str
    revision: int
    #: `false` for an identical re-submission. The revision is unchanged in that
    #: case, by design (plan sect. 6.5) -- a no-op must invalidate nobody's cache.
    changed: bool
    items: tuple[SelectedItemView, ...] = ()
    lines: tuple[OrderLineView, ...] = ()


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


def _not_found(case_id: str) -> HTTPException:
    """Absent, not forbidden -- the wording `api/cases.py` already answers with."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "CASE_NOT_FOUND",
            "message": f"Case {case_id} does not exist.",
            "retryable": False,
        },
    )


def _conflict(code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message, "retryable": False, **extra},
    )


async def _owned_case(request: Request, case_id: str) -> dict[str, Any]:
    """The caller's own case, or a 404 that tells a guesser nothing.

    Tenant **and** principal, exactly as `api/cases.py:_belongs_to` scopes the
    projection read: a principal id repeated in a second tenant would otherwise
    read across the boundary. Checked here rather than in the repository for the
    reason that module gives -- the loader mirrors `get_case`, half a dozen
    callers have no principal to scope by, and a scope you can opt out of is not
    a scope.
    """
    repository = resolve_operational_repository(request)
    case = await repository.get_case(case_id)
    if case is None:
        raise _not_found(case_id)
    principal = cast(Principal, request.state.principal)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    if case.get("tenantId") != tenant_id or case.get("principalId") != principal.subject:
        raise _not_found(case_id)
    return case


def _confirmed_order(case: Mapping[str, Any], case_id: str) -> str:
    order_reference = case.get("confirmedOrderReference")
    if not isinstance(order_reference, str) or not order_reference.strip():
        # The lines of an order nobody has confirmed are not this resource's to
        # serve. Plan sect. 12.1 routes that read through the candidate set,
        # where the agent's own guards bind the choice to the conversation.
        raise _conflict(
            "ORDER_NOT_CONFIRMED",
            f"Case {case_id} has no confirmed order, so it has no order lines. "
            "Resolve candidates through order discovery instead.",
        )
    return order_reference.strip()


def _active_release(request: Request) -> ReturnPlatformConfiguration:
    """The configuration this process has adopted. No fallback.

    Refused with 503 rather than defaulted for the same reason
    `api/cases.py:_requirement_table` refuses: an unconfigured platform that
    quietly answers from a constant looks exactly like a configured one, and here
    the difference only surfaces as quantity held for the wrong length of time,
    or as a selection accepted against a catalogue nobody published.
    """
    loaded = getattr(request.app.state, "return_configuration", None)
    if not isinstance(loaded, LoadedReturnConfiguration):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETURN_CONFIGURATION_UNAVAILABLE",
                "message": (
                    "No return configuration is active, so the reservation TTL cannot be "
                    "resolved and no quantity can be held."
                ),
                "retryable": True,
            },
        )
    return loaded.configuration


async def _active_schema(request: Request) -> ActiveSchema:
    """The schema the source is read through -- published release first.

    The same resolution `GraphSyncService.refresh_schema` and
    `api/return_history.py` use. Reading the order under a different schema than
    the one the graph was built from would mean reading paths the extract does
    not have, and reporting the result as an order with no descriptions.
    """
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ORDER_LINES_UNAVAILABLE",
                "message": "The platform runtime is not initialized.",
                "retryable": True,
            },
        )
    mongo = resources.mongo if isinstance(resources, RuntimeResources) else None
    releases = None if mongo is None else SchemaReleaseStore(mongo, settings.mongo_database)
    return await resolve_active_schema(settings.dynamic_knowledge_schema_path, releases)


async def _source_lines(request: Request, order_reference: str) -> tuple[SourceOrderLine, ...]:
    """The confirmed order's lines, or a 409 naming the order the source lacks."""
    repository = resolve_operational_repository(request)
    schema = await _active_schema(request)
    try:
        collection = await repository.source_dataset(SOURCE_SALES_DATASET)
        lines = await load_source_order_lines(
            collection, schema=schema, order_reference=order_reference
        )
    except OrderLineSchemaUnavailableError as unavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ORDER_LINE_SCHEMA_UNAVAILABLE",
                "message": str(unavailable),
                "retryable": False,
            },
        ) from unavailable
    if lines is None:
        # The case names an order the source does not hold -- a rebinding, a
        # partial extract, or a case older than the data behind it. Reported as
        # a conflict naming the order rather than as an order with no lines,
        # which would read as an order somebody had emptied.
        raise _conflict(
            "ORDER_NOT_IN_SOURCE",
            f"Order {order_reference} is not present in the sales source, so its lines "
            "cannot be read.",
        )
    return lines


def _price(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _line_view(
    line: SourceOrderLine,
    availability: LineAvailability,
    colours: Mapping[str, str],
) -> OrderLineView:
    return OrderLineView(
        lineReference=line.line_reference,
        sku=line.sku,
        description=line.description,
        colour=colours.get(line.product_reference or ""),
        orderedQuantity=line.ordered_quantity,
        unitPrice=_price(line.unit_price),
        productReference=line.product_reference,
        returnableQuantity=availability.returnable_quantity,
        completedReturnQuantity=availability.completed_return_quantity,
        openAuthorizedQuantity=availability.open_authorized_quantity,
        activeReservationQuantity=availability.active_reservation_quantity,
        selfReservedQuantity=availability.self_reserved_quantity,
        dataInconsistency=availability.data_inconsistency,
    )


async def _line_views(
    request: Request,
    *,
    case_id: str,
    tenant_id: str,
    order_reference: str,
    lines: tuple[SourceOrderLine, ...],
) -> tuple[OrderLineView, ...]:
    repository = resolve_operational_repository(request)
    availability = await repository.load_order_line_availability(
        tenant_id=tenant_id,
        order_reference=order_reference,
        viewing_case_id=case_id,
        ordered_by_line={line.line_reference: line.ordered_quantity for line in lines},
    )
    colours = await _line_colours(request, lines)
    return tuple(
        _line_view(line, availability[line.line_reference], colours) for line in lines
    )


async def _line_colours(
    request: Request, lines: tuple[SourceOrderLine, ...]
) -> Mapping[str, str]:
    """Colour per master product id, or nothing at all.

    Best-effort by construction, and deliberately so: colour is a decoration on
    an availability read that an associate needs in order to act. A catalogue
    that is unreadable, or a release that binds no colour path, leaves every line
    reporting no colour -- which the client renders as an explicit unavailable
    state. Failing the whole order-lines read over it would take away the
    quantities as well, which is the wrong trade.
    """
    loaded = getattr(request.app.state, "return_configuration", None)
    paths = (
        loaded.configuration.source_resolution.product_colour_paths
        if isinstance(loaded, LoadedReturnConfiguration)
        else ()
    )
    if not paths:
        return {}
    repository = resolve_operational_repository(request)
    try:
        catalogue = await repository.source_dataset(SOURCE_PRODUCTS_DATASET)
        return await resolve_product_colours(
            catalogue,
            product_references=[line.product_reference or "" for line in lines],
            colour_paths=paths,
        )
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("order_line_colour_unavailable", exc_info=True)
        return {}


@router.get("/{case_id}/order-lines", response_model=APIResponse[OrderLines])
async def read_case_order_lines(
    case_id: str,
    request: Request,
    _actor_id: str = Depends(require_read_roles),
) -> APIResponse[OrderLines]:
    """The lines of the order this case confirmed, with what is still returnable.

    Four steps and no fifth: resolve the case under the caller's scope, take the
    confirmed order off it, project the lines from the source through the active
    schema, and ask the repository for the arithmetic. Nothing is derived in this
    handler.

    The read is not a promise. Two associates can both see the same
    `returnableQuantity` and both submit it; `POST /selected-items`
    re-evaluates and commits atomically, and one of them gets a 409 carrying the
    recomputed figure.
    """
    case = await _owned_case(request, case_id)
    order_reference = _confirmed_order(case, case_id)
    lines = await _source_lines(request, order_reference)
    return APIResponse(
        data=OrderLines(
            caseId=case_id,
            orderReference=order_reference,
            lines=await _line_views(
                request,
                case_id=case_id,
                tenant_id=str(getattr(request.state, "tenant_id", "default")),
                order_reference=order_reference,
                lines=lines,
            ),
        ),
        meta=_meta(request),
    )


def _reject_unpublished_terms(
    payload: SelectedItemsRequest, vocabulary: SelectionVocabularyConfiguration
) -> None:
    """Refuse reasons and conditions the active release does not publish.

    Plan sect. 12.4 puts both vocabularies in the return configuration, and this
    is where the release finally means something: `SelectedItemRequest` bounds
    the length and nothing else, so before this a client could record `asdf` as
    the reason a line came back and the value would travel verbatim into
    `case_facts` and out again as evidence.

    **A release that publishes no catalogue refuses nothing.** The check is the
    release's, not this module's -- `unknown_reasons` answers empty for an
    unpublished catalogue -- so a deployment on a release cut before the block
    keeps exactly today's free-text behaviour rather than losing its returns to
    a key nobody had been asked for.

    422 rather than 409: the body names something that does not exist in this
    release, which is a request the client should not have been able to compose,
    not a conflict with another writer. The published catalogue travels with the
    refusal so the client can re-render the picker instead of guessing.
    """
    reasons = vocabulary.unknown_reasons(
        [item.reason for item in payload.items if item.reason is not None]
    )
    conditions = vocabulary.unknown_conditions(
        [item.condition for item in payload.items if item.condition is not None]
    )
    if not reasons and not conditions:
        return
    named = []
    if reasons:
        named.append(f"reason(s) {', '.join(reasons)}")
    if conditions:
        named.append(f"condition(s) {', '.join(conditions)}")
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "SELECTION_TERM_NOT_PUBLISHED",
            "message": (f"The active return configuration does not publish {' or '.join(named)}."),
            "retryable": False,
            "reasons": list(reasons),
            "conditions": list(conditions),
            "publishedReasons": list(vocabulary.reasons),
            "publishedConditions": list(vocabulary.conditions),
        },
    )


def _selections(
    payload: SelectedItemsRequest,
    lines: tuple[SourceOrderLine, ...],
    *,
    order_reference: str,
) -> tuple[LineSelection, ...]:
    """The request, bound to lines the order actually has.

    A line reference the order does not carry is refused rather than reserved.
    Reserving it would create a hold against a line no availability read will
    ever mention, which is quantity that leaks until its TTL expires and that
    nobody can see to release.
    """
    by_reference = {line.line_reference: line for line in lines}
    unknown = sorted(
        item.orderLineReference
        for item in payload.items
        if item.orderLineReference not in by_reference
    )
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "ORDER_LINE_NOT_ON_ORDER",
                "message": (f"Order {order_reference} has no line(s) {', '.join(unknown)}."),
                "retryable": False,
                "orderLineReferences": unknown,
            },
        )
    repeated = sorted(
        {
            item.orderLineReference
            for index, item in enumerate(payload.items)
            for other in payload.items[index + 1 :]
            if item.orderLineReference == other.orderLineReference
        }
    )
    if repeated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "ORDER_LINE_REPEATED",
                "message": (
                    "A selection names the same order line twice: "
                    f"{', '.join(repeated)}. Send one entry per line with the total quantity."
                ),
                "retryable": False,
                "orderLineReferences": repeated,
            },
        )
    return tuple(
        LineSelection(
            order_line_reference=item.orderLineReference,
            quantity=item.quantity,
            reason=item.reason,
            condition=item.condition,
            package_reference=item.packageReference,
            # Server-derived from the source line, never from the body.
            product_reference=by_reference[item.orderLineReference].product_reference,
        )
        for item in payload.items
    )


#: Fact name -> the field of `ReturnContactRequest` that states it.
#:
#: The fact log is where every other case-level return detail already lives --
#: the confirmed order, the policy decision and its override, the bay and its
#: confidence -- and it is what `draft_support_request` reads to compose the
#: message Returns Support answers. Putting the associate anywhere else would
#: put it somewhere Support cannot see, which for a contact whose entire purpose
#: is a UPS or LTL label is the same as not recording it.
#:
#: `branch_associate_*` rather than `associate_*`: Fergusonhome's own list asks
#: for the *branch* associate, and the case already carries an unrelated
#: `principalId` for whoever is signed in. They are frequently the same person
#: and are not the same field -- a counter terminal is shared.
_CONTACT_FACTS: tuple[tuple[str, str], ...] = (
    ("branch_associate_name", "name"),
    ("branch_associate_email", "email"),
    ("branch_associate_phone", "phone"),
)


async def _record_return_contact(
    repository: Any, *, case_id: str, contact: ReturnContactRequest | None
) -> None:
    """Append what changed about the branch associate, and nothing else.

    **Only what changed.** `append_case_fact` is insert-only and bumps the case
    revision, so re-sending an unchanged selection -- which the replace-set
    shape encourages, and which answers `changed: false` for the items -- must
    not append three identical facts and invalidate every cached projection. The
    current value is read first and an identical restatement is dropped.

    **A field the payload omits is not a field the case forgot.** `None` means
    this submission made no claim; the empty string means the associate cleared
    a value they had entered, which is a real transition and is recorded as one.
    Clearing something that was never recorded is nothing happening, so it
    appends nothing.

    Written after the selection commits, deliberately. The selection is the act
    that holds quantity and the one that can be refused; a contact appended
    before a `409 QUANTITY_UNAVAILABLE` would leave the case carrying details
    for a return that was never recorded.
    """
    if contact is None:
        return
    current = await repository.latest_case_facts(case_id)
    for fact_name, attribute in _CONTACT_FACTS:
        stated = cast(str | None, getattr(contact, attribute))
        if stated is None:
            continue
        held = current.get(fact_name)
        recorded = "" if held is None else str(held.get("value") or "")
        if stated == recorded:
            continue
        await repository.append_case_fact(
            fact_id=str(uuid.uuid4()),
            case_id=case_id,
            fact_name=fact_name,
            value=stated,
            agent_id="return-copilot",
            # Channel A: an associate typed it into the return screen. Not
            # `SYSTEM` -- nothing here derived it -- and not `CHANNEL_B`, which
            # is Support's side of the exchange this contact exists to feed.
            channel=FactChannel.CHANNEL_A,
            acquisition_method=FactAcquisition.STATED,
            source_system="RETURN_PLATFORM_CONSOLE",
            source_path="RETURN_DETAILS_CAPTURE",
        )


def _selected_item_view(item: Mapping[str, Any]) -> SelectedItemView:
    quantity = item.get("quantity")
    return SelectedItemView(
        returnItemId=str(item["returnItemId"]),
        orderLineReference=str(item.get("orderLineId") or ""),
        productReference=(
            str(item["productReference"]) if item.get("productReference") is not None else None
        ),
        quantity=quantity if isinstance(quantity, int) and not isinstance(quantity, bool) else None,
        reason=str(item["reason"]) if item.get("reason") is not None else None,
        condition=str(item["condition"]) if item.get("condition") is not None else None,
        packageReference=(
            str(item["packageReference"]) if item.get("packageReference") is not None else None
        ),
    )


@router.post("/{case_id}/selected-items", response_model=APIResponse[SelectedItems])
async def replace_case_selected_items(
    case_id: str,
    payload: SelectedItemsRequest,
    request: Request,
    _actor_id: str = Depends(require_associate_roles),
) -> APIResponse[SelectedItems]:
    """Record the case's selection and hold the quantity, or refuse it (plan sect. 12.4).

    **The write re-evaluates; the read did not promise.** Availability is
    recomputed inside the transaction that commits the selection, so two
    associates submitting the same last unit produce exactly one hold. The loser
    gets `409 QUANTITY_UNAVAILABLE` carrying the recomputed quantity per line,
    which is what lets the client re-render instead of guessing.

    **Idempotent by consequence rather than by a key.** The payload is the whole
    selection, so re-sending it changes nothing, moves no revision, and answers
    `changed: false`. The optional `contact` keeps that property: it appends a
    fact only where the value differs from what the case already holds.

    **`changed` is about the items and stays about the items.** A submission
    that alters only the branch associate answers `changed: false` and a moved
    revision, because the selection genuinely did not change; the contact is not
    part of the item ledger and `SelectedItems` does not carry it. The client
    re-reads the case, where `CaseProjection.facts` serves it like every other
    case-level detail.

    Gated on the associate roles rather than on read: naming the lines of a
    return is the associate's act, and a console viewer holding quantity out of
    a branch's reach is not a read. The contact is the same act, by the same
    person, in the same breath -- which is why it arrives here rather than
    through a second route the pane's one button would have to drive with no
    story for a half-failure.
    """
    case = await _owned_case(request, case_id)
    order_reference = _confirmed_order(case, case_id)
    tenant_id = str(getattr(request.state, "tenant_id", "default"))
    release = _active_release(request)
    ttl_seconds = release.return_case.item_reservation_ttl_seconds
    _reject_unpublished_terms(payload, release.selection_vocabulary)
    lines = await _source_lines(request, order_reference)
    selections = _selections(payload, lines, order_reference=order_reference)

    repository = resolve_operational_repository(request)
    try:
        outcome: SelectionOutcome = await repository.replace_case_line_selection(
            case_id=case_id,
            tenant_id=tenant_id,
            order_reference=order_reference,
            selections=selections,
            ordered_by_line={line.line_reference: line.ordered_quantity for line in lines},
            ttl_seconds=ttl_seconds,
        )
    except QuantityUnavailableError as unavailable:
        logger.info(
            "selected_items_refused_quantity_unavailable",
            extra={"case_id": case_id, "order_reference": order_reference},
        )
        raise _conflict(
            "QUANTITY_UNAVAILABLE",
            str(unavailable),
            # Recomputed inside the transaction that refused, so the client
            # re-renders from the figure that beat it rather than from the one
            # it was looking at.
            lines=[
                {"orderLineReference": line, "returnableQuantity": quantity}
                for line, quantity in sorted(unavailable.unavailable.items())
            ],
        ) from unavailable
    except LineAlreadyAuthorizedError as authorized:
        raise _conflict(
            "LINE_ALREADY_AUTHORIZED",
            str(authorized),
            lines=[
                {"orderLineReference": line, "returnRecordId": record}
                for line, record in sorted(authorized.order_line_references.items())
            ],
        ) from authorized

    await _record_return_contact(repository, case_id=case_id, contact=payload.contact)

    return APIResponse(
        data=SelectedItems(
            caseId=case_id,
            revision=outcome.revision,
            changed=outcome.changed,
            items=tuple(_selected_item_view(item) for item in outcome.items),
            lines=await _line_views(
                request,
                case_id=case_id,
                tenant_id=tenant_id,
                order_reference=order_reference,
                lines=lines,
            ),
        ),
        meta=_meta(request),
    )
