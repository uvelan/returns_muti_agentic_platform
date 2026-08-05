"""Search strategy and ranking for order intent."""

from __future__ import annotations

import re
from typing import Any

from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent


def normalize_string(val: str) -> str:
    """Normalize string for search matching."""
    return re.sub(r'[\s\-]+', '', val.lower())


def build_progressive_plans(intent: OrderSearchIntent) -> list[LogicalQueryPlan]:
    """Translate search intent into a sequence of query plans."""
    plans = []
    
    # 1. Exact passes
    order_nums = set(intent.orderNumbers + intent.orderIds)
    for num in order_nums:
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="sales_order",
                filters=(
                    QueryCondition(
                        entity_id="sales_order",
                        field_id="sales_order_number",
                        operator="EXACT",
                        value=num,
                    ),
                ),
                limit=1,
            )
        )
        
    # 2. Customer passes
    for name in intent.customerNames:
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="customer",
                filters=(
                    QueryCondition(
                        entity_id="customer",
                        field_id="customer_name",
                        operator="CONTAINS",
                        value=name,
                    ),
                ),
                limit=5,
            )
        )
        
    # 3. Product passes
    for sku in intent.skus:
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="product",
                filters=(
                    QueryCondition(
                        entity_id="product",
                        field_id="sku",
                        operator="EXACT",
                        value=sku,
                    ),
                ),
                limit=5,
            )
        )

    # 4. Fallback search (free text / ambiguous)
    for term in intent.freeTextTerms:
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="sales_order",
                filters=(
                    QueryCondition(
                        entity_id="sales_order",
                        field_id="sales_order_number",
                        operator="CONTAINS",
                        value=term,
                    ),
                ),
                limit=5,
            )
        )

    return plans


def rank_search_results(
    intent: OrderSearchIntent, 
    raw_results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Score search results and return aggregated evidence."""
    # This acts as the evidence-based ranking stage.
    candidates = {}
    
    for res in raw_results:
        # Assuming Neo4j response structure with 'nodes' or direct dictionaries
        items = res.get("nodes", []) if isinstance(res, dict) else res
        
        for item in items:
            props = item.get("properties", item) if isinstance(item, dict) else {}
            
            # Identify identity
            key = props.get("sales_order_number") or props.get("customer_id") or props.get("sku") or str(id(props))
            
            if key not in candidates:
                candidates[key] = {"data": props, "score": 0.0, "matches": []}
                
            # Evidence scoring
            score = 0.5 # base score
            
            # Boosts based on exact match
            norm_key = normalize_string(key)
            for intent_id in intent.orderNumbers + intent.orderIds:
                if normalize_string(intent_id) == norm_key:
                    score += 0.5
                    candidates[key]["matches"].append("order_number_exact")
                    
            if "customer_name" in props:
                norm_name = normalize_string(props["customer_name"])
                for intent_name in intent.customerNames:
                    if normalize_string(intent_name) in norm_name:
                        score += 0.3
                        candidates[key]["matches"].append("customer_name_contains")
                        
            candidates[key]["score"] = min(1.0, candidates[key]["score"] + score)

    # Sort by score descending
    sorted_candidates = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
    
    return {
        "intent": intent.model_dump(),
        "candidates": sorted_candidates[:10], # Top 10
        "total_found": len(sorted_candidates)
    }
