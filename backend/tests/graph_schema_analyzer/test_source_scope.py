"""The grant itself: what `InspectionScope` permits, and how it refuses.

These are the checks that have to hold before any connector is involved, because
W4.5's requirement is that scope is a *hard filter in code* -- a model naming a
table it was not granted is refused whether or not the prompt said so. A scope
object that quietly widened on a missing field would make every real-infra proof
downstream meaningless, so the widening cases are tested explicitly rather than
assumed from the type.
"""

from __future__ import annotations

import pytest

from return_platform.graph_schema_analyzer.domain.errors import ScopeViolation
from return_platform.graph_schema_analyzer.domain.source_scope import (
    InspectionScope,
    ObjectScope,
    SourceScope,
)


def _scope() -> InspectionScope:
    return InspectionScope(
        sources=(
            SourceScope(
                source_id="warehouse",
                objects=(
                    ObjectScope(object_name="dbo.bay", fields=frozenset({"bay_id", "aisle"})),
                    ObjectScope(object_name="dbo.warehouse"),
                ),
                max_sample_rows=5,
            ),
        )
    )


def test_a_source_outside_the_grant_is_refused_rather_than_answered() -> None:
    """Catches the failure where an ungranted source returns an empty result: the
    caller cannot then tell "this source has nothing" from "you may not read
    it", and an audit of what was refused would find no refusal to read."""
    with pytest.raises(ScopeViolation, match="is not in scope"):
        _scope().source_scope("finance")


def test_an_object_outside_the_grant_is_refused_by_name() -> None:
    """The headline requirement: a caller that can name a table it was not
    granted is still refused by code."""
    with pytest.raises(ScopeViolation, match=r"dbo\.salary"):
        _scope().source_scope("warehouse").object_scope("dbo.salary")


def test_a_refusal_names_what_was_granted_so_the_scope_can_be_corrected() -> None:
    """An operator who sees only "denied" has to guess at the grant; the message
    carrying the granted set is what makes a misconfiguration a two-minute fix."""
    with pytest.raises(ScopeViolation, match=r"dbo\.bay"):
        _scope().source_scope("warehouse").object_scope("dbo.salary")


def test_an_ungranted_field_is_refused_and_every_offender_is_reported() -> None:
    """Reporting only the first offending field makes correcting a scope an
    iterative game of whack-a-mole against a database round trip each time."""
    with pytest.raises(ScopeViolation) as caught:
        _scope().require_fields(
            source_id="warehouse",
            object_name="dbo.bay",
            requested=("bay_id", "hourly_rate", "manager_ssn"),
        )
    message = str(caught.value)
    assert "hourly_rate" in message
    assert "manager_ssn" in message


def test_an_object_granted_without_a_field_list_permits_every_field() -> None:
    """`None` is "not narrowed at this level". Without this the two-level grant
    would require every object to enumerate its columns, and a scope nobody can
    write is a scope that gets bypassed."""
    assert _scope().permitted_fields(source_id="warehouse", object_name="dbo.warehouse") is None


def test_an_empty_object_grant_permits_nothing_rather_than_everything() -> None:
    """The failure this exists to catch: a scope built from a half-populated
    configuration must fail closed. `objects=()` is "nothing is granted here",
    not "no restriction" -- over-refusing is an operator complaint, over-granting
    is a data leak nobody reports."""
    scope = InspectionScope(
        sources=(SourceScope(source_id="warehouse", objects=(), max_sample_rows=5),)
    )
    assert scope.source_scope("warehouse").permits_object("dbo.bay") is False
    with pytest.raises(ScopeViolation):
        scope.source_scope("warehouse").object_scope("dbo.bay")


def test_an_empty_field_grant_permits_no_field_rather_than_all_of_them() -> None:
    """Same fail-closed rule one level down, where it is easier to get wrong
    because an empty projection looks like "no projection"."""
    scope = InspectionScope(
        sources=(
            SourceScope(
                source_id="warehouse",
                objects=(ObjectScope(object_name="dbo.bay", fields=frozenset()),),
                max_sample_rows=5,
            ),
        )
    )
    with pytest.raises(ScopeViolation):
        scope.require_fields(source_id="warehouse", object_name="dbo.bay", requested=("bay_id",))


def test_a_sample_larger_than_the_grant_is_clamped_rather_than_refused() -> None:
    """A caller asking for more rows than granted is asking a reasonable question
    badly; answering with the granted number is more useful than an error, and
    the bound still holds."""
    assert _scope().sample_limit(source_id="warehouse", requested=5_000) == 5


def test_a_source_granted_no_rows_refuses_sampling_entirely() -> None:
    """Zero rows is a different statement from "a small number of rows": it says
    values must not be read at all. Clamping it to 1 would turn a deliberate
    metadata-only grant into a one-row leak."""
    scope = InspectionScope(sources=(SourceScope(source_id="warehouse"),))
    with pytest.raises(ScopeViolation, match="grants no sample rows"):
        scope.sample_limit(source_id="warehouse", requested=1)


def test_a_sample_bound_above_the_analyzer_ceiling_is_refused_at_construction() -> None:
    """Catches a configuration asking for a bulk export through the sampling
    door; refused when the scope is built, not silently clamped at call time."""
    with pytest.raises(ValueError, match="less than or equal to 100"):
        SourceScope(source_id="warehouse", max_sample_rows=1_000_000)


def test_the_broadest_grant_is_a_named_constructor_rather_than_an_inline_default() -> None:
    """`over_sources` is greppable. The failure it prevents is a whole-source
    grant appearing inline in a call site where a reviewer reads past it."""
    scope = InspectionScope.over_sources(("warehouse", "orders"), max_sample_rows=10)
    assert scope.source_ids() == ("warehouse", "orders")
    assert scope.permitted_fields(source_id="orders", object_name="anything") is None
    assert scope.sample_limit(source_id="orders", requested=99) == 10
