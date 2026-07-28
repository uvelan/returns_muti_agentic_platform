import hashlib
import json

from .models import OperationalGenerationProposal


def calculate_proposal_checksum(proposal: OperationalGenerationProposal) -> str:
    # Exclude provenance metadata that changes like AI traces or metrics
    canonical_data = {
        "proposal_id": str(proposal.proposal_id),
        "schema_release_id": proposal.schema_release_id,
        "schema_checksum": proposal.schema_checksum,
        "deterministic_seed": proposal.deterministic_seed,
        "generation_mode": proposal.generation_mode.value,
        "records": [
            {
                "asset_id": rec.asset_id,
                "temporary_record_key": rec.temporary_record_key,
                "values": rec.values,
                "dependency_keys": rec.dependency_keys,
                "generation_index": rec.generation_index,
            }
            for rec in proposal.records
        ],
    }

    canonical_json = json.dumps(canonical_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
