from datetime import UTC, datetime
from typing import Any

from .models import GenerationProvenance


def build_provenance(
    generator_version: str, metrics: dict[str, Any], ai_traces: list[Any]
) -> GenerationProvenance:
    return GenerationProvenance(
        timestamp=datetime.now(UTC),
        generator_version=generator_version,
        metrics=metrics,
        ai_traces=ai_traces,
    )
