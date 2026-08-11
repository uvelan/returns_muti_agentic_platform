import random

from return_platform.data_platform.schema_registry import SchemaRegistry

from .checksum import calculate_proposal_checksum
from .deterministic_values import (
    deterministic_random,
    generate_stable_date,
    generate_stable_string,
    generate_stable_uuid,
    get_synthetic_email,
    get_synthetic_name,
    get_synthetic_phone,
)
from .guard import HallucinationGuard
from .models import (
    GeneratedRecord,
    GenerationRequest,
    OperationalGenerationProposal,
    OperationProposal,
    ValidationResultState,
)
from .provenance import build_provenance
from .relationships import RelationshipResolver, construct_dependency_graph
from .scenarios import distribute_scenarios
from .semantic_values import SemanticValueProvider, get_deterministic_semantic_fallback

_RELATIONAL_SOURCE_ASSETS = frozenset(
    {
        "source.mongodb.customer_outbound_cdm",
        "source.mongodb.product_search",
        "source.mongodb.sales_inv",
        "source.mongodb.shipment_info",
    }
)

#: Generators whose values are a closed vocabulary in the source, taken from
#: the distribution observed in the Ferguson samples.
_LITERAL_GENERATORS: dict[str, tuple[str, ...]] = {
    "sales_doc_type": ("headerLines",),
    "sales_type": ("CASH", "INV"),
    "order_status": ("CALLCSR", "INVOICED", "INVOICE-PAID", "READY-FOR-PICKUP"),
    "source_code": ("SOE", "COPY"),
    "erp_code": ("Trilogie",),
    "party_type": ("PARTY",),
    "brand_type": ("Own Brand", "National Brand"),
    "unit_of_measure": ("EA", "BOX", "CASE"),
    "ship_via_description": ("OUR TRUCK", "COUNTER PICK-UP", "WILL CALL", "UPS GROUND"),
    "ship_via_code": ("OT", "CPU", "WCL", "UPSG"),
}

# The nested shapes below mirror the real Ferguson documents rather than a
# convenient flat approximation. They matter because the active schema reads
# through them: order_line explodes salesLines[] and reads lineData.*, and
# contact_point explodes customer.address[]. Seed data that flattens these
# would extract to nothing while every test still passed, which is the exact
# failure this seed previously had.


def _nest_dotted_keys(values: dict[str, object]) -> dict[str, object]:
    """Turn `{"salesHdr.salesHdrData.custId": x}` into real nested objects.

    The registry names a document source's fields by dotted path, because that
    is how the source contract is written down. Nothing was expanding those
    paths, so generated documents carried literal keys containing dots while
    the active schema read `physical_path: [salesHdr, salesHdrData, custId]`.
    Every nested field therefore extracted as null from seed data -- silently,
    since a null field is indistinguishable from an absent one.
    """
    nested: dict[str, object] = {}
    for key, value in values.items():
        if "." not in key:
            nested[key] = value
            continue
        *parents, leaf = key.split(".")
        cursor = nested
        for segment in parents:
            existing = cursor.get(segment)
            if not isinstance(existing, dict):
                existing = {}
                cursor[segment] = existing
            cursor = existing
        cursor[leaf] = value
    return nested


