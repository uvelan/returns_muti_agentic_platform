"""Active seed selection safeguards for rebuildable graph projection.

The fail-closed seed-digest-mismatch check and the guarantee that a pinned
seed generation is always read exhaustively (never truncated by
maxRecordsPerAsset) now live inside MongoDBSourceScanConnector.scan() --
see test_dynamic_knowledge/test_mongodb_connector.py's
test_scan_with_seed_pin_fails_closed_on_digest_mismatch and
test_scan_with_seed_pin_ignores_max_records_per_source, which are the
direct continuation of this file's original coverage.

What stays GraphSyncService's own responsibility -- and what this file
covers -- is correctly deciding *whether* a seed pin applies at all, which
sources it applies to, and which store each Mongo source is read from.
"""

from pathlib import Path

from return_platform.data_platform.graph.sync_service import GraphSyncService
from return_platform.dynamic_knowledge.config_loader import load_active_schema

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)


def test_no_seed_pin_when_seed_version_and_digest_are_absent() -> None:
    pins = GraphSyncService._build_seed_pins(
        frozenset({"customer_outbound", "sales_inventory"}), None, None
    )
    assert pins is None


def test_every_mongo_source_is_pinned_to_the_same_seed_generation() -> None:
    pins = GraphSyncService._build_seed_pins(
        frozenset({"customer_outbound", "sales_inventory"}), "v2", "digest-v2"
    )
    assert pins is not None
    assert set(pins) == {"customer_outbound", "sales_inventory"}
    assert all(pin.seed_version == "v2" and pin.seed_digest == "digest-v2" for pin in pins.values())


def test_the_return_side_sources_are_read_from_the_platform_store() -> None:
    """Which Mongo connector each source resolves to.

    A connector is bound to one database, and the return side lives in the
    platform's own. Resolved by connector type alone -- as this did until the
    return entities were added -- `cases` was scanned against the upstream
    Ferguson database, found nothing, and the run reported success.
    """
    schema = load_active_schema(SCHEMA_PATH)

    platform = GraphSyncService.platform_store_source_ids(
        schema, frozenset(schema.sources), "return_platform"
    )

    assert platform == {
        "source_return_cases",
        "source_return_records",
        "source_return_items",
        "source_handling_units",
    }


def test_an_upstream_source_is_never_routed_to_the_platform_store() -> None:
    """The other direction of the same mistake.

    Redirecting `salesInv` into the platform database would empty the order side
    of the graph, which is a far louder failure but no less silent at the point
    it happens.
    """
    schema = load_active_schema(SCHEMA_PATH)

    platform = GraphSyncService.platform_store_source_ids(
        schema, frozenset(schema.sources), "return_platform"
    )

    assert "source_sales" not in platform
    assert "source_customers" not in platform
    assert "source_products" not in platform
    assert "source_shipments" not in platform


def test_nothing_is_routed_to_the_platform_store_under_a_different_database_name() -> None:
    """`object_ref.database` is matched exactly against the configured platform
    database, so an installation that renamed it gets the previous behaviour --
    every Mongo source on the upstream connector -- rather than a startup
    failure over an infrastructure rename."""
    schema = load_active_schema(SCHEMA_PATH)

    platform = GraphSyncService.platform_store_source_ids(
        schema, frozenset(schema.sources), "renamed_platform_database"
    )

    assert platform == frozenset()
