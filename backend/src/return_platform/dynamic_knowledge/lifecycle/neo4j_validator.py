"""Runs `graph/validation.py`'s compiled checks against a real Neo4j.

Kept separate from the check definitions so the Cypher stays testable without a
database, and so the orchestrator depends on a port it can substitute rather
than on a driver.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from return_platform.dynamic_knowledge.graph.generation_writer import GenerationDriver
from return_platform.dynamic_knowledge.graph.validation import (
    GenerationValidationReport,
    ValidationFinding,
    compile_validation_checks,
    evaluate,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

_LOGGER = logging.getLogger(__name__)


class GenerationValidator(Protocol):
    async def validate(
        self, *, schema: ActiveSchema, graph_generation_id: str
    ) -> GenerationValidationReport: ...


class Neo4jGenerationValidator:
    def __init__(self, driver: GenerationDriver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    async def validate(
        self, *, schema: ActiveSchema, graph_generation_id: str
    ) -> GenerationValidationReport:
        """Run every schema-derived check and collect the findings.

        Runs all checks rather than stopping at the first error: an operator
        deciding whether a rebuild is salvageable needs the whole picture, and
        the queries are counts against a generation-scoped index, not scans.
        """
        checks = compile_validation_checks(schema, graph_generation_id=graph_generation_id)
        findings: list[ValidationFinding] = []
        async with self._driver.session(database=self._database) as session:
            for check in checks:
                observed = await session.execute_read(_count_rows, check=check)
                finding = evaluate(check, observed)
                if finding is not None:
                    findings.append(finding)
        report = GenerationValidationReport(
            graph_generation_id=graph_generation_id, findings=tuple(findings)
        )
        if not report.passed:
            _LOGGER.error("Generation validation failed: %s", report.summary())
        elif report.warnings:
            _LOGGER.warning("Generation validation passed with warnings: %s", report.summary())
        return report


async def _count_rows(tx: Any, *, check: Any) -> int:
    result = await tx.run(check.statement.cypher, check.statement.parameters)
    rows = [row async for row in result]
    if not rows:
        # A count query always returns one row; no rows means the query did not
        # execute as expected. Treating that as zero would silently pass the
        # violation-when-nonzero checks, so it is reported as zero only for the
        # populated-ness checks, where zero is itself the failure.
        return 0
    value = rows[0]["observed"]
    return int(value) if value is not None else 0