def _sales_lines(
    values: dict[str, object],
    resolver: RelationshipResolver,
    record_rng: random.Random,
    request: GenerationRequest,
    asset_id: str,
    index: int,
) -> list[dict[str, object]]:
    """salesLines[] entries: {salesLnsEventData: {...}, lineData: {...}}.

    Emits two to four lines. One line per order was the previous behaviour and
    it hid the index-pinning bug in the old schema -- with a single line,
    `salesLines.0.*` and a proper explosion produce identical results.
    """
    product_id = resolver.get_key("product_reference", record_rng)
    if not product_id:
        return []
    order_id = str(
        values.get(
            "salesHdrEventData.orderId",
            generate_stable_string(request.deterministic_seed, asset_id, index, "order_reference"),
        )
    )
    account_id = str(values.get("salesHdrEventData.accountId", "BRANCH"))
    warehouse = str(values.get("salesHdrEventData.sellWhseId", "0000"))
    lines: list[dict[str, object]] = []
    for line_number in range(1, record_rng.randint(2, 4) + 1):
        # The product document's `_id`, because that is what the graph joins:
        # `line_references_product` matches order_line.master_product_id
        # (lineData.masterProductId) against product.product_id, whose physical
        # path is `_id`. This used to take `master_product_reference`, which is
        # `fopt.mstrProdId` -- a composite of the id and a warehouse, so it
        # equalled no product's `_id` and REFERENCES_PRODUCT could not form.
        master_product_id = resolver.get_key("product_document_id", record_rng) or product_id
        ordered = record_rng.randint(1, 6)
        unit_price = round(record_rng.uniform(5.0, 450.0), 3)
        lines.append(
            {
                "salesLnsEventData": {
                    "account": account_id,
                    "orderId": order_id,
                    "lineNumber": str(line_number),
                    "lineType": "MP",
                },
                "lineData": {
                    "productId": f"{master_product_id}*{warehouse}",
                    "masterProductId": master_product_id,
                    # The SKU lives in altCode1, not a field called `sku`.
                    "altCode1": resolver.get_key("sku", record_rng),
                    "productDesc": resolver.get_key("product_description", record_rng),
                    "orderQty": ordered,
                    "shipQty": ordered,
                    "boQty": 0,
                    "netPrice": unit_price,
                    "lineNetAmt": round(unit_price * ordered, 2),
                    "invenWhse": warehouse,
                },
            }
        )
    return lines


def _customer_addresses(
    record_rng: random.Random, seed: int, index: int
) -> list[dict[str, object]]:
    """customer.address[]: one postal address repeated per contact point.

    The repetition is not an accident of the real data -- it is how the ERP
    carries several emails for one account, and it is why contact_point is
    declared `distinct`.
    """
    city = get_deterministic_semantic_fallback("customer.address", "city", seed, index)
    state = get_deterministic_semantic_fallback("customer.address", "state", seed, index)
    base = {
        "address1": get_deterministic_semantic_fallback(
            "customer.address", "address1", seed, index
        ),
        "city": city,
        "state": state,
        "postalCode": f"{record_rng.randint(10000, 99999)}",
        "county": get_deterministic_semantic_fallback("customer.address", "county", seed, index),
        "country": "US",
    }
    rows: list[dict[str, object]] = [{**base, "phoneNumber": get_synthetic_phone(record_rng)}]
    for contact in range(record_rng.randint(1, 3)):
        rows.append({**base, "email": get_synthetic_email(f"{seed}-{index}-{contact}")})
    return rows


def _cdm_parties(
    values: dict[str, object],
    resolver: RelationshipResolver,
    record_rng: random.Random,
    seed: int,
    index: int,
) -> list[dict[str, object]]:
    """customerOutboundCDM party[]: name, contact points, and the custAccts[]
    bridge whose additionalCustomerInfo[].customerId is what salesInv copies
    into custId."""
    party_id = str(values.get("partyId", generate_stable_string(seed, "cdm", index, "partyId")))
    name = get_synthetic_name(index, seed=seed)
    # Falls back to a stable id when no salesInv customer has been generated
    # yet -- asset generation order decides whether the resolver has one, and
    # emitting no custAccts at all would leave customer_account with zero
    # records and hide whether the exploded path works.
    customer_id = resolver.get_key("customer_reference", record_rng) or generate_stable_string(
        seed, "cdm", index, "customer_reference", 10
    )
    # Publish it. The resolver is fed from the field loop, which only registers
    # *string* values -- and this id is minted inside an array, so it was never
    # published and salesInv could not consume it. Both sides then invented
    # their own customer id and the documented bridge joined nothing: every
    # CustomerParty in the graph was an orphan, from seed data that looked
    # entirely plausible.
    resolver.add_key("customer_reference", customer_id)
    return [
        {
            "partyNumber": party_id,
            "partyName": name,
            "organizationName": name,
            "b2bCustomer": "Y",
            "customerContactPoints": [
                {
                    "contactPointType": "PHONE",
                    "phoneNumber": get_synthetic_phone(record_rng),
                    "searchPhoneNumber": get_synthetic_phone(record_rng),
                    "emailAddress": get_synthetic_email(f"{seed}-{index}"),
                    "personFirstName": name,
                    "personLastName": name,
                }
            ],
            "custAccts": [
                {
                    "accountName": name,
                    "additionalCustomerInfo": [
                        {
                            "customerId": customer_id,
                            "customerAcct": party_id,
                            "custBranchId": get_deterministic_semantic_fallback(
                                "cdm", "custBranchId", seed, index
                            ),
                            "shipToPhone": get_synthetic_phone(record_rng),
                        }
                    ],
                }
            ],
        }
    ]


