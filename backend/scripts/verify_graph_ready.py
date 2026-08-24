"""Refuse a graph that is built but cannot answer.

`build_knowledge_graph.py` already refuses a COMPLETED run that wrote no nodes or
no relationships. That catches an empty build and nothing subtler, and the
failures that actually reach an associate are subtler:

  * A generation holding 2,781 order lines of which **one** reaches a product.
    Nodes written, relationships written, run COMPLETED -- and every order opens
    with no items on it.
  * A serving generation the full-text index does not cover, so every customer
    search returns nothing. The copilot then has no matches to show, and the
    model in front of it has been observed inventing five accounts rather than
    saying so. `find order for BLUEFIN` produced five "BLUEFIN UTILITIES" rows
    against branch codes borrowed from real data; the graph contained no
    BLUEFIN at all.
  * Two generations present where the complete dataset is the FAILED one and the
    ACTIVE one holds something else entirely.

None of those are visible in a build log. All of them are one query away, and
the point of asking here is that a bad load fails the script that produced it
rather than the associate holding a box three days later.

Exit codes: 0 ready, 1 not ready (reasons printed), 2 could not check.

    python backend/scripts/verify_graph_ready.py
    python backend/scripts/verify_graph_ready.py --sample 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neo4j import AsyncGraphDatabase

from return_platform.configuration.settings import Settings

#: Order lines that reach a product, below which the graph is not usable. Not
#: 100%: a real catalogue has discontinued lines. The observed failure was 0.03%
#: and the observed healthy build 99.9%, so anything under this is the failure.
MINIMUM_PRODUCT_LINK_RATIO = 0.80

#: A serving generation with fewer customers than this is a partial load, not a
#: small dataset.
MINIMUM_CUSTOMERS = 10


class NotReady(Exception):
    """A specific, actionable reason the graph cannot serve."""


async def _one(session: Any, cypher: str, **params: Any) -> dict[str, Any] | None:
    result = await session.run(cypher, **params)
    async for record in result:
        return dict(record)
    return None


async def _serving_generation(session: Any) -> str:
    row = await _one(
        session,
        """MATCH (g:GraphGeneration) WHERE g.status = 'ACTIVE'
           RETURN g.generation_id AS gen ORDER BY g.created_at DESC LIMIT 1""",
    )
    if row is None or not row.get("gen"):
        raise NotReady(
            "No ACTIVE GraphGeneration. Nothing is serving, so every discovery "
            "read returns nothing. Run the graph build."
        )
    return str(row["gen"])


async def _check_customers(session: Any, gen: str) -> int:
    row = await _one(
        session,
        "MATCH (c:Customer {graph_generation_id:$g}) RETURN count(c) AS n",
        g=gen,
    )
    count = int((row or {}).get("n") or 0)
    if count < MINIMUM_CUSTOMERS:
        raise NotReady(
            f"The serving generation holds {count} customers, under the {MINIMUM_CUSTOMERS} "
            "a real load produces. The build wrote a generation the loader did not fill."
        )
    return count


async def _check_product_links(session: Any, gen: str) -> float:
    row = await _one(
        session,
        """MATCH (l:OrderLine {graph_generation_id:$g})
           OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product {graph_generation_id:$g})
           RETURN count(l) AS lines, count(p) AS linked""",
        g=gen,
    )
    lines = int((row or {}).get("lines") or 0)
    linked = int((row or {}).get("linked") or 0)
    if lines == 0:
        raise NotReady(
            "The serving generation holds no order lines, so no order can show what is on it."
        )
    ratio = linked / lines
    if ratio < MINIMUM_PRODUCT_LINK_RATIO:
        raise NotReady(
            f"Only {linked} of {lines} order lines ({ratio:.1%}) reach a product in the "
            f"serving generation; at least {MINIMUM_PRODUCT_LINK_RATIO:.0%} is required. "
            "Every order will open with no items on it. The product join did not run -- "
            "rebuild the graph rather than shipping this generation."
        )
    return ratio


async def _check_search_index(session: Any, gen: str) -> str:
    """The index must find a name that is provably in the serving generation.

    Searching a name nobody has is not a test: it returns nothing whether the
    index is healthy or absent. So this reads a real name out of the generation
    first and then asks the index for it -- the only version that distinguishes
    "no such customer" from "the search cannot see this generation".
    """
    row = await _one(
        session,
        """MATCH (c:Customer {graph_generation_id:$g})
           WHERE c.customer_name IS NOT NULL AND size(c.customer_name) > 3
           RETURN c.customer_name AS name LIMIT 1""",
        g=gen,
    )
    name = str((row or {}).get("name") or "").strip()
    if not name:
        raise NotReady("No customer in the serving generation carries a name to search on.")

    token = name.split()[0]
    hit = await _one(
        session,
        """CALL db.index.fulltext.queryNodes('customer_name_search_v2', $t) YIELD node
           WITH node WHERE node.graph_generation_id = $g
           RETURN count(node) AS n""",
        t=token,
        g=gen,
    )
    if int((hit or {}).get("n") or 0) == 0:
        raise NotReady(
            f"The full-text index returns nothing for {token!r}, a name that IS in the "
            "serving generation. Every customer search will come back empty -- and an "
            "empty search is what the discovery agent has been observed answering with "
            "invented accounts. Rebuild the index before serving this generation."
        )
    return token


async def _check_reachable_orders(session: Any, gen: str, sample: int) -> list[str]:
    """A customer must reach orders, lines and products -- the copilot's whole path."""
    result = await session.run(
        """MATCH (c:Customer {graph_generation_id:$g})-[:PLACED_ORDER]->(o:SalesOrder {graph_generation_id:$g})
           MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine {graph_generation_id:$g})
                 -[:REFERENCES_PRODUCT]->(p:Product {graph_generation_id:$g})
           WITH c, count(DISTINCT o) AS orders, count(DISTINCT p) AS products
           RETURN c.customer_name AS name, orders, products LIMIT $n""",
        g=gen,
        n=sample,
    )
    rows = [dict(record) async for record in result]
    if not rows:
        raise NotReady(
            "No customer in the serving generation reaches an order, a line and a product. "
            "The copilot can identify a customer and will then show an empty return."
        )
    return [f"{r['name']}: {r['orders']} orders, {r['products']} products" for r in rows]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=3, help="customers to report on")
    arguments = parser.parse_args()

    try:
        settings = Settings()  # type: ignore[call-arg]
        driver = AsyncGraphDatabase.driver(
            str(settings.neo4j_uri),
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        )
    except Exception as exc:  # noqa: BLE001 - a missing config is not a bad graph
        print(f"[graph-ready] could not connect: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        async with driver.session() as session:
            gen = await _serving_generation(session)
            print(f"[graph-ready] serving generation {gen}")

            customers = await _check_customers(session, gen)
            print(f"[graph-ready]   customers            {customers}")

            ratio = await _check_product_links(session, gen)
            print(f"[graph-ready]   order lines -> product {ratio:.1%}")

            token = await _check_search_index(session, gen)
            print(f"[graph-ready]   search index         finds {token!r}")

            reachable = await _check_reachable_orders(session, gen, arguments.sample)
            for line in reachable:
                print(f"[graph-ready]   reachable            {line}")
    except NotReady as reason:
        print(f"\n[graph-ready] NOT READY: {reason}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - report rather than a traceback
        print(f"\n[graph-ready] check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        await driver.close()

    print("\n[graph-ready] OK: the serving generation can answer a discovery turn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
