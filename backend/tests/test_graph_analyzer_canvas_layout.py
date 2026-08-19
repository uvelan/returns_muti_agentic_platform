"""Entity positions stay on the canvas whatever the graph contains.

`GraphEntity.x` and `.y` are percentages bounded 0..100 by the model itself.
The original layout was a fixed three-column grid with a constant row pitch,
which walks off the canvas on the fourth row -- `25 + (9 // 3) * 35` is 130 --
so `GET /api/graph-analyzer/v1/bootstrap` answered 500 against any system graph
with ten or more entities. Nothing caught it because every fixture graph in the
suite is smaller than that, and the real one is not.

The counts below bracket that boundary rather than sampling near it.
"""

from __future__ import annotations

import pytest

from return_platform.graph_analyzer.models import GraphEntity
from return_platform.graph_analyzer.service import _canvas_position


@pytest.mark.parametrize("total", [1, 2, 3, 9, 10, 11, 25, 60, 200, 1000])
def test_every_position_is_inside_the_canvas(total: int) -> None:
    for index in range(total):
        x, y = _canvas_position(index, total)
        assert 0.0 <= x <= 100.0, (index, total, x)
        assert 0.0 <= y <= 100.0, (index, total, y)


@pytest.mark.parametrize("total", [1, 9, 10, 40])
def test_the_model_accepts_every_position(total: int) -> None:
    """The bound that matters is the model's, so assert against the model."""
    for index in range(total):
        x, y = _canvas_position(index, total)
        entity = GraphEntity(
            id=f"e{index}",
            name=f"Entity{index}",
            description="",
            x=x,
            y=y,
            properties=[],
            constraints=[],
            change="UNCHANGED",
        )
        assert entity.x == x
        assert entity.y == y


def test_positions_are_distinct_so_entities_do_not_stack() -> None:
    """A layout inside the bounds that puts everything in one spot is no layout."""
    total = 12
    assert len({_canvas_position(i, total) for i in range(total)}) == total


def test_a_single_entity_is_centred_rather_than_pinned_to_a_corner() -> None:
    assert _canvas_position(0, 1) == (50.0, 50.0)
