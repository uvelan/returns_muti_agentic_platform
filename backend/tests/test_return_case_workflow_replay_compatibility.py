"""Replay compatibility for `ReturnCaseWorkflow`, and the rule that keeps it.

Commit `eaed61c` changed `draft_support_request` from returning the handoff prose
as a bare `str` to returning `SupportRequestDraft`. The workflow asks for an
activity result *by type*, so every history recorded before that commit -- which
holds a JSON string -- failed to decode on replay:

    TypeError: Cannot convert to dataclass ...SupportRequestDraft,
    value is <class 'str'> not dict

The failure is not recoverable by retrying, because replay is deterministic: the
same history decodes the same way every time. The observed effect was a worker
raising `Failed activation on workflow return-platform-return-case-v1` five times
in forty-five minutes, a case parked in `AWAITING_SUPPORT` that no downstream
agent could reach, and no alert, metric or terminal state anywhere.

Five things are asserted here:

1. the decode failure is real, and reproducible from the data converter alone;
2. the workflow guards the change with `workflow.patched`;
3. both sides of that guard produce one `SupportRequestDraft`, so nothing
   downstream has to know which history it is running on;
4. the activity result-type contract is pinned, so the *next* change of this
   shape fails a test instead of a production replay;
5. a runtime answering as a pre-`eaed61c` history drives the real
   `_open_support` to completion -- Support is opened rather than the task
   failing.

Point 4 is the one that matters beyond this defect. The repository had zero uses
of `workflow.patched`, `get_version` or `deprecate_patch` when this was written,
so nothing would have caught it -- and nothing would catch the next one either.

**What is still not proven here.** Point 5 drives the workflow's own branch, but
through a stand-in rather than a genuine recorded history replayed by
`temporalio.worker.Replayer`. No such fixture exists in this repository, and
capturing one requires the live Temporal service. So this file proves the branch
is correct and reachable; it does not prove that case `721fb62e` specifically
advances. That is a runtime observation, recorded as a residual in the ledger
rather than claimed here.
"""

from __future__ import annotations

import ast
import inspect
import logging
import pathlib
import uuid
from datetime import UTC, datetime

import pytest
from temporalio.converter import default as default_converter

from return_platform.workflows import return_case_workflow as workflow_module
from return_platform.workflows.return_case_workflow import (  # noqa: E501
    _PATCH_STRUCTURED_SUPPORT_DRAFT,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
    ReturnCaseWorkflowInput,
    SupportRequestDraft,
    _coerce_support_draft,
)

WORKFLOW_SOURCE = pathlib.Path(inspect.getfile(ReturnCaseWorkflow))

#: The handoff prose a pre-`eaed61c` activity returned. Shortened, but the shape
#: is what matters: a bare JSON string where a dataclass is now expected.
LEGACY_DRAFT_TEXT = (
    "Hello -- we have a return to raise against CQ800002. Could you create the "
    "RMA and send the return label or pickup instructions when you have a moment?"
)

#: Every activity result type the workflow asks for, pinned.
#:
#: This is a lockfile, not a description. Changing an activity's result type is
#: a workflow-visible contract change: in-flight executions decode their history
#: with the *new* type and wedge if the shapes disagree. Editing this dict is the
#: moment to decide whether the change needs a `workflow.patched` guard.
#:
#: `draft_support_request` appears twice on purpose -- that is the guard working.
ACTIVITY_RESULT_TYPES: dict[str, set[str]] = {
    "record_case_customer_identity": {"bool"},
    "request_bay_assignment": {"BayResultNotice"},
    "evaluate_case_eligibility": {"CaseEligibilityOutcome"},
    # `SupportRequestDraft` only. The un-patched branch no longer pins a result
    # type at all: it decodes whatever the history holds and coerces it through
    # `_coerce_support_draft`.
    #
    # It used to pin `str`, on the reasoning that an unmarked history predates
    # `eaed61c` and therefore holds prose. Two live histories disproved that --
    # they ran after `eaed61c`, before the patch marker existed, and recorded a
    # dict. Asking for `str` failed them with "Expected value to be str, was
    # <class 'dict'>", which is UIAUDIT-005 on the population the first fix
    # missed. Pinning a type on that branch is what made the assumption
    # invisible, so it is deliberately unpinned now.
    "draft_support_request": {"SupportRequestDraft"},
    "open_support_work_item": {"str"},
    "resolve_business_deadline": {"ResolvedBusinessDeadline"},
    "record_support_outcome": {"_RECEIPT_RESULT_TYPE"},
    "synchronize_return_records": {"str"},
}


