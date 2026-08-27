"""`/api/cases/{caseId}/order-lines` and `/selected-items`, over real HTTP.

Against an in-memory repository, so what is skipped is Mongo and nothing else:
the routes, the scoping, the source projection through the *real* active schema,
the availability arithmetic and the response models are all exercised. The
properties a database decides -- that two writers cannot both hold the last
unit, that an authorization and an expiry cannot both win -- are asserted in
`tests/operations/test_order_line_reservations_real_infra.py`, where a fake
would answer "yes" to every one of them.

Five things are proved here that nothing else proves:

* **The lines are the order's, not a fixture's.** They are projected from a
  `salesInv`-shaped document through the schema the runtime actually loads, so a
  release that re-binds `productDesc` breaks this test rather than a pane.
* **A foreign case is 404, not 403** -- on both routes, and for a case in
  another tenant as well as one belonging to another principal.
* **A case with no confirmed order is a 409, not an empty list.** Plan
  sect. 12.1 routes the pre-confirmation read through the candidate set.
* **The refusal carries the recomputed quantity.** A `409
  QUANTITY_UNAVAILABLE` that only said "no" would leave the client guessing,
  which is what the plan forbids by name.
* **An identical re-submission answers `changed: false`** and reports the same
  revision, which is the read side of "a no-op advances it by zero".
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api import order_lines as order_lines_module
from return_platform.api.order_lines import router
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    SelectionVocabularyConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import (
    DEFAULT_RETURN_CONFIGURATION_PATH,
    Settings,
)
from return_platform.operations.order_lines import (
    LineSelection,
    QuantityUnavailableError,
    SelectionOutcome,
    compute_order_line_availability,
)
from return_platform.security import roles as r
from return_platform.security.principal import Principal

TENANT = "tenant-a"
PRINCIPAL = "associate-1"
CASE = "case-1"
ORDER = "CQ363350"
NOW = datetime(2026, 8, 15, 9, 30, tzinfo=UTC)


def _sales_document() -> dict[str, Any]:
    """A `salesInv` document in the shape the reference extract has."""
    return {
        "_id": f"CHARLOTTE*{ORDER}",
        "salesHdrEventData": {"orderId": ORDER, "accountId": "CHARLOTTE"},
        "salesLines": [
            {
                "salesLnsEventData": {"lineNumber": "1", "lineType": "MP"},
                "lineData": {
                    "altCode1": "Q1685",
                    "productDesc": "16X25 SILV FLEX AIR DUCT R8.0",
                    "masterProductId": "3180140",
                    "orderQty": 3,
                    "netPrice": 146.306,
                },
            },
            {
                "salesLnsEventData": {"lineNumber": "2", "lineType": "MP"},
                "lineData": {
                    "altCode1": "Q9002",
                    "productDesc": "3IN BRASS GATE VALVE",
                    "masterProductId": "9110022",
                    "orderQty": 1,
                    "netPrice": 88.5,
                },
            },
        ],
    }


def _case(
    *,
    case_id: str = CASE,
    tenant_id: str = TENANT,
    principal_id: str = PRINCIPAL,
    order: str | None = ORDER,
) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "tenantId": tenant_id,
        "principalId": principal_id,
        "status": "GATHERING_INFO",
        "confirmedOrderReference": order,
        "version": 7,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


class _Collection:
    """Just enough of `AsyncCollection` for one indexed `find_one`."""

    def __init__(self, document: dict[str, Any] | None) -> None:
        self._document = document
        self.queries: list[Mapping[str, Any]] = []

    async def find_one(self, query: Mapping[str, Any]) -> dict[str, Any] | None:
        self.queries.append(query)
        if self._document is None:
            return None
        return self._document


class StubRepository:
    """Documents in, availability out -- through the real pure functions.

    `load_order_line_availability` is reproduced here over
    `compute_order_line_availability` rather than stubbed to a hand-built
    answer, for the reason `test_case_projection_route.py` reproduces the
    projection assembler: a hand-built answer would let the route look right
    over arithmetic that had stopped agreeing with it.

    It deliberately does **not** filter by tenant or principal in `get_case`.
    That mirrors the real method, and a stub that filtered would make the router
    look correct whether or not it checked anything.
    """

    def __init__(
        self,
        *,
        case: dict[str, Any] | None = None,
        sales: dict[str, Any] | None = None,
        items: list[dict[str, Any]] | None = None,
        statuses: dict[str, Any] | None = None,
        refuse: Mapping[str, int] | None = None,
        facts: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._case = case
        self.collection = _Collection(sales)
        self._items = items or []
        self._statuses = statuses or {}
        self._refuse = refuse
        self.written: list[Sequence[LineSelection]] = []
        self.ttl_seconds: list[int] = []
        #: The latest-per-name projection, as `latest_case_facts` serves it.
        self.facts: dict[str, dict[str, Any]] = facts or {}
        #: Every append, in order, so a test can assert what was *not* written.
        self.appended: list[dict[str, Any]] = []

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        if self._case is None or self._case["caseId"] != case_id:
            return None
        return self._case

    async def latest_case_facts(self, case_id: str) -> dict[str, dict[str, Any]]:
        return dict(self.facts)

    async def append_case_fact(self, **fact: Any) -> dict[str, Any]:
        """Insert-only, and projected forward, exactly as the real one is.

        Updating `facts` here rather than only recording the call is what lets a
        test submit twice and prove the second submission appends nothing --
        which is the property that keeps an unchanged re-post from bumping the
        revision and invalidating every cached projection.
        """
        self.appended.append(fact)
        self.facts[str(fact["fact_name"])] = {"value": fact["value"]}
        return fact

    async def source_dataset(self, dataset: str) -> _Collection:
        return self.collection

    async def load_order_line_availability(
        self,
        *,
        tenant_id: str,
        order_reference: str,
        viewing_case_id: str | None,
        ordered_by_line: Mapping[str, int | None],
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        return compute_order_line_availability(
            ordered_by_line=ordered_by_line,
            items=self._items,
            case_status_by_id=self._statuses,
            reservations=[],
            now=now or NOW,
            viewing_case_id=viewing_case_id,
        )

    async def replace_case_line_selection(
        self,
        *,
        case_id: str,
        tenant_id: str,
        order_reference: str,
        selections: Sequence[LineSelection],
        ordered_by_line: Mapping[str, int | None],
        ttl_seconds: int,
    ) -> SelectionOutcome:
        if self._refuse is not None:
            raise QuantityUnavailableError(
                order_reference=order_reference, unavailable=self._refuse
            )
        self.written.append(selections)
        self.ttl_seconds.append(ttl_seconds)
        # The second identical write is the no-op: it changes nothing and the
        # revision stays exactly where the first one left it.
        changed = len(self.written) == 1
        return SelectionOutcome(
            case_id=case_id,
            revision=8,
            changed=changed,
            items=tuple(
                {
                    "returnItemId": f"item-{selection.order_line_reference}",
                    "caseId": case_id,
                    "returnRecordId": None,
                    "orderLineId": selection.order_line_reference,
                    "productReference": selection.product_reference,
                    "quantity": selection.quantity,
                    "reason": selection.reason,
                    "condition": selection.condition,
                    "packageReference": selection.package_reference,
                }
                for selection in selections
            ),
            reservations=(),
        )


@pytest.fixture(autouse=True)
def _stub_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        order_lines_module,
        "resolve_operational_repository",
        lambda request: request.app.state.stub_repository,
    )


def _client(
    repository: StubRepository,
    *,
    tenant_id: str = TENANT,
    roles: frozenset[str] = frozenset({r.RETURN_ASSOCIATE}),
) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject=PRINCIPAL, roles=roles)
        request.state.tenant_id = tenant_id
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router)
    app.state.stub_repository = repository
    app.state.settings = Settings()
    app.state.resources = None
    app.state.return_configuration = LoadedReturnConfiguration(
        configuration=load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration,
        path=DEFAULT_RETURN_CONFIGURATION_PATH,
        sha256="0" * 64,
    )
    with TestClient(app) as client:
        yield client


def _get(repository: StubRepository, **kwargs: Any) -> Any:
    for client in _client(repository, **kwargs):
        return client.get(f"/api/cases/{CASE}/order-lines")
    raise AssertionError("the client fixture yielded nothing")


def _post(repository: StubRepository, body: dict[str, Any], **kwargs: Any) -> Any:
    for client in _client(repository, **kwargs):
        return client.post(f"/api/cases/{CASE}/selected-items", json=body)
    raise AssertionError("the client fixture yielded nothing")


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


def test_the_lines_project_from_the_real_order_with_a_returnable_quantity() -> None:
    response = _get(StubRepository(case=_case(), sales=_sales_document()))

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["caseId"] == CASE
    assert body["orderReference"] == ORDER
    assert [line["lineReference"] for line in body["lines"]] == ["1", "2"]
    first = body["lines"][0]
    assert first["sku"] == "Q1685"
    assert first["description"] == "16X25 SILV FLEX AIR DUCT R8.0"
    assert first["orderedQuantity"] == 3
    assert first["returnableQuantity"] == 3
    assert first["productReference"] == "3180140"
    # A decimal string, never a JSON number: a refund basis that round-trips
    # through binary floating point disagrees with the invoice by a cent.
    assert first["unitPrice"] == "146.306"


def test_the_order_is_located_by_the_path_the_schema_declares() -> None:
    """Not a literal in the route. A rebinding must move the query with it."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    _get(repository)
    assert repository.collection.queries == [{"salesHdrEventData.orderId": ORDER}]


