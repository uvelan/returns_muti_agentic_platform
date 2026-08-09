"""Budgets that turn "the model cannot get there" into a human decision.

The failure mode this exists to prevent is not an exception -- it is a graph that
keeps clarifying, proposing, and re-validating forever, burning tokens against a
source shape the model will never resolve. Every loop in `routing.py` is bounded
by one of these, and exhausting a budget routes to `NEEDS_HUMAN_REVIEW` with a
reason. It never raises: an analysis that needs a human is a normal outcome of
schema analysis, not an error.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AnalyzerBudgets", "BudgetVerdict"]


class AnalyzerBudgets(BaseModel):
    """Per-analysis ceilings. Defaults are deliberately small: a schema the model
    cannot propose in a couple of attempts is one a human should look at."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_clarifications: int = Field(default=3, ge=0, le=10)
    max_validation_attempts: int = Field(default=3, ge=1, le=10)


class BudgetVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    exhausted: bool
    reason: str | None = None

    @classmethod
    def ok(cls) -> BudgetVerdict:
        return cls(exhausted=False)

    @classmethod
    def stop(cls, reason: str) -> BudgetVerdict:
        return cls(exhausted=True, reason=reason)


def check_clarification_budget(budgets: AnalyzerBudgets, clarification_count: int) -> BudgetVerdict:
    if clarification_count >= budgets.max_clarifications:
        return BudgetVerdict.stop(
            f"Asked {clarification_count} clarifying question(s) without reaching sufficient "
            f"context (limit {budgets.max_clarifications}). A human should review the sources."
        )
    return BudgetVerdict.ok()


def check_validation_budget(budgets: AnalyzerBudgets, validation_attempt: int) -> BudgetVerdict:
    if validation_attempt >= budgets.max_validation_attempts:
        return BudgetVerdict.stop(
            f"Proposal failed validation {validation_attempt} time(s) "
            f"(limit {budgets.max_validation_attempts}). A human should review the findings."
        )
    return BudgetVerdict.ok()