def _activity_calls() -> list[tuple[str | None, str | None, int]]:
    """Every `execute_activity` call in the workflow: (name, result_type, line)."""
    tree = ast.parse(WORKFLOW_SOURCE.read_text(encoding="utf-8"))
    calls: list[tuple[str | None, str | None, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in {"execute_activity", "execute_local_activity"}
        ):
            continue
        name = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        result_type = next(
            (ast.unparse(kw.value) for kw in node.keywords if kw.arg == "result_type"),
            None,
        )
        calls.append((name, result_type, node.lineno))
    return calls


# ---------------------------------------------------------------------------
# 1. The wedge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_legacy_history_payload_decodes_as_the_string_it_holds() -> None:
    converter = default_converter()
    payloads = await converter.encode([LEGACY_DRAFT_TEXT])

    decoded = await converter.decode(payloads, [str])

    assert decoded == [LEGACY_DRAFT_TEXT]


@pytest.mark.asyncio
async def test_decoding_that_payload_as_the_new_dataclass_is_what_wedged() -> None:
    """The exact failure from `logs/worker-temporal.log`, reproduced.

    Asserted so the guard above cannot be removed on the belief that the two
    shapes are interchangeable. They are not, and this is the proof.
    """
    converter = default_converter()
    payloads = await converter.encode([LEGACY_DRAFT_TEXT])

    with pytest.raises(TypeError) as raised:
        await converter.decode(payloads, [SupportRequestDraft])

    message = str(raised.value)
    assert "SupportRequestDraft" in message
    assert "not dict" in message


# ---------------------------------------------------------------------------
# 2. The guard
# ---------------------------------------------------------------------------


def test_the_draft_result_type_change_is_behind_a_patch() -> None:
    source = WORKFLOW_SOURCE.read_text(encoding="utf-8")
    assert f"workflow.patched({_PATCH_STRUCTURED_SUPPORT_DRAFT!s}" not in source, (
        "the patch id must be referenced by its constant, not inlined -- an "
        "inlined string can drift from the constant this test imports"
    )
    assert "workflow.patched(_PATCH_STRUCTURED_SUPPORT_DRAFT)" in source


def test_both_draft_branches_are_inside_the_patch_decision() -> None:
    """The typed and legacy calls must be the two arms of one `if`.

    A guard that only wraps the new path leaves the old one unreachable, which
    is the same wedge with extra steps.
    """
    tree = ast.parse(WORKFLOW_SOURCE.read_text(encoding="utf-8"))

    guarded: set[str | None] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Attribute)
            and test.func.attr == "patched"
        ):
            continue
        for branch in (node.body, node.orelse):
            for sub in branch:
                for call in ast.walk(sub):
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "execute_activity"
                        and call.args
                        and isinstance(call.args[0], ast.Constant)
                    ):
                        guarded.add(call.args[0].value)

    assert "draft_support_request" in guarded, (
        "both draft calls must sit inside the `workflow.patched` if/else"
    )


def test_the_patch_id_is_stable() -> None:
    """The id is written into every new history; changing it re-wedges them.

    Pinned to the literal rather than to the constant so that renaming the
    constant cannot silently change the recorded marker.
    """
    assert _PATCH_STRUCTURED_SUPPORT_DRAFT == "support-draft-returns-structured-payload"


# ---------------------------------------------------------------------------
# 3. Convergence
# ---------------------------------------------------------------------------


def test_a_legacy_draft_becomes_a_draft_with_no_invented_payload() -> None:
    """The legacy arm wraps prose; it does not fabricate the structured half.

    A pre-`eaed61c` activity composed no payload, so there is nothing to
    recover. Filling one in would put facts on a Support message that nothing
    ever observed.
    """
    draft = SupportRequestDraft(text=LEGACY_DRAFT_TEXT)

    assert draft.text == LEGACY_DRAFT_TEXT
    assert draft.payload == {}
    assert draft.subject == ""


# ---------------------------------------------------------------------------
# 4. The rule
# ---------------------------------------------------------------------------


def test_activity_result_types_match_the_pinned_contract() -> None:
    """Changing an activity's result type must be a deliberate, reviewed act.

    If this fails, an activity's workflow-visible result shape changed. Decide
    whether in-flight histories can decode the new shape. If they cannot, guard
    the change with `workflow.patched` -- see `_PATCH_STRUCTURED_SUPPORT_DRAFT`
    -- and only then update the dict above.
    """
    observed: dict[str, set[str]] = {}
    for name, result_type, _line in _activity_calls():
        if name is None or result_type is None or result_type == "None":
            continue
        observed.setdefault(name, set()).add(result_type)

    assert observed == ACTIVITY_RESULT_TYPES


def test_every_activity_call_names_its_activity_literally() -> None:
    """A computed activity name cannot be pinned, so it must not appear.

    The contract lock above is only as good as its ability to see every call.
    """
    unnamed = [line for name, _rt, line in _activity_calls() if name is None]

    assert unnamed == [], f"execute_activity with a non-literal name at lines {unnamed}"


# ---------------------------------------------------------------------------
# 5. The wedge, driven through the real `_open_support`
# ---------------------------------------------------------------------------


