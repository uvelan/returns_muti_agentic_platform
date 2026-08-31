"""Acceptance item 17 under AMENDMENT-4: eventually once, **never** atomically.

The observable item 17 names is unchanged by the amendment — "on restart the
artifact is relayed once, bound to the correct return record, with no duplicate
omc write". What changed is the mechanism: §5 said the mirror row was "enqueued
in the artifact-persistence transaction", and **there is no such transaction**.
The amendment restates it as *convergently idempotent in a crash-safe order —
merge → mirror row → outbox row, each a no-op on repeat — driven to completion
by the classify command's at-least-once redelivery*, and says outright what that
gives up: **the window between the merge and the outbox row is now explicitly a
recoverable gap rather than an impossible one.**

The brief's instruction follows from that: assert the mirror as *eventually
once, never atomically*, and **do not write a scenario that asserts atomicity**.
So this file asserts both halves, and the second half is the one no other test
makes:

* **Never atomically** — after a crash between the merge and the mirror, the
  intermediate state is *observable*: the record carries the artifact and there
  is no mirror row and no outbox row. A transaction would make that state
  unrepresentable. Asserting it is asserting the amendment's honesty; a suite
  that could not tell this state from the finished one would be equally green
  against a claim of atomicity that production does not implement.
* **Eventually once** — the redelivery that follows completes the sequence and
  lands exactly one mirror row and exactly one outbox row, and a third delivery
  changes neither.

**Against the real mirror, not a recording double.** The classification suite's
own mirror tests use `_RecordingOmc`, which is right for what they assert (the
call is made, under the derived identity, once). It cannot show a crash-safe
*order*, because it has no rows and no outbox. This builds `DurableOmcMirror`
over a real `OperationalRepository` with the two uniqueness constraints in
force, and reads the two collections a production process would actually write.

**Why the redelivery here is the load-bearing one.** On it the merge writes
nothing — the record already carries `1Z-AAA` from the crashed attempt — so this
is exactly the path on which a mirror gated on "did the merge write anything"
would be skipped, and skipped permanently, because there will never be an
attempt on which the merge writes again. `test_support_message_classification.py`
covers that gating with the post-crash state set up by hand; this reaches the
same state by **actually crashing**, which is the difference between testing the
recovery and testing a fixture that resembles it.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import pytest_asyncio

from return_platform.configuration.settings import Settings
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.analysis_records import (
    SupportAnalysisRecordStore,
)
from return_platform.operations.return_support.message_classification import (
    OMC_RETURN_UPDATE_TOPIC,
)
from return_platform.operations.return_support.omc_mirror import DurableOmcMirror
from tests.operations.mongo_double import FakeClient
from tests.operations.test_support_message_classification import (  # noqa: PLC2701
    CASE_ID,
    _analyse,
    _analyser,
    _record_document,
)

#: The identity the mirror derives for this artifact. Pinned as a literal for
#: the reason the classification suite pins it: re-deriving it here would
#: compare the function with itself, and a prefix assertion would pass for a key
#: with a random tail -- which is the shape that mirrors one business change
#: twice with the receiver holding nothing to dedupe on.
DELIVERY_ID = "omc-return-update:6ba35ba5-c726-5c0e-843b-89f1ee902019"

_ARTIFACT = {
    "records": [],
    "artifacts": [{"artifactType": "TRACKING", "value": "1Z-AAA", "binding": "RMA-1"}],
}


class _CrashOnce:
    """The real mirror, with the process taken away on its first call.

    Wrapping rather than substituting: every attempt after the first runs the
    genuine `DurableOmcMirror`, so the recovery being asserted is production's
    and not a fixture's idea of it. The crash lands **after** the merge has
    committed and **before** any row is written, which is the window
    AMENDMENT-4 declares recoverable.
    """

    def __init__(self, real: DurableOmcMirror) -> None:
        self._real = real
        self.crashed = False

    async def enqueue_omc_update(self, **kwargs: Any) -> str:
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("the worker was taken away between the merge and the mirror")
        return await self._real.enqueue_omc_update(**kwargs)


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest_asyncio.fixture
async def repository(mongo: FakeClient, test_settings: Settings) -> OperationalRepository:
    """A real repository with the omc uniqueness in force.

    The two indexes are created here rather than by `ensure_indexes()`, which
    cannot run against the double -- an unrelated step calls
    `index_information()`. `test_support_omc_mirror.py` pins that these are the
    same two `ensure_indexes` declares, so the copy cannot drift.
    """
    made = OperationalRepository(cast(Any, mongo), test_settings)
    await made.omc_command_records.create_index("commandId", unique=True)
    await made.omc_command_records.create_index("idempotencyKey", unique=True)
    return made


@pytest_asyncio.fixture
async def analysis(mongo: FakeClient, test_settings: Settings) -> SupportAnalysisRecordStore:
    store = SupportAnalysisRecordStore(cast(Any, mongo), test_settings)
    await store.ensure_indexes()
    return store


async def _rows(repository: OperationalRepository) -> tuple[list[Any], list[Any]]:
    """`(mirror rows, outbox rows for this topic)` — the two collections."""
    mirrors = [row async for row in repository.omc_command_records.find({})]
    outbox = [
        row
        for row in await repository.list_integration_commands(CASE_ID)
        if row["topic"] == OMC_RETURN_UPDATE_TOPIC
    ]
    return mirrors, outbox


@pytest.mark.asyncio
async def test_a_crash_between_the_merge_and_the_mirror_converges_to_exactly_one(
    analysis: SupportAnalysisRecordStore, repository: OperationalRepository
) -> None:
    analyser, parts = _analyser(
        analysis, extraction=_ARTIFACT, stored_records=[_record_document("RMA-1", "rr-1")]
    )
    mirror = _CrashOnce(
        DurableOmcMirror(repository, topic=OMC_RETURN_UPDATE_TOPIC, actor_id="acceptance")
    )
    analyser._omc = mirror  # noqa: SLF001 - the real mirror, behind a crash

    # --- attempt 1: the merge commits, then the process is taken away --------
    with pytest.raises(RuntimeError):
        await _analyse(analyser)

    assert mirror.crashed, "the crash never happened, so nothing below is a recovery"
    assert parts["records"].updates == [("rr-1", {"trackingReference": "1Z-AAA"})], (
        "the merge did not commit before the crash, so this is not the window "
        "AMENDMENT-4 describes -- it is a failure earlier in the sequence, and the "
        "redelivery below would be recovering from something else"
    )

    mirrors, outbox = await _rows(repository)
    # **The `never atomically` half.** This state exists. A transaction would
    # make it unrepresentable, and a scenario that could not distinguish it from
    # the finished state would go green against a claim of atomicity the
    # platform does not implement.
    assert mirrors == [], (
        "a mirror row survives a crash that happened before the mirror ran -- then "
        "the order is not merge -> mirror -> outbox and the amendment's crash-safe "
        "claim is describing something else"
    )
    assert outbox == []

    # --- attempt 2: at-least-once redelivery, and the merge writes nothing ---
    parts["records"].updates.clear()
    await _analyse(analyser)

    assert parts["records"].updates == [], (
        "the merge wrote again on the redelivery. That makes this the easy path, "
        "not the one AMENDMENT-4 is about: the whole hazard is that the merge is a "
        "no-op on every attempt after the first, so a mirror gated on it is lost "
        "for good on the exact retry meant to recover it."
    )

    mirrors, outbox = await _rows(repository)
    assert [row["idempotencyKey"] for row in mirrors] == [DELIVERY_ID]
    assert [row["idempotencyKey"] for row in outbox] == [DELIVERY_ID]
    assert mirrors[0]["requestPayload"]["returnRecordId"] == "rr-1"
    # The outbox row points at the row actually on file, whoever wrote it.
    assert outbox[0]["payload"]["commandId"] == mirrors[0]["commandId"]

    # --- attempt 3: a further redelivery changes neither collection ----------
    await _analyse(analyser)
    again_mirrors, again_outbox = await _rows(repository)
    assert [row["idempotencyKey"] for row in again_mirrors] == [DELIVERY_ID]
    assert [row["idempotencyKey"] for row in again_outbox] == [DELIVERY_ID]
    assert again_mirrors[0]["commandId"] == mirrors[0]["commandId"], (
        "a redelivery replaced the mirror row rather than being a no-op on it -- "
        "the receiver would see one business change under two command ids"
    )


@pytest.mark.asyncio
async def test_the_uncrashed_path_writes_the_same_two_rows(
    analysis: SupportAnalysisRecordStore, repository: OperationalRepository
) -> None:
    """The control: convergence is not the crash producing a different answer.

    Without it, the scenario above establishes only that *something* ends up in
    the two collections. What makes "eventually once" a claim is that the
    crashed-and-recovered run lands the **same** two rows the clean run does.
    """
    analyser, _parts = _analyser(
        analysis, extraction=_ARTIFACT, stored_records=[_record_document("RMA-1", "rr-1")]
    )
    analyser._omc = DurableOmcMirror(
        repository, topic=OMC_RETURN_UPDATE_TOPIC, actor_id="acceptance"
    )  # noqa: SLF001
    await _analyse(analyser)

    mirrors, outbox = await _rows(repository)
    assert [row["idempotencyKey"] for row in mirrors] == [DELIVERY_ID]
    assert [row["idempotencyKey"] for row in outbox] == [DELIVERY_ID]
    assert outbox[0]["payload"]["commandId"] == mirrors[0]["commandId"]
