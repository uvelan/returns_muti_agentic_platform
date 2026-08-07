"""Active seed selection safeguards for rebuildable graph projection.

The fail-closed seed-digest-mismatch check and the guarantee that a pinned
seed generation is always read exhaustively (never truncated by
maxRecordsPerAsset) now live inside MongoDBSourceScanConnector.scan() --
see test_dynamic_knowledge/test_mongodb_connector.py's
test_scan_with_seed_pin_fails_closed_on_digest_mismatch and
test_scan_with_seed_pin_ignores_max_records_per_source, which are the
direct continuation of this file's original coverage.

What stays GraphSyncService's own responsibility -- and what this file
covers -- is correctly deciding *whether* a seed pin applies at all, and
pinning every Mongo source to the same seed generation when it does.
"""

from return_platform.data_platform.graph.sync_service import GraphSyncService


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
