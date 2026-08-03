"""Tests for deterministic E2E seed identity and scenario coverage."""

import json
from datetime import UTC, datetime

from return_platform.operations.seed_manifest import (
    SEED_CUSTOMERS,
    SEED_MANIFEST_PATH,
    SEED_ORDERS,
    SEED_PRODUCTS,
    SEED_SCENARIOS,
    effective_seed_counts,
    manifest_digest,
    materialize_domain_seed,
    materialize_seed,
    scenario_counts,
)

TEST_EVIDENCE_KEY = "unit-test-seed-evidence-key"


def test_seed_manifest_has_required_positive_and_negative_coverage() -> None:
    counts = scenario_counts()

    assert counts == {
        "positive": 5,
        "negative": 3,
        "reviewRequired": 2,
        "total": 10,
    }
    assert len({str(item["id"]) for item in SEED_SCENARIOS}) == 10
    assert len({str(item["orderReference"]) for item in SEED_SCENARIOS}) == 10


def test_seed_digest_is_stable_across_materialization_times() -> None:
    version = "e2e-v2"
    first_digest = manifest_digest(version, TEST_EVIDENCE_KEY)
    first = materialize_seed(
        version,
        datetime(2026, 7, 1, tzinfo=UTC),
        TEST_EVIDENCE_KEY,
    )
    second = materialize_seed(
        version,
        datetime(2026, 7, 24, tzinfo=UTC),
        TEST_EVIDENCE_KEY,
    )

    assert first_digest == manifest_digest(version, TEST_EVIDENCE_KEY)
    assert first[2][0]["deliveredAt"] != second[2][0]["deliveredAt"]
    assert {str(first[2][index]["seedDigest"]) for index in (0, len(first[2]) - 1)} == {
        first_digest
    }
    assert {str(second[2][index]["seedDigest"]) for index in (0, len(second[2]) - 1)} == {
        first_digest
    }


def test_seed_manifest_materializes_review_and_rejection_evidence() -> None:
    _, _, orders = materialize_seed(
        "e2e-v2",
        datetime(2026, 7, 24, tzinfo=UTC),
        TEST_EVIDENCE_KEY,
    )
    scenario_orders = orders[: len(SEED_SCENARIOS)]
    decisions = {str(order["expectedDecision"]) for order in scenario_orders}

    assert decisions == {"APPROVE", "REJECT", "REVIEW_REQUIRED"}
    assert any(
        order["status"] == "IN_TRANSIT" and order["deliveredAt"] is None
        for order in scenario_orders
    )
    assert any(order["expectedReasonCode"] == "FRAUD_SUSPECTED" for order in scenario_orders)


def test_realistic_seed_has_required_counts_and_multi_line_orders() -> None:
    records = materialize_domain_seed(
        "e2e-v2",
        datetime(2026, 7, 24, tzinfo=UTC),
        TEST_EVIDENCE_KEY,
    )

    assert len(SEED_CUSTOMERS) == 1_000
    assert len(SEED_PRODUCTS) == 1_000
    assert len(SEED_ORDERS) == 1_000
    assert len(records["customerOutboundCDM"]) == 1_000
    assert len(records["lkpSearchProduct"]) == 1_000
    assert len(records["salesInv"]) == 1_000
    assert len(records["shipmentInfo"]) == 1_000
    assert any(len(order["salesLines"]) > 1 for order in records["salesInv"])


def test_request_scoped_record_limit_bounds_each_seed_dataset() -> None:
    counts = effective_seed_counts(25)
    customers, products, orders = materialize_seed(
        "limited-seed",
        datetime(2026, 7, 24, tzinfo=UTC),
        TEST_EVIDENCE_KEY,
        25,
    )
    domain = materialize_domain_seed(
        "limited-seed",
        datetime(2026, 7, 24, tzinfo=UTC),
        TEST_EVIDENCE_KEY,
        25,
    )

    assert counts == {"customers": 25, "products": 25, "orders": 25}
    assert len(customers) == 25
    assert len(products) == 25
    assert len(orders) == 25
    assert len(domain["customerOutboundCDM"]) == 25
    assert len(domain["lkpSearchProduct"]) == 25
    assert len(domain["salesInv"]) == 25
    assert len(domain["shipmentInfo"]) == 25


def test_seed_evidence_is_keyed_and_never_contains_the_key() -> None:
    first = manifest_digest("e2e-v2", "first-evidence-key")
    second = manifest_digest("e2e-v2", "second-evidence-key")

    assert first != second
    assert "first-evidence-key" not in first


def test_seed_definitions_are_loaded_from_editable_json_manifest() -> None:
    payload = json.loads(SEED_MANIFEST_PATH.read_text(encoding="utf-8"))
    first_names = payload["customerCatalog"]["firstNames"]
    last_names = payload["customerCatalog"]["lastNames"]

    assert payload["counts"] == {
        "customers": 10_000,
        "products": 20_000,
        "orders": 1_000_000,
    }
    assert len(first_names) == 120
    assert len(set(first_names)) == 120
    assert len(last_names) == 70
    assert len(set(last_names)) == 70
    assert len(payload["scenarios"]) == 10
    assert payload["productCatalog"]["fixedProducts"]