class OperationalGenerator:
    def __init__(
        self,
        registry: SchemaRegistry,
        guard: HallucinationGuard,
        semantic_provider: SemanticValueProvider | None = None,
    ):
        self.registry = registry
        self.guard = guard
        self.semantic_provider = semantic_provider

    async def generate_proposal(self, request: GenerationRequest) -> OperationalGenerationProposal:
        # 1. Resolve approved assets, reject prohibited
        for asset_id in request.asset_ids:
            asset = self.registry.asset(asset_id)
            if (
                asset.owner == "OMC"
                or asset.write_policy == "DENIED"
                or asset.ownership == "DERIVED_PROJECTION"
            ):
                raise ValueError(f"Asset {asset_id} is prohibited for generation")
            if asset.generated_data_policy != "ENABLED":
                raise ValueError(f"Asset {asset_id} generation policy is disabled")

        # 2. Construct dependency graph
        sorted_assets = construct_dependency_graph(self.registry, request.asset_ids)

        # 3. Validate request distribution
        rng = random.Random(request.deterministic_seed)
        distribute_scenarios(rng, request.scenario_distribution)

        # 4. Generate records
        generated_records = []
        resolver = RelationshipResolver()

        for asset_id in sorted_assets:
            asset = self.registry.asset(asset_id)
            for i in range(request.record_count):
                record_rng = deterministic_random(request.deterministic_seed, asset_id, i, "record")
                # scenario unused

                from typing import Any

                values: dict[str, Any] = {}
                dep_keys = []

                # Assign Tenant
                if any(f.name == "tenant_id" for f in asset.fields):
                    values["tenant_id"] = request.tenant_id

                # Fill fields deterministically
                for field in asset.fields:
                    if field.name == "tenant_id":
                        continue

                    if field.name in asset.natural_keys:
                        values[field.name] = generate_stable_string(
                            request.deterministic_seed, asset_id, i, field.name, 10
                        )

                    elif field.name in asset.dependency_fields:
                        val = resolver.get_key(field.generator or "", record_rng)
                        if val:
                            values[field.name] = val
                            dep_keys.append(field.name)
                        else:
                            # if no valid parent exists, we might need a fallback or stable ID
                            values[field.name] = generate_stable_string(
                                request.deterministic_seed, asset_id, i, field.name, 10
                            )

                    elif field.type == "string" and field.generator in _LITERAL_GENERATORS:
                        # Enumerated values the schema actually filters or groups
                        # on. `sales_doc_type` in particular must be exact:
                        # sales_order declares `where docType == headerLines`, so
                        # a random string here would silently extract zero orders.
                        values[field.name] = record_rng.choice(_LITERAL_GENERATORS[field.generator])

                    elif field.type == "string":
                        # Dispatch on the declared generator, not the field name.
                        # These used to test `field.name == "email"` /
                        # `"phoneNumber"` / `"customerName"`, which were field
                        # names of the earlier flat schema. Registry names are
                        # now paths -- the one phone field is
                        # `salesHdr.salesHdrData.shipping.shipTo.address.shipToPhone`
                        # -- so no comparison matched and the STRICT_SYNTHETIC
                        # identity policy quietly stopped applying: the field fell
                        # through to the generic semantic fallback and got the
                        # literal string "Deterministic <path> <seed>-<index>"
                        # where a phone number belongs. The generator is the
                        # declared intent and does not move when a path does.
                        if field.generator == "email":
                            values[field.name] = get_synthetic_email(
                                f"{request.deterministic_seed}-{i}"
                            )
                        elif field.generator == "phone":
                            values[field.name] = get_synthetic_phone(record_rng)
                        elif field.generator == "person_name":
                            values[field.name] = get_synthetic_name(
                                i, seed=request.deterministic_seed
                            )
                        elif "date" in field.name.lower() or "time" in field.name.lower():
                            # Note: datetime fields should be handled by 'datetime' type, but string dates exist
                            dt = generate_stable_date(
                                record_rng, request.date_from, request.date_to
                            )
                            values[field.name] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                        else:
                            values[field.name] = get_deterministic_semantic_fallback(
                                asset_id, field.name, request.deterministic_seed, i
                            )
                    elif field.type == "datetime":
                        dt = generate_stable_date(record_rng, request.date_from, request.date_to)
                        values[field.name] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    elif field.type == "boolean":
                        values[field.name] = record_rng.choice([True, False])
                    elif field.type in ("number", "integer"):
                        if field.minimum is not None and field.maximum is not None:
                            values[field.name] = record_rng.randint(
                                int(field.minimum), int(field.maximum)
                            )
                        else:
                            values[field.name] = record_rng.randint(1, 100)
                    elif field.type == "array":
                        if field.generator == "customer_accounts":
                            customer_id = resolver.get_key("customer_reference", record_rng)
                            values[field.name] = (
                                [{"accountNumber": customer_id, "status": "ACTIVE"}]
                                if customer_id
                                else []
                            )
                        elif field.generator == "sales_lines":
                            values[field.name] = _sales_lines(
                                values, resolver, record_rng, request, asset_id, i
                            )
                        elif field.generator == "customer_addresses":
                            values[field.name] = _customer_addresses(
                                record_rng, request.deterministic_seed, i
                            )
                        elif field.generator == "cdm_parties":
                            values[field.name] = _cdm_parties(
                                values, resolver, record_rng, request.deterministic_seed, i
                            )
                        else:
                            values[field.name] = []
                    elif field.type == "object":
                        values[field.name] = {}

                    generated_value = values.get(field.name)
                    if (
                        asset_id in _RELATIONAL_SOURCE_ASSETS
                        and field.generator
                        and isinstance(generated_value, str)
                    ):
                        resolver.add_key(field.generator, generated_value)

                # salesInv's _id is a composite of the two fields generated
                # above it, not an independent identifier: the real documents
                # key on ACCOUNT*ORDERNUMBER, and sales_order.order_key reads
                # _id directly. Composed after the field loop because both
                # parts have to exist first.
                if asset_id == "source.mongodb.sales_inv":
                    account = values.get("salesHdrEventData.accountId")
                    order = values.get("salesHdrEventData.orderId")
                    if isinstance(account, str) and isinstance(order, str):
                        values["_id"] = f"{account}*{order}"

                record_key = str(
                    generate_stable_uuid(request.deterministic_seed, asset_id, i, "record_key")
                )

                generated_records.append(
                    GeneratedRecord(
                        asset_id=asset_id,
                        temporary_record_key=record_key,
                        values=values,
                        dependency_keys=tuple(dep_keys),
                        generation_index=i,
                    )
                )

        # 5. Hallucination Guard
        for asset_id in sorted_assets:
            recs = [dict(r.values) for r in generated_records if r.asset_id == asset_id]
            if not recs:
                continue

            op_proposal = OperationProposal(asset_id=asset_id, records=recs)
            result = self.guard.validate(op_proposal, tenant_id=request.tenant_id)
            if result.state != ValidationResultState.VALID:
                raise ValueError(f"Guard failed for asset {asset_id}: {result.findings}")

        # 5b. Materialise the documents the registry's dotted paths describe.
        # Deliberately after the guard: the guard validates records against the
        # registry's own flat field names, so nesting earlier would make every
        # generated field look unrecognised to it.
        generated_records = [
            record.model_copy(update={"values": _nest_dotted_keys(dict(record.values))})
            if record.asset_id in _RELATIONAL_SOURCE_ASSETS
            else record
            for record in generated_records
        ]

        # 6. Build final proposal
        proposal_id = generate_stable_uuid(request.deterministic_seed, "proposal", 0, "id")

        prov = build_provenance("1.0.0", {}, [])

        proposal = OperationalGenerationProposal(
            proposal_id=proposal_id,
            schema_release_id=self.registry.schema_version,
            schema_checksum="MOCK_CHECKSUM",
            deterministic_seed=request.deterministic_seed,
            generation_mode=request.generation_mode,
            records=tuple(generated_records),
            provenance=prov,
            proposal_checksum="",
        )

        # Calculate true checksum
        proposal_checksum = calculate_proposal_checksum(proposal)

        return OperationalGenerationProposal(
            proposal_id=proposal_id,
            schema_release_id=self.registry.schema_version,
            schema_checksum="MOCK_CHECKSUM",
            deterministic_seed=request.deterministic_seed,
            generation_mode=request.generation_mode,
            records=tuple(generated_records),
            provenance=prov,
            proposal_checksum=proposal_checksum,
        )
