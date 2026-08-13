"""W4.6 at the boundary: what a caller can obtain, not what a caller should do.

The masking algorithm is covered in `tests/platform/test_sample_masking.py`.
What is tested here is placement -- that the object an analysis is handed cannot
return an unmasked row, that scope still holds through the composition, and that
the methods carrying statistics and structure rather than values are left alone.
A mask in the right module and the wrong place is indistinguishable from no mask.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from return_platform.graph_schema_analyzer.application.prompt_context import build_prompt_blocks
from return_platform.graph_schema_analyzer.application.sample_masking import (
    build_masked_source_inspection,
)
from return_platform.graph_schema_analyzer.application.source_inspection import (
    build_scoped_source_inspection,
)
from return_platform.graph_schema_analyzer.domain.errors import ScopeViolation
from return_platform.graph_schema_analyzer.domain.source_scope import (
    InspectionScope,
    ObjectScope,
    SourceScope,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    FieldProfile,
    IndexDescription,
    ObjectProfile,
    SourceInspectionPort,
)
from return_platform.platform.redaction.sample_masking import MASK_PREFIX, SampleMasker

ROWS: tuple[Mapping[str, Any], ...] = (
    {"customer_id": "C-1", "customer_name": "Jane Doe", "total": 41.5},
    {"customer_id": "C-2", "customer_name": "John Roe", "total": 12.0},
    {"customer_id": "C-1", "customer_name": "Jane Doe", "total": 8.25},
)


class StubInspection:
    """The unmasked side of the boundary. Records what it was asked so the scope
    assertions can tell a refusal from a silently narrowed read."""

    def __init__(self) -> None:
        self.sampled_fields: Sequence[str] | None = None

    async def validate(self, *, source_id: str) -> Any:
        raise NotImplementedError

    async def list_sources(self) -> Sequence[str]:
        return ("warehouse",)

    async def list_objects(self, *, source_id: str) -> Sequence[Any]:
        return ()

    async def describe_object(self, *, source_id: str, object_name: str) -> Any:
        raise NotImplementedError

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        self.sampled_fields = fields
        return [dict(row) for row in ROWS[:limit]]

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> ObjectProfile:
        return ObjectProfile(
            source_id=source_id,
            object_name=object_name,
            approximate_row_count=1000,
            sampled_rows=3,
            fields=(
                FieldProfile(
                    field_name="customer_name",
                    declared_type="string",
                    null_rate=0.25,
                    approximate_distinct=2,
                    identifier_candidate=False,
                ),
            ),
        )

    async def list_indexes(self, *, source_id: str, object_name: str) -> Sequence[IndexDescription]:
        return (IndexDescription(index_name="ix_name", fields=("customer_name",), unique=True),)

    async def list_relationships(
        self, *, source_id: str, object_name: str | None = None
    ) -> Sequence[Any]:
        return ()


def _scope(fields: frozenset[str] | None = None) -> InspectionScope:
    return InspectionScope(
        sources=(
            SourceScope(
                source_id="warehouse",
                objects=(
                    ObjectScope(object_name="dbo.orders", fields=fields)
                    if fields is not None
                    else ObjectScope(object_name="dbo.orders"),
                ),
                max_sample_rows=5,
            ),
        )
    )


async def _sample(port: SourceInspectionPort, limit: int = 3) -> Sequence[Mapping[str, Any]]:
    return await port.sample(source_id="warehouse", object_name="dbo.orders", limit=limit)


@pytest.mark.asyncio
async def test_the_tool_layer_masks_without_being_asked_to() -> None:
    """The headline requirement, and the reason masking is a default rather than
    a parameter: `build_scoped_source_inspection` is what an analysis is handed,
    and an opt-in mask is a mask the first caller written in a hurry omits."""
    port = build_scoped_source_inspection(StubInspection(), scope=_scope())
    rows = await _sample(port)
    assert all(row["customer_name"].startswith(MASK_PREFIX) for row in rows)
    assert not any("Jane Doe" in repr(row) for row in rows)


@pytest.mark.asyncio
async def test_masking_survives_the_composition_with_scope() -> None:
    """Both wrappers, both still doing their job. A composition that dropped
    either would still return rows, which is why this asserts on both at once.
    """
    port = build_scoped_source_inspection(
        StubInspection(), scope=_scope(frozenset({"customer_id", "customer_name"}))
    )
    rows = await _sample(port)
    assert [sorted(row) for row in rows] == [["customer_id", "customer_name"]] * 3
    assert all(row["customer_name"].startswith(MASK_PREFIX) for row in rows)


@pytest.mark.asyncio
async def test_an_ungranted_object_is_still_refused_rather_than_masked() -> None:
    """Masking is not a substitute for scope. A row that should never have been
    read is a scope failure even if every value in it came back masked."""
    port = build_scoped_source_inspection(StubInspection(), scope=_scope())
    with pytest.raises(ScopeViolation):
        await port.sample(source_id="warehouse", object_name="dbo.salaries", limit=3)


@pytest.mark.asyncio
async def test_cardinality_survives_the_whole_boundary() -> None:
    """The end-to-end version of the property: two customers over three rows are
    still two customers after passing through scope and mask together."""
    port = build_scoped_source_inspection(StubInspection(), scope=_scope())
    rows = await _sample(port)
    assert len({row["customer_name"] for row in rows}) == 2
    assert rows[0]["customer_name"] == rows[2]["customer_name"]


@pytest.mark.asyncio
async def test_profile_comes_back_untouched() -> None:
    """W4.8 ranks on these numbers. `profile` carries no value to mask -- the
    rows it was computed from never left the profiling module -- so masking it
    would corrupt the statistics while protecting nothing."""
    port = build_scoped_source_inspection(StubInspection(), scope=_scope())
    profile = await port.profile(source_id="warehouse", object_name="dbo.orders", sample_size=3)
    assert profile.sampled_rows == 3
    assert profile.approximate_row_count == 1000
    assert profile.fields[0].field_name == "customer_name"
    assert profile.fields[0].null_rate == 0.25
    assert profile.fields[0].approximate_distinct == 2


@pytest.mark.asyncio
async def test_structure_bearing_methods_are_not_masked() -> None:
    """An index over `customer_name` is a statement about an access path, not a
    disclosure of anyone's name. Masking it would leave the analyzer unable to
    tell what the source is cheap to look up by."""
    port = build_scoped_source_inspection(StubInspection(), scope=_scope())
    indexes = await port.list_indexes(source_id="warehouse", object_name="dbo.orders")
    assert indexes[0].fields == ("customer_name",)
    assert indexes[0].unique is True


@pytest.mark.asyncio
async def test_a_shared_masker_lines_up_tokens_across_objects() -> None:
    """Why the masker is an argument at all. Two objects read through the same
    analysis must agree on the token for a shared value, or the foreign key
    between them is invisible."""
    masker = SampleMasker(salt=b"fixed")
    first = build_masked_source_inspection(StubInspection(), masker=masker)
    second = build_masked_source_inspection(StubInspection(), masker=masker)
    left = await _sample(first)
    right = await _sample(second)
    assert left[0]["customer_name"] == right[0]["customer_name"]


def test_a_sample_rendered_into_a_prompt_block_is_masked() -> None:
    """The other route to a model, and the one C3.5 named. Block 5 is text bound
    for a provider; delimiter neutralisation stops it forging structure and does
    nothing about it containing a customer."""
    blocks = build_prompt_blocks(
        task_definition="t",
        source_metadata=(),
        untrusted_samples={"warehouse.dbo.orders": ROWS},
        user_requirements="r",
    )
    rendered = blocks[4].render()
    assert "Jane Doe" not in rendered
    assert MASK_PREFIX in rendered
    # Structure the model needs is still legible: the field name, and the fact
    # that two of the three rows carry the same customer.
    assert "customer_name=" in rendered
    assert "total=41.5" in rendered


def test_a_prompt_block_does_not_mask_an_already_masked_row_twice() -> None:
    """Rows can arrive here having already come through the inspection port. A
    second mask over the first would report every field as a string of the
    surrogate's own length, destroying the size metadata that is the point.

    Asserted as "both routes render identically" rather than by matching the
    surrogate's text, because the surrogate's own length is a number that can
    coincide with a real value's -- `[MASKED:` happens to be eight characters,
    and so does "Jane Doe".
    """
    masker = SampleMasker(salt=b"fixed")

    def render(rows: Sequence[Mapping[str, Any]]) -> str:
        return build_prompt_blocks(
            task_definition="t",
            source_metadata=(),
            untrusted_samples={"warehouse.dbo.orders": rows},
            user_requirements="r",
            masker=masker,
        )[4].render()

    from_raw = render(ROWS)
    from_masked = render(masker.mask_rows(ROWS))
    assert from_raw == from_masked
    # One surrogate per sensitive cell, so none of them wraps another.
    assert from_raw.count(MASK_PREFIX) == len(ROWS)
