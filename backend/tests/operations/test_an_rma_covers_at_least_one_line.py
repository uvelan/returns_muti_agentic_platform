"""An RMA that covers no order lines is a return that covers nothing.

The frozen return-truth decision says a return cannot present as issued without
durable items. That was declared and enforced in no layer at all:

  - the console's submit gate asked only for a non-empty `returnReference`;
  - `ReturnOutcomeRecord.orderLineReferences` defaulted to `()`;
  - the activity builds a record's items by iterating that list, so an empty one
    produced a record covering nothing;
  - SQL cannot express "at least one child row" declaratively, so the store
    could not catch it either.

The consequence is measurable in this deployment: five documents in Mongo
`return_records` read `status: "ISSUED"` with an empty `approvedItems`, and this
is the path that made them. `T19b` repairs those; without this, it would be
treating a symptom that recurs.

**Enforced at the edge, not at the seam.** `IssuanceRecord` is a data carrier
that a dozen tests construct while asserting something else -- whether a return
method reaches the SQL column, whether a whitespace carrier renders as nothing --
and making all of them carry items they do not care about would put a
client-input rule two layers below the client. The seam keeps only the rule that
is about the data itself: one line comes back on one RMA.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.api.return_support import ReturnOutcomeRecord
from return_platform.operations.return_issuance import IssuanceItem, IssuanceRecord


class TestTheWireContract:
    """Where the rule lives, because this is where the client is."""

    def test_a_record_naming_no_lines_is_refused(self) -> None:
        with pytest.raises(ValidationError) as refusal:
            ReturnOutcomeRecord(returnReference="RMA-1", orderLineReferences=())

        assert "orderLineReferences" in str(refusal.value)

    def test_the_field_cannot_simply_be_omitted(self) -> None:
        """It defaulted to `()`, so omitting it was the quiet way in."""
        with pytest.raises(ValidationError):
            ReturnOutcomeRecord(returnReference="RMA-1")

    def test_one_line_is_enough(self) -> None:
        record = ReturnOutcomeRecord(returnReference="RMA-1", orderLineReferences=("LINE-1",))
        assert record.orderLineReferences == ("LINE-1",)

    def test_the_upper_bound_still_holds(self) -> None:
        """Requiring a minimum must not have removed the maximum."""
        with pytest.raises(ValidationError):
            ReturnOutcomeRecord(
                returnReference="RMA-1",
                orderLineReferences=tuple(f"LINE-{index}" for index in range(201)),
            )


class TestTheSeam:
    """One line comes back on one RMA -- the rule that is about the data."""

    def test_a_repeated_line_is_refused(self) -> None:
        with pytest.raises(ValueError, match="repeats an order line"):
            IssuanceRecord(
                return_record_id="rec-1",
                return_reference="RMA-1",
                items=(IssuanceItem(order_line_id="L1"), IssuanceItem(order_line_id="L1")),
            )

    def test_distinct_lines_are_accepted(self) -> None:
        record = IssuanceRecord(
            return_record_id="rec-1",
            return_reference="RMA-1",
            items=(IssuanceItem(order_line_id="L1"), IssuanceItem(order_line_id="L2")),
        )
        assert len(record.items) == 2

    def test_the_seam_does_not_enforce_the_client_rule(self) -> None:
        """Deliberate, and worth stating so it is not "fixed" later.

        A dozen tests in this package build an `IssuanceRecord` while asserting
        something entirely different. Requiring items here would make every one
        of them carry a line it does not care about, to enforce a rule the wire
        contract already refuses -- defence in depth bought at the cost of every
        fixture around it.
        """
        record = IssuanceRecord(return_record_id="rec-1", return_reference="RMA-1")
        assert record.items == ()
