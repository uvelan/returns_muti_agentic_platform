from typing import Any, Protocol

from pydantic import BaseModel


class SemanticValueRequest(BaseModel):
    allowed_fields: tuple[str, ...]
    asset_id: str
    record_context: dict[str, Any]


class SemanticValueResult(BaseModel):
    values: dict[str, Any]
    provider: str
    metrics: dict[str, Any]


class SemanticValueProvider(Protocol):
    async def generate_values(self, request: SemanticValueRequest) -> SemanticValueResult: ...


def get_deterministic_semantic_fallback(
    asset_id: str, field_name: str, seed: int, record_index: int
) -> str:
    if field_name == "customerName" or "name" in field_name.lower():
        return f"Synthetic Customer {seed}-{record_index}"
    if (
        field_name == "productDescription"
        or "description" in field_name.lower()
        or "reason" in field_name.lower()
        or "notes" in field_name.lower()
    ):
        return f"Operationally generated {field_name} for scenario (Seed {seed})"
    return f"Deterministic {field_name} {seed}-{record_index}"
