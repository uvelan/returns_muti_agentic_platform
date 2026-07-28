import random
from collections.abc import Iterable

from return_platform.data_platform.schema_registry import SchemaRegistry


def construct_dependency_graph(registry: SchemaRegistry, asset_ids: Iterable[str]) -> list[str]:
    # A simple topological sort.
    # Known ordering: customers -> products -> orders -> order_lines -> shipments
    # We will derive it by checking if an asset's generator is the same as another asset's natural key.
    # For AIG3, a simplified heuristic based on generator names:

    asset_order_heuristic = {
        "customers": 10,
        "customerOutboundCDM": 10,
        "products": 20,
        "lkpSearchProduct": 20,
        "orders": 30,
        "salesInv": 40,
        "shipmentInfo": 50,
        "workspaces": 5,
    }

    sorted_assets = sorted(list(asset_ids), key=lambda aid: asset_order_heuristic.get(aid, 100))
    return sorted_assets


class RelationshipResolver:
    def __init__(self) -> None:
        # Maps generator type (e.g. "customer_reference") to a list of available valid keys
        self.available_keys: dict[str, list[str]] = {}

    def add_key(self, generator_type: str, value: str) -> None:
        if generator_type not in self.available_keys:
            self.available_keys[generator_type] = []
        self.available_keys[generator_type].append(value)

    def get_key(self, generator_type: str, rng: random.Random) -> str | None:
        keys = self.available_keys.get(generator_type)
        if keys:
            return rng.choice(keys)
        return None
