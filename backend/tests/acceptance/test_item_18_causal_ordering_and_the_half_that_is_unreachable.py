"""Acceptance item 18 — the causal ordering that exists, and the half that does not.

Item 18 reads, in the brief's words: *downtime backlog drained in order per case;
**causal ordering (outbound waits for its inbound's classification, unrelated
approval does not)***.

**The first half is implemented and covered, and this module does not duplicate
it.** `tests/operations/test_support_ingress_store.py` builds the chain with the
**real** `DurableSupportIngressStore` and drains it with the **real**
`IntegrationOutboxDispatcher`, with the queue deliberately loaded *against* the
answer (newest command made oldest-due). Dispatch condition 3 — "assert the
chain, not just the drain" — is met there by a separate assertion on
`causationId` and `requiredPredecessorIds`. ACC audited both by injection rather
than by reading; the evidence is in `.plan/acceptance/item-18-causal-ordering.md`,
and the separability the condition is about was measured: **dropping
`causation_id` while keeping the predecessors reds the chain test and leaves the
drain green.**

**The second half is not implemented, and this module makes that checkable**
rather than leaving it a silence — the same posture AMENDMENT-8 ruled for item
10, and for the same reason: a frozen acceptance item that nothing can reach
should become a verified fact, not a green tick.

Contracts §7 declares four per-case streams — `inbound`, `outbound`,
`review_commands`, `omc` — and adds one sentence: *"Acceptance 18 applies to the
inbound stream."* Measured against `src/`, that sentence is doing more work than
it looks:

* **only two of the four streams have a producer at all.** `inbound` (the
  ingress store) and `review_commands` (the case-command store). Nothing in
  `src/` ever names `CaseStream.OUTBOUND` or `CaseStream.OMC`;
* **no caller anywhere populates a cross-stream predecessor.** The machinery
  takes one — `plan_command` has the keyword and `ordered_command_fields`
  validates a predecessor's *existence on the case* rather than its stream — and
  **no production call site passes it.**

So "outbound waits for its inbound's classification" has no outbound event to
wait, and "unrelated approval does not" has nothing to be unrelated to. This is
RV rule 13's exact shape in the ordering plane: *the correct mechanism exists and
is bypassed* — and the module asserts precisely that, so the day someone wires it
the assertion fails and the half returns to scope.

**The gate that runs this** (rule 13, applied to ACC's own guard): the module has
no `_real_infra` suffix and no `live_infra` marker, so it is in the default
backend suite, which `.github/workflows/checks.yml` runs on every push. It is not
one of the 512 deselected tests — CI never runs those.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from return_platform.operations.integrations.outbox import (
    CaseStream,
    ordered_command_fields,
)

#: `parents[2]` is `backend/`. Written as `parents[3]` first, which pointed at
#: the repository root and made every scan below walk a directory that does not
#: exist -- `Path.rglob` on a missing directory yields nothing and raises
#: nothing, so both scans returned "no streams named, no call sites" and an
#: assertion phrased as `"OUTBOUND" not in named` would have passed vacuously.
#: The exact-set assertions caught it on the first run; `_scanned_files` below
#: is what stops it recurring silently.
_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "return_platform"

#: Where `CaseStream` is declared. Its own module names every member by
#: definition, so it is the one file a "who names this stream" scan must skip --
#: and skipping it by path rather than by heuristic is what keeps the scan from
#: quietly excusing a second file later.
_ENUM_MODULE = _SOURCE_ROOT / "operations" / "integrations" / "outbox.py"

#: The two streams this deployment actually produces on.
_PRODUCED = frozenset({CaseStream.INBOUND, CaseStream.REVIEW_COMMANDS})


def _scanned_files() -> list[Path]:
    """Every module the scans below walk, and never an empty list.

    A scan of nothing reports no violations, which is indistinguishable from a
    clean scan and is how an absence assertion goes green for the wrong reason.
    """
    files = sorted(_SOURCE_ROOT.rglob("*.py"))
    assert len(files) > 100, (
        f"the source scan found {len(files)} modules under {_SOURCE_ROOT} -- that is "
        "not this codebase, so every assertion below would be reporting the absence "
        "of things it never looked for"
    )
    return files


def _stream_members_named_in_source() -> dict[str, set[str]]:
    """`{member name: {files that name it}}`, for `CaseStream.X` in `src/`.

    An AST walk rather than a grep: `CaseStream.OUTBOUND` inside a docstring or
    a comment is prose, and a scan that counted it would report a producer that
    does not exist -- which is the failure mode of asserting an absence by text
    search. `ast.Attribute` over `ast.Name` is the access, and nothing else is.
    """
    found: dict[str, set[str]] = {}
    for path in _scanned_files():
        if path == _ENUM_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "CaseStream"
            ):
                found.setdefault(node.attr, set()).add(path.relative_to(_SOURCE_ROOT).as_posix())
    return found


def _call_sites_passing_predecessors() -> dict[str, int]:
    """`{module: how many calls pass a non-empty `required_predecessor_ids=`}`.

    Counted per file rather than pinned by line number: a line pin fails on any
    edit above it, which trains whoever hits it to update the number, and a
    number people update on sight is not a guard. A count per file still fails
    when a *second* population appears in a file that already had one, which is
    the case a set-of-files assertion would miss.

    Definitions forwarding their own parameter are excluded -- a bare `Name`
    matching an enclosing function's argument is a pass-through, not a source.
    An explicit empty tuple populates nothing. Everything else counts, including
    the outbox reader's `tuple(...)` over a stored document: that one is
    deserialisation rather than population, and it is left **in** the count
    deliberately. Teaching this scan to recognise "deserialisation" is teaching
    it to excuse a shape, and the next thing wearing that shape would be
    excused too. Two is the number; both are named in the test.
    """
    sites: dict[str, int] = {}
    for path in _scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parameters: dict[ast.AST, set[str]] = {}
        for function in ast.walk(tree):
            if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                names = {
                    argument.arg for argument in [*function.args.args, *function.args.kwonlyargs]
                }
                for inner in ast.walk(function):
                    parameters[inner] = names
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "required_predecessor_ids":
                    continue
                value = keyword.value
                if isinstance(value, ast.Name) and value.id in parameters.get(node, set()):
                    continue
                if isinstance(value, ast.Tuple) and not value.elts:
                    continue
                module = path.relative_to(_SOURCE_ROOT).as_posix()
                sites[module] = sites.get(module, 0) + 1
    return sites


class TestTheOrderingPlaneThisDeploymentActuallyHas:
    def test_only_two_of_the_four_streams_have_a_producer(self) -> None:
        """§7 declares four; `src/` names two.

        Asserted as an exact set rather than as "outbound is absent": a new
        member appearing, or `omc` gaining a producer, both change what item 18
        means and neither is visible to a `not in` check.
        """
        named = _stream_members_named_in_source()
        assert set(named) == {stream.name for stream in _PRODUCED}, (
            "the set of streams named in src/ has changed: "
            f"{ {member: sorted(files) for member, files in named.items()} }. "
            "If `OUTBOUND` now has a producer, acceptance item 18's second half "
            "-- outbound waits for its inbound's classification -- is reachable "
            "and must be exercised rather than recorded as absent."
        )

    def test_only_one_place_in_the_codebase_populates_a_predecessor(self) -> None:
        """The population half, and the one rule 13 is about.

        Two call sites reach `required_predecessor_ids` with a value, and
        neither expresses a cross-stream dependency:

        * `operations/integrations/outbox.py` -- the **reader**, rebuilding an
          `OutboxCommand` from `requiredPredecessorIds` on a stored document. It
          carries whatever was written; it decides nothing.
        * `operations/return_support/ingress_store.py` -- the **only producer**,
          and it supplies the previous event on **its own** stream. That the
          predecessor is same-stream is not asserted here by reading: it is
          `tests/operations/test_support_ingress_store.py`'s
          `test_every_enqueued_event_carries_its_causation`, which ACC audited
          by injection (dropping `causation_id` while keeping the predecessors
          reds that test and leaves the drain green).

        Nothing in this deployment expresses "this outbound waits for that
        inbound". The machinery would take it -- see the next test -- and no
        caller offers it.
        """
        sites = _call_sites_passing_predecessors()
        assert sites == {
            "operations/integrations/outbox.py": 1,
            "operations/return_support/ingress_store.py": 1,
        }, (
            f"the places populating predecessors have changed: {sites}. A new one, or "
            "a second one in a file that already had one, may express a cross-stream "
            "dependency -- which is acceptance item 18's second half becoming "
            "reachable, and it must then be exercised rather than recorded as absent."
        )

    @pytest.mark.asyncio
    async def test_the_machinery_would_accept_a_cross_stream_predecessor(
        self, database: object
    ) -> None:
        """So the gap is a population gap, not a machinery limit.

        This is the half that makes the two assertions above a *finding* rather
        than a description. `ordered_command_fields` validates that a
        predecessor exists **on the case**; it does not require the same stream.
        Demonstrated by enqueuing an inbound event and then successfully
        allocating a `review_commands` event that names it -- the exact shape
        "outbound waits for its inbound's classification" would need.

        If this ever raised, the correct report would be the opposite one: the
        machinery forbids cross-stream ordering and §7's four streams cannot be
        chained at all. It does not raise, so the mechanism is there and unused.
        """
        from return_platform.operations.integrations.outbox import (
            INTEGRATION_OUTBOX_COLLECTION,
        )

        collection = database[INTEGRATION_OUTBOX_COLLECTION]  # type: ignore[index]
        await collection.insert_one(
            {
                "_id": "cmd-inbound-1",
                "aggregateId": "case-18",
                "eventId": "evt-inbound-1",
                "stream": CaseStream.INBOUND.value,
                "streamSequence": 1,
                "requiredPredecessorIds": [],
            }
        )
        fields = await ordered_command_fields(
            database,  # type: ignore[arg-type]
            case_id="case-18",
            stream=CaseStream.REVIEW_COMMANDS,
            event_id="evt-review-1",
            causation_id="evt-inbound-1",
            required_predecessor_ids=("evt-inbound-1",),
        )
        assert fields["stream"] == CaseStream.REVIEW_COMMANDS.value
        assert fields["requiredPredecessorIds"] == ["evt-inbound-1"]
        assert fields["causationId"] == "evt-inbound-1"