def test_the_four_terms_travel_with_the_answer() -> None:
    """A pane that only had the total could not explain it to an associate."""
    response = _get(
        StubRepository(
            case=_case(),
            sales=_sales_document(),
            items=[
                {
                    "returnItemId": "i-1",
                    "caseId": "case-other",
                    "returnRecordId": "rec-1",
                    "orderLineId": "1",
                    "quantity": 2,
                }
            ],
            statuses={"case-other": "CLOSED"},
        )
    )

    line = response.json()["data"]["lines"][0]
    assert line["completedReturnQuantity"] == 2
    assert line["openAuthorizedQuantity"] == 0
    assert line["activeReservationQuantity"] == 0
    assert line["selfReservedQuantity"] == 0
    assert line["returnableQuantity"] == 1


def test_a_line_committed_beyond_its_ordered_quantity_reports_zero_and_the_flag() -> None:
    response = _get(
        StubRepository(
            case=_case(),
            sales=_sales_document(),
            items=[
                {
                    "returnItemId": "i-1",
                    "caseId": "case-other",
                    "returnRecordId": "rec-1",
                    "orderLineId": "2",
                    "quantity": 4,
                }
            ],
            statuses={"case-other": "CLOSED"},
        )
    )

    line = next(row for row in response.json()["data"]["lines"] if row["lineReference"] == "2")
    assert line["returnableQuantity"] == 0
    assert line["dataInconsistency"] == "COMMITMENTS_EXCEED_ORDERED_QUANTITY"


