"""Tests for deterministic E2E seed identity and scenario coverage."""

from datetime import UTC, datetime

from return_platform.operations.seed_manifest import (
    SEED_SCENARIOS,
    manifest_digest,
    materialize_seed,
    scenario_counts,
)


def test_seed_manifest_has_required_positive_and_negative_coverage() -> None:
    counts = scenario_counts()

    assert counts["total"] == len(SEED_SCENARIOS) == 1250
    assert counts["positive"] + counts["negative"] + counts["reviewRequired"] == counts["total"]
    assert counts["positive"] > 0
    assert counts["negative"] > 0
    assert counts["reviewRequired"] > 0

    assert len({str(item["id"]) for item in SEED_SCENARIOS}) == len(SEED_SCENARIOS)
    assert len({str(item["orderReference"]) for item in SEED_SCENARIOS}) == len(SEED_SCENARIOS)


def test_seed_digest_is_stable_across_materialization_times() -> None:
    version = "e2e-v2"
    expected_digest = manifest_digest(version)

    first = materialize_seed(version, datetime(2026, 7, 1, tzinfo=UTC))
    second = materialize_seed(version, datetime(2026, 7, 24, tzinfo=UTC))

    assert expected_digest == manifest_digest(version)

    first_orders = {str(order["_id"]): order for order in first[2]}
    second_orders = {str(order["_id"]): order for order in second[2]}

    assert first_orders.keys() == second_orders.keys()

    timestamp_fields = (
        "orderCreatedAt",
        "orderDate",
        "shippedAt",
        "deliveredAt",
        "updatedAt",
    )
    assert any(
        first_orders[order_id].get(field) != second_orders[order_id].get(field)
        for order_id in first_orders
        for field in timestamp_fields
    )

    assert {str(order["seedDigest"]) for order in first[2]} == {expected_digest}
    assert {str(order["seedDigest"]) for order in second[2]} == {expected_digest}


def test_seed_manifest_materializes_review_and_rejection_evidence() -> None:
    _, _, orders = materialize_seed("e2e-v2", datetime(2026, 7, 24, tzinfo=UTC))
    decisions = {str(order["expectedDecision"]) for order in orders}

    assert decisions == {"APPROVE", "REJECT", "REVIEW_REQUIRED"}
    assert any(order["status"] == "IN_TRANSIT" and order["deliveredAt"] is None for order in orders)
    assert any(order["expectedReasonCode"] == "FRAUD_SUSPECTED" for order in orders)
