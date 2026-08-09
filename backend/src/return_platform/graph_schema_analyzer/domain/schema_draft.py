"""The editable graph schema and its lifecycle (design doc section 10.4).

    DRAFT --mutate--> DRAFT (new revision)
    DRAFT --validate--> VALIDATED --approve--> APPROVED --build--> lifecycle
       ^                   |
       +---- mutate -------+   (any mutation invalidates VALIDATED)

**Any mutation invalidates VALIDATED**, and that is enforced here rather than
left to callers. A validation result is a statement about a specific schema
shape; the moment the shape changes the statement is stale, and an APPROVED
draft built from a stale validation is the failure this whole state machine
exists to prevent.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from return_platform.graph_schema_analyzer.domain.errors import InvalidSessionTransition

__all__ = ["DraftStatus", "GraphSchemaDraft", "GraphSchemaShape"]


class DraftStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"


class GraphSchemaShape(BaseModel):
    """The schema itself: labels, their properties, and relationships.

    Deliberately plain data. Every meaningful change goes through a typed
    mutation command, so this model needs no mutation methods of its own -- it is
    the *result* of applying commands, never an editing surface.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entities: Mapping[str, Mapping[str, Any]] = {}
    relationships: tuple[Mapping[str, Any], ...] = ()
    graph_indexes: tuple[Mapping[str, Any], ...] = ()
    graph_constraints: tuple[Mapping[str, Any], ...] = ()

    def is_empty(self) -> bool:
        return not self.entities and not self.relationships


class GraphSchemaDraft(BaseModel):
    """One editable schema for one analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str
    analysis_id: str
    status: DraftStatus = DraftStatus.DRAFT
    shape: GraphSchemaShape = GraphSchemaShape()
    current_revision: int = 0
    version: int = 0
    created_at: datetime
    updated_at: datetime
    # Set when status is VALIDATED, cleared by any mutation. Its presence is what
    # `approve()` checks, so a draft cannot be approved on a validation that a
    # later edit invalidated.
    validation_result_id: str | None = None

    def mutated(self, shape: GraphSchemaShape, *, occurred_at: datetime) -> GraphSchemaDraft:
        """Apply a new shape as the next revision.

        Always returns to DRAFT and clears `validation_result_id`, including when
        the draft was already APPROVED: editing an approved schema is legitimate
        (it simply has to be re-approved), and silently keeping the approval
        would let a build run against something no human signed off on.
        """
        return self.model_copy(
            update={
                "shape": shape,
                "status": DraftStatus.DRAFT,
                "validation_result_id": None,
                "current_revision": self.current_revision + 1,
                "version": self.version + 1,
                "updated_at": occurred_at,
            }
        )

    def validated(self, validation_result_id: str, *, occurred_at: datetime) -> GraphSchemaDraft:
        if self.status is DraftStatus.APPROVED:
            raise InvalidSessionTransition(
                f"draft {self.draft_id} is APPROVED; mutate it first if it needs revalidating."
            )
        if self.shape.is_empty():
            raise InvalidSessionTransition(
                f"draft {self.draft_id} has no entities or relationships to validate."
            )
        return self.model_copy(
            update={
                "status": DraftStatus.VALIDATED,
                "validation_result_id": validation_result_id,
                "version": self.version + 1,
                "updated_at": occurred_at,
            }
        )

    def approved(self, *, occurred_at: datetime) -> GraphSchemaDraft:
        """Only a VALIDATED draft can be approved, and only on the validation
        result still attached to its current shape."""
        if self.status is not DraftStatus.VALIDATED:
            raise InvalidSessionTransition(
                f"draft {self.draft_id} is {self.status}; only a VALIDATED draft can be approved."
            )
        if self.validation_result_id is None:
            raise InvalidSessionTransition(
                f"draft {self.draft_id} is VALIDATED but carries no validation result; "
                "it must be re-validated before approval."
            )
        return self.model_copy(
            update={
                "status": DraftStatus.APPROVED,
                "version": self.version + 1,
                "updated_at": occurred_at,
            }
        )

    def to_document(self) -> Mapping[str, Any]:
        return self.model_dump(mode="json")