# ---------------------------------------------------------------------------
# Scoping: absent, never forbidden
# ---------------------------------------------------------------------------


def test_another_principals_case_is_reported_as_absent() -> None:
    response = _get(StubRepository(case=_case(principal_id="associate-2"), sales=_sales_document()))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"


def test_another_tenants_case_is_reported_as_absent() -> None:
    response = _get(StubRepository(case=_case(), sales=_sales_document()), tenant_id="tenant-b")
    assert response.status_code == 404


def test_a_case_that_does_not_exist_is_reported_as_absent() -> None:
    assert _get(StubRepository(case=None, sales=_sales_document())).status_code == 404


def test_the_writer_refuses_a_foreign_case_the_same_way() -> None:
    """A 403 here would confirm the case id exists just as surely as on the read."""
    response = _post(
        StubRepository(case=_case(principal_id="associate-2"), sales=_sales_document()),
        {"items": [{"orderLineReference": "1", "quantity": 1}]},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CASE_NOT_FOUND"


def test_a_case_with_no_confirmed_order_is_a_conflict_not_an_empty_list() -> None:
    response = _get(StubRepository(case=_case(order=None), sales=_sales_document()))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ORDER_NOT_CONFIRMED"


def test_an_order_the_source_does_not_hold_is_named_rather_than_shown_as_empty() -> None:
    response = _get(StubRepository(case=_case(), sales=None))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ORDER_NOT_IN_SOURCE"


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


def test_a_selection_is_written_with_the_product_resolved_from_the_source() -> None:
    """The client cannot name the product. It is a property of the order line."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(
        repository,
        {
            "items": [
                {
                    "orderLineReference": "1",
                    "quantity": 2,
                    "reason": "SHIPPING_DAMAGE",
                    "condition": "NEW_PACKAGING_OPENED",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["changed"] is True
    assert body["revision"] == 8
    assert body["items"] == [
        {
            "returnItemId": "item-1",
            "orderLineReference": "1",
            "productReference": "3180140",
            "quantity": 2,
            "reason": "SHIPPING_DAMAGE",
            "condition": "NEW_PACKAGING_OPENED",
            "packageReference": None,
        }
    ]
    written = repository.written[0][0]
    assert written.product_reference == "3180140"


# ---------------------------------------------------------------------------
# The reason and condition vocabularies come from the release (plan sect. 12.4)
# ---------------------------------------------------------------------------


def test_a_reason_the_release_does_not_publish_is_refused() -> None:
    """The whole point of moving the vocabulary into configuration.

    Before this, `SelectedItemRequest` bounded the length and nothing else, so
    any string up to 128 characters became the recorded reason a line came back.
    """
    response = _post(
        StubRepository(case=_case(), sales=_sales_document()),
        {"items": [{"orderLineReference": "1", "quantity": 1, "reason": "BECAUSE_I_SAID_SO"}]},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "SELECTION_TERM_NOT_PUBLISHED"
    assert detail["reasons"] == ["BECAUSE_I_SAID_SO"]
    assert detail["conditions"] == []
    # The catalogue travels with the refusal so the client re-renders the picker
    # rather than guessing what it may send.
    assert "SHIPPING_DAMAGE" in detail["publishedReasons"]


def test_a_condition_the_release_does_not_publish_is_refused() -> None:
    response = _post(
        StubRepository(case=_case(), sales=_sales_document()),
        {"items": [{"orderLineReference": "1", "quantity": 1, "condition": "SLIGHTLY_BENT"}]},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["conditions"] == ["SLIGHTLY_BENT"]
    assert "NEW_IN_ORIGINAL_PACKAGING" in detail["publishedConditions"]


def test_the_published_catalogue_is_matched_case_insensitively() -> None:
    """A client sending `shipping_damage` means the same thing, and the value is
    stored as the client sent it -- the catalogue decides admission, not spelling."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(
        repository,
        {
            "items": [
                {
                    "orderLineReference": "1",
                    "quantity": 1,
                    "reason": "shipping_damage",
                    "condition": " used ",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert repository.written[0][0].reason == "shipping_damage"


def test_an_unpublished_catalogue_refuses_nothing() -> None:
    """A release cut before the block keeps today's free-text behaviour.

    Refusing every selection instead would take a branch's returns offline over
    a configuration key nobody had been asked for -- the reason the field
    defaults empty rather than to a copy of the catalogue.
    """
    repository = StubRepository(case=_case(), sales=_sales_document())
    packaged = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    for client in _client(repository):
        client.app.state.return_configuration = LoadedReturnConfiguration(
            configuration=packaged.model_copy(
                update={"selection_vocabulary": SelectionVocabularyConfiguration()}
            ),
            path=DEFAULT_RETURN_CONFIGURATION_PATH,
            sha256="0" * 64,
        )
        response = client.post(
            f"/api/cases/{CASE}/selected-items",
            json={
                "items": [{"orderLineReference": "1", "quantity": 1, "reason": "ANYTHING_AT_ALL"}]
            },
        )
        assert response.status_code == 200, response.text
        assert repository.written[0][0].reason == "ANYTHING_AT_ALL"
        return
    raise AssertionError("the client fixture yielded nothing")


def test_the_response_carries_the_refreshed_lines_so_the_pane_need_not_poll() -> None:
    response = _post(
        StubRepository(case=_case(), sales=_sales_document()),
        {"items": [{"orderLineReference": "1", "quantity": 1}]},
    )
    assert [line["lineReference"] for line in response.json()["data"]["lines"]] == ["1", "2"]


def test_the_reservation_ttl_comes_from_the_active_release() -> None:
    """Configuration, not a source constant (plan sect. 12.3)."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    _post(repository, {"items": [{"orderLineReference": "1", "quantity": 1}]})
    expected = load_return_configuration(
        DEFAULT_RETURN_CONFIGURATION_PATH
    ).configuration.return_case.item_reservation_ttl_seconds
    assert repository.ttl_seconds == [expected]


def test_an_identical_resubmission_reports_that_nothing_changed() -> None:
    repository = StubRepository(case=_case(), sales=_sales_document())
    body = {"items": [{"orderLineReference": "1", "quantity": 1}]}
    for client in _client(repository):
        first = client.post(f"/api/cases/{CASE}/selected-items", json=body)
        second = client.post(f"/api/cases/{CASE}/selected-items", json=body)
        break
    assert first.json()["data"]["changed"] is True
    assert second.json()["data"]["changed"] is False
    assert second.json()["data"]["revision"] == first.json()["data"]["revision"]


def test_an_empty_selection_is_a_withdrawal_rather_than_a_bad_request() -> None:
    """Replace-set semantics: `items: []` means "I am returning nothing"."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(repository, {"items": []})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"] == []


def test_a_line_the_order_does_not_have_is_refused_rather_than_reserved() -> None:
    """A hold on a phantom line is quantity nobody can see to release."""
    response = _post(
        StubRepository(case=_case(), sales=_sales_document()),
        {"items": [{"orderLineReference": "99", "quantity": 1}]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ORDER_LINE_NOT_ON_ORDER"


def test_the_same_line_named_twice_is_refused() -> None:
    response = _post(
        StubRepository(case=_case(), sales=_sales_document()),
        {
            "items": [
                {"orderLineReference": "1", "quantity": 1},
                {"orderLineReference": "1", "quantity": 1},
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ORDER_LINE_REPEATED"


def test_a_refused_selection_carries_the_recomputed_quantity() -> None:
    """`409 QUANTITY_UNAVAILABLE` must let the client re-render, not guess."""
    response = _post(
        StubRepository(case=_case(), sales=_sales_document(), refuse={"1": 1}),
        {"items": [{"orderLineReference": "1", "quantity": 3}]},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "QUANTITY_UNAVAILABLE"
    assert detail["retryable"] is False
    assert detail["lines"] == [{"orderLineReference": "1", "returnableQuantity": 1}]


def test_a_viewer_may_read_the_lines_but_not_hold_quantity() -> None:
    """Naming the lines of a return is the associate's act, not a read."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    viewer = frozenset({r.CONSOLE_VIEWER})
    assert _get(repository, roles=viewer).status_code == 200
    refused = _post(
        repository, {"items": [{"orderLineReference": "1", "quantity": 1}]}, roles=viewer
    )
    assert refused.status_code == 403


def test_a_request_body_may_not_name_a_server_derived_field() -> None:
    response = _post(
        StubRepository(case=_case(), sales=_sales_document()),
        {"items": [{"orderLineReference": "1", "quantity": 1, "productReference": "FORGED"}]},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# The branch associate (case-level, on the fact log)
# ---------------------------------------------------------------------------


def _line() -> dict[str, Any]:
    return {"orderLineReference": "1", "quantity": 1}


def _appended(repository: StubRepository) -> dict[str, Any]:
    """Fact name -> value, for everything this request appended."""
    return {str(fact["fact_name"]): fact["value"] for fact in repository.appended}


def test_the_branch_associate_lands_on_the_case_fact_log() -> None:
    """Case-level, so it goes where every other case-level detail goes.

    Not onto the selected item: one associate raises one return, and a contact
    per line would let a two-line selection carry two different people with no
    rule for choosing between them. The fact log is also the one thing
    `draft_support_request` reads, which is the whole point of collecting it.
    """
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(
        repository,
        {
            "items": [_line()],
            "contact": {
                "name": "D. Reyes",
                "email": "d.reyes@branch.example",
                "phone": "704-555-0134",
            },
        },
    )

    assert response.status_code == 200, response.text
    assert _appended(repository) == {
        "branch_associate_name": "D. Reyes",
        "branch_associate_email": "d.reyes@branch.example",
        "branch_associate_phone": "704-555-0134",
    }
    # Recorded as what it is: an associate typed it on Channel A. Not `SYSTEM`
    # -- nothing derived it -- and not evidence of anything having been checked.
    assert {str(fact["channel"].value) for fact in repository.appended} == {"CHANNEL_A"}
    assert {str(fact["acquisition_method"].value) for fact in repository.appended} == {"STATED"}
    # And nowhere near the line, which carries only what the writer derived.
    (selection,) = repository.written
    assert [item.order_line_reference for item in selection] == ["1"]


def test_a_selection_with_no_contact_is_recorded_exactly_as_before() -> None:
    """Optional by operator instruction. Absent blocks nothing and writes nothing."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(repository, {"items": [_line()]})

    assert response.status_code == 200, response.text
    assert repository.appended == []


def test_a_partly_stated_contact_records_only_what_was_stated() -> None:
    """No default, ever. A name with no email is a name with no email."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(repository, {"items": [_line()], "contact": {"name": "D. Reyes"}})

    assert response.status_code == 200, response.text
    assert _appended(repository) == {"branch_associate_name": "D. Reyes"}


def test_an_email_that_is_not_an_email_is_refused() -> None:
    """Shape, not existence -- and refused here as well as at entry.

    A phone number typed into the email box produces a Support request nobody
    can answer, and the console cannot be the only thing standing between that
    typo and a carrier.
    """
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(repository, {"items": [_line()], "contact": {"email": "704-555-0134"}})

    assert response.status_code == 422, response.text
    # Nothing was written, including the selection: the body never validated.
    assert repository.appended == []
    assert repository.written == []


def test_an_unchanged_contact_is_not_appended_twice() -> None:
    """The replace-set shape invites re-posting, and the log is insert-only.

    Three identical facts per re-submission would bump the case revision and
    invalidate every cached projection for a case in which nothing changed.
    """
    repository = StubRepository(case=_case(), sales=_sales_document())
    body = {"items": [_line()], "contact": {"name": "D. Reyes", "email": "", "phone": ""}}

    assert _post(repository, body).status_code == 200
    first = len(repository.appended)
    assert _appended(repository) == {"branch_associate_name": "D. Reyes"}

    for client in _client(repository):
        again = client.post(f"/api/cases/{CASE}/selected-items", json=body)
    assert again.status_code == 200, again.text
    assert len(repository.appended) == first


def test_a_cleared_contact_is_recorded_as_a_retraction() -> None:
    """The log is append-only, so a correction is an empty value over the old one.

    Read back, an empty value is absent everywhere -- `_stated` in the support
    draft and `projectedFactString` in the console both treat it as nothing --
    so the associate sees the box they cleared stay cleared rather than watching
    a mistake reappear on the next poll.
    """
    repository = StubRepository(
        case=_case(),
        sales=_sales_document(),
        facts={"branch_associate_email": {"value": "wrong@branch.example"}},
    )
    response = _post(repository, {"items": [_line()], "contact": {"email": ""}})

    assert response.status_code == 200, response.text
    assert _appended(repository) == {"branch_associate_email": ""}


def test_clearing_something_never_recorded_writes_nothing() -> None:
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(
        repository, {"items": [_line()], "contact": {"name": "", "email": "", "phone": ""}}
    )

    assert response.status_code == 200, response.text
    assert repository.appended == []


def test_the_return_details_the_support_template_reads_are_recorded() -> None:
    """The three lines that rendered "Not available" beside a case that knew.

    `compose_support_handoff` reads `product_presence`, `requested_resolution`
    and `associate_notes` off the fact log, and nothing on the case path wrote
    any of them -- so every handoff asked Support to decide from a form with
    those three blank. The names asserted here are that contract, not a
    preference: a fourth spelling would write a fact nothing renders, which is
    the defect one level down.
    """
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(
        repository,
        {
            "items": [_line()],
            "returnDetails": {
                "productPresence": "PRESENT_AT_BRANCH",
                "requestedResolution": "CREDIT",
                "notes": "Customer opened the carton at the jobsite.",
            },
        },
    )

    assert response.status_code == 200, response.text
    assert _appended(repository) == {
        "product_presence": "PRESENT_AT_BRANCH",
        "requested_resolution": "CREDIT",
        "associate_notes": "Customer opened the carton at the jobsite.",
    }


def test_return_details_carry_no_return_method() -> None:
    """Support decides the method, per RMA, and intake may not say it for them.

    `record_support_outcome` writes it onto the record and the projection reads
    it from there. A method accepted here would satisfy the case's honest
    `awaiting: RETURN_METHOD` without anybody having answered it -- the screen
    would go quiet and no RMA would exist.
    """
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(
        repository,
        {"items": [_line()], "returnDetails": {"returnMethod": "PPL"}},
    )

    assert response.status_code == 422, response.text
    assert repository.appended == []
    assert repository.written == []


def test_return_details_and_a_contact_are_recorded_from_one_submission() -> None:
    """One form, one write. They are separate statements about the same case."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(
        repository,
        {
            "items": [_line()],
            "contact": {"name": "D. Reyes"},
            "returnDetails": {"productPresence": "OFFSITE_CUSTOMER_JOBSITE"},
        },
    )

    assert response.status_code == 200, response.text
    assert _appended(repository) == {
        "branch_associate_name": "D. Reyes",
        "product_presence": "OFFSITE_CUSTOMER_JOBSITE",
    }


def test_unchanged_return_details_are_not_appended_twice() -> None:
    """Same rule as the contact, and for the same reason: the log is insert-only."""
    repository = StubRepository(case=_case(), sales=_sales_document())
    body = {"items": [_line()], "returnDetails": {"productPresence": "PRESENT_AT_BRANCH"}}

    assert _post(repository, body).status_code == 200
    first = len(repository.appended)
    assert _post(repository, body).status_code == 200

    assert len(repository.appended) == first


def test_a_refused_selection_records_no_contact() -> None:
    """Details for a return that was never recorded are details about nothing."""
    repository = StubRepository(case=_case(), sales=_sales_document(), refuse={"1": 0})
    response = _post(
        repository,
        {"items": [_line()], "contact": {"name": "D. Reyes"}},
    )

    assert response.status_code == 409
    assert repository.appended == []


def test_the_contact_is_trimmed_rather_than_stored_as_typed() -> None:
    repository = StubRepository(case=_case(), sales=_sales_document())
    response = _post(
        repository,
        {"items": [_line()], "contact": {"name": "  D. Reyes  ", "email": " d@b.example "}},
    )

    assert response.status_code == 200, response.text
    assert _appended(repository) == {
        "branch_associate_name": "D. Reyes",
        "branch_associate_email": "d@b.example",
    }


def test_a_contact_may_not_be_stated_on_a_line() -> None:
    """The shape is the guard: a per-line contact is unsayable, not merely wrong."""
    response = _post(
        StubRepository(case=_case(), sales=_sales_document()),
        {"items": [{"orderLineReference": "1", "quantity": 1, "name": "D. Reyes"}]},
    )
    assert response.status_code == 422
