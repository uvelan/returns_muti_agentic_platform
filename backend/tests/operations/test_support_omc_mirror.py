"""The real `omc.return.update` mirror, against a real repository.

Phase 1b item B. Before this, `enqueue_omc_update` existed as a `Protocol`, one
call site and a test stub; the analyser's `omc` parameter defaulted to `None`.
Production had no implementation and the tests could not tell, because the tests
were the implementation.

So this file deliberately does **not** use a double for the thing under test. It
builds a real `OperationalRepository` over the mongo double, puts the unique
`commandId` / `idempotencyKey` constraints in force, and asserts against the two
collections a production process would actually write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

import return_platform.operations.repository as repository_module
from return_platform.configuration.settings import Settings
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.message_classification import (
    OMC_RETURN_UPDATE_TOPIC,
)
from return_platform.operations.return_support.omc_mirror import (
    OMC_RECORD_SUPPORT_ARTIFACT,
    DurableOmcMirror,
    derive_omc_delivery_id,
)
from tests.operations.mongo_double import FakeClient

CASE_ID = "case-77"
EVENT_ID = "sev-77"
ACTOR = "support-message-analysis"


@pytest_asyncio.fixture
async def repository() -> OperationalRepository:
    """A real repository over the mongo double, with the omc uniqueness in force.

    The two indexes are created here rather than by `ensure_indexes()`, which
    cannot run against the double at all -- an unrelated step on the `events`
    collection calls `index_information()`, which the double does not implement.
    They are the same two, spelled the same way;
    `test_production_still_declares_the_uniqueness_this_relies_on` is what stops
    the copy drifting from `ensure_indexes`.
    """
    made = OperationalRepository(FakeClient(), Settings())
    await made.omc_command_records.create_index("commandId", unique=True)
    await made.omc_command_records.create_index("idempotencyKey", unique=True)
    return made


def test_production_still_declares_the_uniqueness_this_relies_on() -> None:
    """The fixture copies two lines out of `ensure_indexes`. This pins them.

    Once-only here is a database constraint, not a check in application code --
    which is the right design and also means the tests below prove nothing if
    production stops declaring it. Read out of the source because the double
    cannot run `ensure_indexes`, so there is no runtime way to ask.
    """
    source = Path(repository_module.__file__).read_text(encoding="utf-8")
    for field in ("commandId", "idempotencyKey"):
        assert (
            f'await self.omc_command_records.create_index("{field}", unique=True)' in source
        ), field


def _mirror(repository: OperationalRepository) -> DurableOmcMirror:
    return DurableOmcMirror(repository, topic=OMC_RETURN_UPDATE_TOPIC, actor_id=ACTOR)


def _payload(**overrides: Any) -> dict[str, Any]:
    body = {
        "caseId": CASE_ID,
        "returnRecordId": "rr-1",
        "artifactType": "TRACKING",
        "value": "1Z-AAA",
        "supportEventId": EVENT_ID,
    }
    body.update(overrides)
    return body


async def _enqueue(mirror: DurableOmcMirror, delivery_id: str, **overrides: Any) -> str:
    return await mirror.enqueue_omc_update(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        delivery_id=delivery_id,
        payload=_payload(**overrides),
    )


# --------------------------------------------------------------------------- #
# The derivation
# --------------------------------------------------------------------------- #


def test_the_delivery_id_is_the_exact_derived_value() -> None:
    """Pinned as a literal, not re-derived.

    Re-deriving here would compare the function with itself and stay green under
    any change to it -- including a change that made the key random. The literal
    is uuid5 over the length-prefixed
    ("case-77", "sev-77", "rr-1", "TRACKING", "1Z-AAA").
    """
    assert (
        derive_omc_delivery_id(
            case_id=CASE_ID,
            support_event_id=EVENT_ID,
            return_record_id="rr-1",
            artifact_type="TRACKING",
            value="1Z-AAA",
        )
        == "omc-return-update:448695e5-b59e-5f6b-b876-14e202c250ce"
    )


def test_a_value_carrying_the_separator_cannot_forge_a_part_boundary() -> None:
    """The collision test, with inputs that can actually collide.

    Two conditions are needed and the previous generation of these tests only
    ever had one. The varied parts must be **adjacent** -- nothing else can
    collapse into a neighbour -- *and* one of them must be able to **contain the
    separator**, or the join produces different strings whether or not the parts
    are length-prefixed and the test passes with the prefixes deleted.

    `value` is the part that satisfies the second condition, and not by accident:
    it is a model's reading of text a person on the far end of a support channel
    typed, so it can contain `|` and `:` because they can type them. This is the
    one part of the key an outsider has any influence over, so it is the one the
    boundary has to hold against.

    The shift is between `artifact_type` and `value`, which are adjacent:
    `("TRACKING|a", "b")` against `("TRACKING", "a|b")`. Length-prefixed these
    are `...|10:TRACKING|a|1:b` and `...|8:TRACKING|3:a|b`; bare-joined, both
    render `...|TRACKING|a|b`.
    """
    left = derive_omc_delivery_id(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        return_record_id="rr-1",
        artifact_type="TRACKING|a",
        value="b",
    )
    right = derive_omc_delivery_id(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        return_record_id="rr-1",
        artifact_type="TRACKING",
        value="a|b",
    )
    assert left != right


def test_every_part_of_the_identity_changes_the_key() -> None:
    """Each part is load-bearing, one at a time.

    A key that ignored `return_record_id` would mirror an artifact onto whichever
    record happened to be enqueued first; one that ignored `support_event_id`
    would silently collapse two separate support messages into one delivery.
    """
    base = dict(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        return_record_id="rr-1",
        artifact_type="TRACKING",
        value="1Z-AAA",
    )
    reference = derive_omc_delivery_id(**base)
    for part, other in (
        ("case_id", "case-78"),
        ("support_event_id", "sev-78"),
        ("return_record_id", "rr-2"),
        ("artifact_type", "LABEL"),
        ("value", "1Z-BBB"),
    ):
        assert derive_omc_delivery_id(**{**base, part: other}) != reference, part


# --------------------------------------------------------------------------- #
# The two writes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_one_mirror_row_and_one_outbox_row_land_under_the_same_key(
    repository: OperationalRepository,
) -> None:
    """Both halves of sect. 5: mirrored to `omc_command_records`, delivered by
    the outbox on `omc.return.update`."""
    delivery_id = derive_omc_delivery_id(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        return_record_id="rr-1",
        artifact_type="TRACKING",
        value="1Z-AAA",
    )
    returned = await _enqueue(_mirror(repository), delivery_id)
    assert returned == delivery_id

    rows = await repository.list_integration_commands(CASE_ID)
    assert [row["topic"] for row in rows] == [OMC_RETURN_UPDATE_TOPIC]
    assert rows[0]["idempotencyKey"] == delivery_id
    assert rows[0]["aggregateId"] == CASE_ID
    assert rows[0]["payload"]["deliveryId"] == delivery_id
    assert rows[0]["payload"]["value"] == "1Z-AAA"

    stored = await repository.omc_command_records.find_one({"idempotencyKey": delivery_id})
    assert stored is not None
    assert stored["operation"] == OMC_RECORD_SUPPORT_ARTIFACT
    assert stored["caseId"] == CASE_ID
    assert stored["supportEventId"] == EVENT_ID
    assert stored["returnRecordId"] == "rr-1"
    assert stored["status"] == "PENDING"
    assert stored["createdBy"] == ACTOR
    # `sessionId` is null on purpose: a case-plane work item has no session, and
    # putting the case id in a field named `sessionId` would be a lie on file.
    assert stored["sessionId"] is None
    # The outbox row points back at the row on file, so a delivery can always be
    # traced to its mirror.
    assert rows[0]["payload"]["commandId"] == stored["commandId"]


@pytest.mark.asyncio
async def test_the_same_delivery_identity_mirrors_once_however_often_it_is_retried(
    repository: OperationalRepository,
) -> None:
    """Idempotence is what replaces the transaction sect. 5 asks for.

    Three enqueues of the same identity -- the shape of an at-least-once outbox
    redelivering a classify command. One row in each collection, and the second
    and third calls still return the key, because "already mirrored" is still
    mirrored and a caller that recorded nothing would under-report what happened.
    """
    delivery_id = derive_omc_delivery_id(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        return_record_id="rr-1",
        artifact_type="TRACKING",
        value="1Z-AAA",
    )
    mirror = _mirror(repository)
    assert [await _enqueue(mirror, delivery_id) for _ in range(3)] == [delivery_id] * 3

    assert len(await repository.list_integration_commands(CASE_ID)) == 1
    stored = [
        document
        async for document in repository.omc_command_records.find(
            {"idempotencyKey": delivery_id}
        )
    ]
    assert len(stored) == 1
    # And the row is the *first* one: a retry must not reset an in-flight
    # delivery's status or attempt count back to PENDING/0 underneath the worker
    # draining it.
    assert stored[0]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_a_second_artifact_on_the_same_event_gets_its_own_row(
    repository: OperationalRepository,
) -> None:
    """Idempotence must not become collapse.

    A test that only ever enqueues one identity cannot tell "written once"
    apart from "writes at most one row, ever".
    """
    mirror = _mirror(repository)
    first = derive_omc_delivery_id(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        return_record_id="rr-1",
        artifact_type="TRACKING",
        value="1Z-AAA",
    )
    second = derive_omc_delivery_id(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        return_record_id="rr-1",
        artifact_type="LABEL",
        value="LBL-9",
    )
    await _enqueue(mirror, first)
    await _enqueue(mirror, second, artifactType="LABEL", value="LBL-9")

    rows = await repository.list_integration_commands(CASE_ID)
    assert sorted(row["idempotencyKey"] for row in rows) == sorted([first, second])
    assert len({row["payload"]["commandId"] for row in rows}) == 2


@pytest.mark.asyncio
async def test_the_outbox_row_names_the_command_that_is_actually_on_file(
    repository: OperationalRepository,
) -> None:
    """The crash window, from the other side -- and a blind spot found by injection.

    The first attempt wrote the mirror row and died before enqueuing. The retry
    finds the row already there, so its own insert is a no-op. If the outbox
    payload were built from the id this attempt computed rather than from the id
    on file, the delivery would carry a `commandId` naming a record that does not
    exist, and the mirror the outbox row is supposed to reference would be
    unreachable from it.

    Set up as that state: a mirror row pre-seeded under the delivery id with a
    `commandId` this call could not have produced.
    """
    delivery_id = derive_omc_delivery_id(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        return_record_id="rr-1",
        artifact_type="TRACKING",
        value="1Z-AAA",
    )
    survivor = "command-written-by-the-attempt-that-died"
    await repository.omc_command_records.insert_one(
        {
            "_id": survivor,
            "commandId": survivor,
            "idempotencyKey": delivery_id,
            "operation": OMC_RECORD_SUPPORT_ARTIFACT,
            "status": "PENDING",
        }
    )

    await _enqueue(_mirror(repository), delivery_id)

    rows = await repository.list_integration_commands(CASE_ID)
    assert [row["payload"]["commandId"] for row in rows] == [survivor]
    # And the surviving row was not overwritten by this attempt's document.
    stored = await repository.omc_command_records.find_one({"idempotencyKey": delivery_id})
    assert stored is not None and stored["commandId"] == survivor