class _LegacyRuntime:
    """A `temporalio.workflow` stand-in answering as a pre-`eaed61c` history.

    `patched` returns False, which is what a history recorded before the marker
    existed answers, and `draft_support_request` hands back the bare string such
    a history holds. Together those are the exact conditions that wedged case
    `721fb62e`.

    Deliberately not the converter: this drives the workflow's own branch, so it
    proves the legacy arm is reachable and terminates -- not merely that a
    payload can be decoded two ways.
    """

    def __init__(self, legacy_text: str) -> None:
        self._legacy_text = legacy_text
        self._uuid = 0
        self.calls: list[str] = []
        self.opened_with: dict[str, object] = {}
        self.logger = logging.getLogger("tests.replay.legacy")

    def patched(self, patch_id: str) -> bool:
        assert patch_id == _PATCH_STRUCTURED_SUPPORT_DRAFT
        return False

    def uuid4(self) -> uuid.UUID:
        self._uuid += 1
        return uuid.UUID(int=self._uuid)

    def now(self) -> datetime:
        return datetime(2026, 8, 22, 10, 0, tzinfo=UTC)

    async def execute_activity(self, name: str, argument: object, **_options: object) -> object:
        self.calls.append(name)
        if name == "draft_support_request":
            # A pre-`eaed61c` activity returned prose, not a dataclass.
            return self._legacy_text
        if name == "open_support_work_item":
            self.opened_with = {
                "support_draft": getattr(argument, "support_draft", None),
                "business_payload": getattr(argument, "business_payload", None),
                "subject": getattr(argument, "subject", None),
            }
            return "work-item-1"
        if name == "record_case_status":
            return None
        raise AssertionError(f"unexpected activity {name}")


@pytest.mark.asyncio
async def test_a_legacy_history_opens_support_instead_of_wedging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closure criterion, as close as a test without a recorded history gets.

    Before the patch this raised `TypeError` inside the activity result decode
    and the workflow task failed -- forever, because replay is deterministic.
    Now the legacy arm carries the prose through and Support is opened.
    """
    timings = ReturnCaseTimings(
        bay_wait_seconds=1,
        support_response_wait_seconds=1,
        reminder_interval_seconds=1,
        max_reminders=0,
        on_reminders_exhausted="ESCALATE",
        business_calendar_id="default",
        timezone="UTC",
    )
    runtime = _LegacyRuntime(LEGACY_DRAFT_TEXT)
    monkeypatch.setattr(workflow_module, "workflow", runtime)

    instance = ReturnCaseWorkflow()
    instance._input = ReturnCaseWorkflowInput(
        case_id="721fb62e-ac3d-4361-9265-a21ceeffee62",
        tenant_id="tenant",
        principal_id="principal",
        conversation_id="conversation",
        configuration_release_id="release",
        timings=timings,
    )

    await instance._open_support(timings)

    assert runtime.calls == [
        "draft_support_request",
        "open_support_work_item",
        "record_case_status",
    ]
    # The prose survives; the structured half is empty rather than invented.
    assert runtime.opened_with["support_draft"] == LEGACY_DRAFT_TEXT
    assert runtime.opened_with["business_payload"] == {}
    assert runtime.opened_with["subject"] == ""


# ---------------------------------------------------------------------------
# 5. The shapes a real history actually holds
# ---------------------------------------------------------------------------


class TestTheDraftDecodeAcceptsEveryRecordedShape:
    """Three shapes have been on the wire, and retention can hold all three.

    The first fix assumed two: marked histories hold a typed payload, unmarked
    ones hold prose. The third -- unmarked *and* typed -- is what an execution
    records between the activity changing and the patch marker existing, and it
    is the one that wedged. Found on two live histories,
    `return-case-7b216e58` and `return-case-2328a586`, which replayed with
    "Expected value to be str, was <class 'dict'>" and now replay clean.
    """

    def test_a_typed_payload_passes_through(self) -> None:
        draft = SupportRequestDraft(text="Please authorise this return.")

        assert _coerce_support_draft(draft) is draft

    def test_prose_becomes_the_text_and_invents_nothing(self) -> None:
        """A `str` history has no structured payload to recover.

        The activity did not compose one, so filling the other fields here would
        put facts on the message that nothing observed.
        """
        draft = _coerce_support_draft("Please authorise this return.")

        assert draft.text == "Please authorise this return."
        assert draft == SupportRequestDraft(text="Please authorise this return.")

    def test_a_dict_is_decoded_rather_than_refused(self) -> None:
        """The case the first fix missed."""
        draft = _coerce_support_draft({"text": "Please authorise this return."})

        assert draft.text == "Please authorise this return."

    def test_an_empty_dict_is_an_empty_draft(self) -> None:
        assert _coerce_support_draft({}) == SupportRequestDraft()

    def test_a_shape_nobody_has_written_raises(self) -> None:
        """Silently accepting an unknown shape is how the next one hides."""
        with pytest.raises(TypeError, match=r"not a shape"):
            _coerce_support_draft(42)
