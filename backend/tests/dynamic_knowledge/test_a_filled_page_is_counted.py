"""A search page that filled is counted, and the envelope says so.

Seven customers named SILVERLAKE AIR SYSTEMS behind a page of five came back as
five, `total_found` counted the five, and the two on the account the associate
had just confirmed were never seen (2026-09-10). The node now asks the graph how
many there really are when a page fills, and names the signal as truncated.

Same harness as the concurrency parity tests: real guards, real compiler, real
catalogue, a graph double that answers by field.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.dynamic_knowledge.knowledge.query_plan import QueryOperation
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.identification import IdentificationCatalogue
from return_platform.dynamic_knowledge.order_agent.search_strategy import build_search_program
from return_platform.dynamic_knowledge.schema import ActiveSchema
from tests.dynamic_knowledge.test_concurrent_search_parity import (
    _Graph,
    _ranked_from,
    _run,
    catalogue,  # noqa: F401 -- fixtures
    production_schema,  # noqa: F401
)


class _CountingGraph(_Graph):
    """The parity double, able to answer a COUNT with the true figure."""

    def __init__(
        self, rows_by_field: dict[str, list[dict[str, Any]]], *, totals: dict[str, int]
    ) -> None:
        super().__init__(rows_by_field)
        self.totals = totals

    async def execute(
        self,
        *,
        schema: Any,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any:
        if plan.operation is QueryOperation.COUNT:
            key = plan.filters[0].field_id
            self.executed.append(f"count:{key}")
            assert "count(" in compiled_cypher
            return {"rows": [{"value": self.totals[key]}], "count": 1}
        return await super().execute(
            schema=schema,
            graph_generation_id=graph_generation_id,
            plan=plan,
            compiled_cypher=compiled_cypher,
            parameters=parameters,
        )


def _silverlake(count: int) -> list[dict[str, Any]]:
    return [
        {"customer_id": f"C{index}", "customer_name": "SILVERLAKE AIR SYSTEMS", "account_id": "X"}
        for index in range(count)
    ]


INTENT = {"searchMode": "SEARCH", "confidence": 0.9, "customerNames": ["SILVERLAKE AIR SYSTEMS"]}


def _page_limit(catalogue: IdentificationCatalogue) -> int:
    program = build_search_program(OrderSearchIntent.model_validate(INTENT), catalogue)
    [planned] = [item for item in program.primary if item.intent_key == "customerNames"]
    return planned.plan.limit


@pytest.mark.asyncio
async def test_a_filled_page_is_counted_and_the_total_is_the_graphs(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    limit = _page_limit(catalogue)
    graph = _CountingGraph(
        {"customer_name": _silverlake(limit)}, totals={"customer_name": limit + 2}
    )

    result, evidence = await _run(production_schema, catalogue, graph, INTENT)

    assert graph.executed == ["customer_name", "count:customer_name"]
    assert result["queries_used"] == 2
    assert result["order_search_cache"]["totalFound"] == limit + 2
    ranked = _ranked_from(result, evidence)
    assert ranked["total_found"] == limit + 2
    assert ranked["truncated_signals"] == ["customerNames"]
    assert len(ranked["candidates"]) == limit


@pytest.mark.asyncio
async def test_a_page_below_the_limit_is_not_counted(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    limit = _page_limit(catalogue)
    graph = _CountingGraph({"customer_name": _silverlake(limit - 1)}, totals={})

    result, evidence = await _run(production_schema, catalogue, graph, INTENT)

    assert graph.executed == ["customer_name"]
    assert result["queries_used"] == 1
    ranked = _ranked_from(result, evidence)
    assert ranked["total_found"] == limit - 1
    assert "truncated_signals" not in ranked


@pytest.mark.asyncio
async def test_a_count_the_budget_cannot_afford_leaves_the_total_a_floor_and_still_names_the_signal(
    production_schema: ActiveSchema, catalogue: IdentificationCatalogue
) -> None:
    limit = _page_limit(catalogue)
    graph = _CountingGraph({"customer_name": _silverlake(limit)}, totals={"customer_name": 99})

    result, evidence = await _run(production_schema, catalogue, graph, INTENT, budget=1)

    assert graph.executed == ["customer_name"]
    ranked = _ranked_from(result, evidence)
    assert ranked["total_found"] == limit
    assert ranked["truncated_signals"] == ["customerNames"]
