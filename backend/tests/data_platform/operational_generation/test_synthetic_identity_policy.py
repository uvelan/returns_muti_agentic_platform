from datetime import UTC, datetime
from pathlib import Path

import pytest
from record_paths import at

from return_platform.data_platform.operational_generation import (
    CollisionPolicy,
    GenerationMode,
    GenerationRequest,
    HallucinationGuard,
    OperationalGenerator,
    ScenarioType,
)
from return_platform.data_platform.operational_generation.deterministic_values import (
    RESERVED_EMAIL_TLDS,
    get_synthetic_name,
)
from return_platform.data_platform.schema_registry import SchemaRegistry, load_schema_registry


@pytest.fixture
def registry() -> SchemaRegistry:
    project_root = Path(__file__).parent.parent.parent.parent.parent
    return load_schema_registry(project_root / "backend" / "config" / "schema_registry.yaml")


@pytest.fixture
def guard(registry: SchemaRegistry) -> HallucinationGuard:
    return HallucinationGuard(registry)


@pytest.fixture
def generator(registry: SchemaRegistry, guard: HallucinationGuard) -> OperationalGenerator:
    return OperationalGenerator(registry, guard)


@pytest.mark.asyncio
async def test_synthetic_email_policy(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    """Emails live inside `customer.address[]`, not in a field called `email`.

    The registry now mirrors the real salesInv documents, where an account's
    several email addresses are rows of one embedded array -- which is why
    `contact_point` is declared `distinct`. Looking for a top-level `email`
    field found no asset at all and the test died on `StopIteration`, reporting
    nothing about the policy it exists to protect.
    """
    asset = next(
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED"
        and any(f.generator == "customer_addresses" for f in a.fields)
        and a.write_policy != "DENIED"
    )

    req = GenerationRequest(
        asset_ids=(asset.asset_id,),
        record_count=1,
        deterministic_seed=123,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    proposal = await generator.generate_proposal(req)
    field = next(f for f in asset.fields if f.generator == "customer_addresses")
    addresses = at(proposal.records[0].values, field.name)
    emails = [row["email"] for row in addresses if "email" in row]

    assert emails, "customer.address[] carried no contact rows to check"
    # The policy is that a generated address can never be delivered to, NOT that
    # it is spelled `generated+...@example.invalid`. Asserting the literal made
    # the unreadable form the only compliant one and let three different
    # conventions drift apart behind it. Every TLD in RESERVED_EMAIL_TLDS is
    # reserved by RFC 2606 / RFC 6761 and cannot be registered, so
    # `riley.chen@irongatecontractors.example` is exactly as undeliverable.
    for email in emails:
        assert isinstance(email, str)
        assert email.endswith(RESERVED_EMAIL_TLDS), email
        assert "@" in email and not email.startswith("@")


@pytest.mark.asyncio
async def test_synthetic_phone_policy(
    generator: OperationalGenerator, registry: SchemaRegistry
) -> None:
    asset = next(
        a
        for a in registry.assets
        if a.generated_data_policy == "ENABLED"
        and any(f.generator == "phone" for f in a.fields)
        and a.write_policy != "DENIED"
    )

    req = GenerationRequest(
        asset_ids=(asset.asset_id,),
        record_count=1,
        deterministic_seed=123,
        tenant_id="test_tenant",
        date_from=datetime(2023, 1, 1, tzinfo=UTC),
        date_to=datetime(2023, 1, 31, tzinfo=UTC),
        generation_mode=GenerationMode.DETERMINISTIC,
        collision_policy=CollisionPolicy.REJECT,
        scenario_distribution={ScenarioType.POSITIVE: 1},
    )

    proposal = await generator.generate_proposal(req)
    # By generator, not by field name: the real path is
    # salesHdr.salesHdrData.shipping.shipTo.address.shipToPhone, and what the
    # policy is about is the value a `phone` generator may produce.
    field = next(f for f in asset.fields if f.generator == "phone")
    phone = at(proposal.records[0].values, field.name)
    assert isinstance(phone, str) and phone.startswith("555-01")


def test_synthetic_names_are_realistic_safe_and_deterministic() -> None:
    name = get_synthetic_name(2, seed=20260728)

    assert name == get_synthetic_name(2, seed=20260728)
    assert len(name.split()) == 2
    assert "Synthetic" not in name
    assert "Sandbox" not in name
    assert "Customer" not in name
    assert name != get_synthetic_name(2, seed=20260729)
