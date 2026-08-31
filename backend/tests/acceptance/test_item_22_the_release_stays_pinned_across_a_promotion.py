"""Acceptance item 22 — the analysis release stays pinned across a promotion.

Item 22 has three clauses. Two are covered in-slice and ACC audited them by
injection rather than duplicating them (see
`.plan/acceptance/items-21-22-context-and-pinning.md`):

* *compaction keeps all pinned facts* — `test_a_pinned_name_survives_a_budget_
  that_fits_nothing_else`;
* *and loses none* — `test_what_the_budget_leaves_out_is_named_never_dropped`,
  which is the sharper of the two: a fact that does not fit is **named in
  `omitted_fact_ids`**, so "the model did not get this" is recorded rather than
  inferred from an absence.

The third clause — **the analysis release stays pinned across a mid-retry config
promotion** — is not covered anywhere. `test_a_crash_between_the_two_stages_
resumes_without_reclassifying` builds the resumed analyser with the *same*
release, so it proves the classification is reused and says nothing about what
happens when a release is promoted between the crash and the retry. That is the
realistic operational sequence: a worker dies mid-case, a release ships, and a
different worker picks the command up.

**What must hold**, from §5: each stage pins its release *before invocation*, one
CAS-accepted result per stage, and retries reuse the accepted result rather than
re-invoking. `pin_routing_decision` is idempotent **by keeping the first pin**,
never by overwriting with the latest — so a stage that was pinned and then
crashed mid-invocation keeps its release, and the retry routes by the decision it
must actually route by rather than by whatever shipped in between.

That is stronger than it first looks, and stronger than this module first
assumed: the third test below was written expecting a never-completed stage to
adopt the promoted release, and reading `pin_routing_decision` corrected it. The
expectation was wrong; the code was right. Recorded rather than quietly amended,
because "the test was adjusted until it passed" and "the test was wrong for a
reason someone can check" look identical in a diff.

A **control** is therefore load-bearing here: every assertion about pins staying
put is also satisfied by a build that can never adopt a new release at all. The
last test closes that by pinning a *second* event under a later release on the
same store.

**The gate that runs it** (RV rule 13): normal suite, no `live_infra` marker, so
`.github/workflows/checks.yml`'s backend job runs it on every push.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import pytest_asyncio

from return_platform.configuration.settings import Settings
from return_platform.configuration.support_ingress_configuration import (
    SupportIngressConfiguration,
)
from return_platform.operations.return_support.analysis_records import (
    AnalysisStatus,
    SupportAnalysisRecordStore,
)
from return_platform.operations.return_support.message_classification import (
    SupportMessageAnalyser,
)
from tests.operations.mongo_double import FakeClient
from tests.operations.test_support_message_classification import (  # noqa: PLC2701
    EVENT_ID,
    _analyse,
    _record_document,
    _RecordingFacts,
    _RecordingOmc,
    _RecordingRelay,
    _RecordingSupportEvents,
    _StubInvoker,
    _StubRecordStore,
)

_EXTRACTION = {"records": [{"returnReference": "RMA-1"}], "artifacts": []}


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest_asyncio.fixture
async def analysis(mongo: FakeClient, test_settings: Settings) -> SupportAnalysisRecordStore:
    store = SupportAnalysisRecordStore(cast(Any, mongo), test_settings)
    await store.ensure_indexes()
    return store


def _analyser_on_release(
    analysis: SupportAnalysisRecordStore,
    release: str,
    *,
    extractor_crash_on: tuple[str, ...] = (),
) -> tuple[SupportMessageAnalyser, dict[str, Any]]:
    """An analyser whose **both** stages report `release`.

    Built here rather than through the classification suite's `_analyser`
    helper, which fixes the release at `release-1` and has no parameter for it.
    Everything else is that module's own doubles, so the only thing this
    scenario varies is the release -- which is the point.
    """
    classifier = _StubInvoker({"intent": "rma_issued"}, release_id=release)
    extractor = _StubInvoker(_EXTRACTION, release_id=release, crash_on=extractor_crash_on)
    parts = {
        "classifier": classifier,
        "extractor": extractor,
        "records": _StubRecordStore([_record_document("RMA-1", "rr-1")]),
        "facts": _RecordingFacts(),
        "events": _RecordingSupportEvents(),
        "omc": _RecordingOmc(),
        "relay": _RecordingRelay(),
    }
    analyser = SupportMessageAnalyser(
        records=analysis,
        classifier=classifier,
        extractor=extractor,
        configuration=SupportIngressConfiguration(),
        record_store=cast(Any, parts["records"]),
        append_scoped_fact_once=cast(Any, parts["facts"]),
        support_events=cast(Any, parts["events"]),
        omc=cast(Any, parts["omc"]),
        relay=cast(Any, parts["relay"]),
    )
    return analyser, parts


@pytest.mark.asyncio
async def test_a_release_promoted_between_the_crash_and_the_retry_does_not_move_the_pin(
    analysis: SupportAnalysisRecordStore,
) -> None:
    """Item 22's third clause, in its operational shape.

    Sequence: classify under `release-1`, the worker dies inside extraction, a
    release is promoted, a different worker picks the command up.
    """
    crashing, first_parts = _analyser_on_release(
        analysis, "release-1", extractor_crash_on=("primary",)
    )
    with pytest.raises(RuntimeError, match="died mid-invocation"):
        await _analyse(crashing)

    record = await analysis.get(EVENT_ID)
    assert record["accepted_classification"] is not None
    assert record["accepted_extraction"] is None
    assert record["status"] == AnalysisStatus.CLASSIFIED.value
    assert record["classification_release_id"] == "release-1"
    assert first_parts["classifier"].calls == ["primary"]
    # **Extraction is already pinned, with no accepted result.** `pin_routing_
    # decision` runs *before* the invocation, so the crash lands after the pin
    # and the record carries a release for a stage that has produced nothing.
    # That is the guarantee, not a leak: it is what makes the retry below route
    # by the release that was chosen for this event rather than by whatever is
    # current when the retry happens.
    assert record["extraction_release_id"] == "release-1"
    assert record["accepted_extraction"] is None

    # --- the promotion, then a fresh worker ---------------------------------
    resumed, resumed_parts = _analyser_on_release(analysis, "release-2")
    outcome = await _analyse(resumed)

    assert outcome.reused_classification is True
    assert resumed_parts["classifier"].calls == [], (
        "the promoted release re-invoked the classifier -- a retry that re-asks is "
        "a second accepted answer nobody can order, and it is what the pin exists "
        "to prevent"
    )

    after = await analysis.get(EVENT_ID)
    assert after["classification_release_id"] == "release-1", (
        "the promotion rewrote the pin on a stage that had already been accepted. "
        "The classification in the record was produced by release-1; a record "
        "saying release-2 produced it is an audit trail that names the wrong "
        "author for an answer the case is still using."
    )
    # Extraction genuinely runs on this dispatch -- it had no accepted result --
    # and it runs under the release **pinned before the crash**, not the promoted
    # one. This assertion was written the other way round first, expecting
    # `release-2` on the reasoning that a stage which had never produced anything
    # could not yet be pinned. Reading `pin_routing_decision` corrected it: the
    # pin is taken before invocation and kept, so the crash leaves it standing.
    # The behaviour is stronger than the expectation, and the expectation was
    # the thing that was wrong -- not the code.
    assert resumed_parts["extractor"].calls == ["primary"]
    assert after["extraction_release_id"] == "release-1", (
        "the promotion moved a pin that was taken before the first invocation. "
        "The retry would then route by a release chosen after the fact, and the "
        "record would name it as the author of attempts made under the old one."
    )


@pytest.mark.asyncio
async def test_a_promotion_after_both_stages_are_accepted_moves_neither_pin(
    analysis: SupportAnalysisRecordStore,
) -> None:
    """The settled case, and the one a redelivery actually hits.

    At-least-once means the classify command can arrive again long after the
    case is done, by which time any number of releases have shipped. Both pins
    must be exactly what produced the accepted results.
    """
    first, _parts = _analyser_on_release(analysis, "release-1")
    await _analyse(first)
    settled = await analysis.get(EVENT_ID)
    assert settled["classification_release_id"] == "release-1"
    assert settled["extraction_release_id"] == "release-1"

    later, later_parts = _analyser_on_release(analysis, "release-9")
    outcome = await _analyse(later)

    assert outcome.reused_classification is True
    assert outcome.reused_extraction is True
    assert later_parts["classifier"].calls == []
    assert later_parts["extractor"].calls == []

    after = await analysis.get(EVENT_ID)
    assert after["classification_release_id"] == "release-1"
    assert after["extraction_release_id"] == "release-1"
    assert after["accepted_extraction"] == settled["accepted_extraction"]


@pytest.mark.asyncio
async def test_the_pin_is_not_simply_the_first_release_the_store_ever_saw(
    analysis: SupportAnalysisRecordStore,
) -> None:
    """The control, and it is not decoration.

    Every assertion above is satisfied by an implementation that recorded the
    first release it ever saw and ignored the rest -- including for a stage that
    genuinely runs later. So: a **second event**, classified under `release-2`
    on a store whose first event was pinned to `release-1`, must be recorded as
    `release-2`. Without this the two tests above pass for a build that cannot
    adopt a new release at all, which is `merge.md`'s "green because the inputs
    can't exercise the property".
    """
    first, _parts = _analyser_on_release(analysis, "release-1")
    await _analyse(first)

    second, _second_parts = _analyser_on_release(analysis, "release-2")
    await second.analyse(
        case_id="case-5150",
        work_item_id="wi-5150",
        support_event_id="sev-second",
        workflow_id="wf-5150",
        body_text="a second message on the same case",
    )

    other = await analysis.get("sev-second")
    assert other["classification_release_id"] == "release-2"
    assert other["extraction_release_id"] == "release-2"
    assert (await analysis.get(EVENT_ID))["classification_release_id"] == "release-1"
