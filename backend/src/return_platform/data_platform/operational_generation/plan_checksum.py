import hashlib
import json

from .write_models import OperationalWritePlan


def calculate_plan_checksum(plan: OperationalWritePlan) -> str:
    from typing import Any

    canonical_data: dict[str, Any] = {
        "proposal_checksum": plan.proposal_checksum,
        "schema_release_id": plan.schema_release_id,
        "schema_checksum": plan.schema_checksum,
        "idempotency_key": plan.idempotency_key,
        "saga_steps": [],
    }

    for step in plan.saga_steps:
        s_data: dict[str, Any] = {
            "step_index": step.step_index,
            "rollback_feasibility": step.rollback_feasibility.value,
            "transaction_groups": [],
        }
        for tg in step.transaction_groups:
            tg_data = {
                "target_channel": tg.target_channel,
                "operations": [
                    {
                        "type": op.type.value,
                        "asset_id": op.asset_id,
                        "payload": op.payload,
                        "dependencies": op.dependencies,
                    }
                    for op in tg.operations
                ],
            }
            s_data["transaction_groups"].append(tg_data)
        canonical_data["saga_steps"].append(s_data)

    canonical_json = json.dumps(canonical_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
