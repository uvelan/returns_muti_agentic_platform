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

                    elif field.type == "string":
                        if field.name == "email":
                            values[field.name] = get_synthetic_email(
                                f"{request.deterministic_seed}-{i}"
                            )
                        elif field.name == "phoneNumber":
                            values[field.name] = get_synthetic_phone(record_rng)
                        elif field.name == "customerName":
                            values[field.name] = get_synthetic_name(i)
                        elif field.generator == "person_name":
                            values[field.name] = get_synthetic_name(i)
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
                        values[field.name] = []
                    elif field.type == "object":
                        values[field.name] = {}

                    # Registration of natural keys to resolver
                    if field.name in asset.natural_keys and field.generator:
                        resolver.add_key(field.generator, values[field.name])

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
            recs = [r.values for r in generated_records if r.asset_id == asset_id]
            if not recs:
                continue

            op_proposal = OperationProposal(asset_id=asset_id, records=recs)
            result = self.guard.validate(op_proposal, tenant_id=request.tenant_id)
            if result.state != ValidationResultState.VALID:
                raise ValueError(f"Guard failed for asset {asset_id}: {result.findings}")

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
