"""What the associate already told us, and the rule for when to ask again.

An associate opening with "I need to return the damaged red pump from order
CW273354" has just stated the return reason, the condition, the colour, the
product and the order. Discovery used all of it to *search* and remembered none
of it as a *fact*: the only facts a case ever received were written at
confirmation, from the confirmation itself. Everything said before that survived
only as prose in the transcript, so the reason for the return -- the one thing
the associate volunteered unprompted -- was asked for again later.

This module is where a stated fact is kept between being said and there being a
case to keep it on. Cases are created at confirmation and not before (contract
C1), so first-turn facts have nowhere durable to live for the first several
turns; they ride the conversation, and `RepositoryCaseStore.confirm_case` writes
them into the case fact log the moment one exists -- with the provenance they
were captured under, not the provenance of the confirmation that flushed them.

**The re-ask rule.** A known fact is not asked for again. The exceptions are
exactly the five the specification names, and each is a distinguishable state
rather than a judgement call:

* `CONFLICTING` -- the associate has said two different things and we cannot
  pick for them.
* `INVALID` -- the value failed the configured validation for that field, so it
  cannot be used whatever we do with it.
* `AMBIGUOUS` -- the model captured it and said it was unsure.
* `STALE` -- older than the field's configured `answer_ttl_seconds`. Almost
  nothing sets one; a return reason does not perish.
* `CONFIRMATION_REQUIRED` -- configured as needing read-back before it is acted
  on, however clearly it was stated.

Anything else is `USABLE` and must not be raised again. That is the whole of
adversarial scenario #36.

**What this is not.** It is not identification. A candidate matched on a
misspelled name is evidence and stays evidence -- `customer_name_fuzzy`, scored
below every confirmed signal, resolved only by the associate confirming a
candidate. Nothing here promotes a search result to a fact; only what someone
actually stated, or what the confirmation itself establishes, becomes one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class FactStatus(StrEnum):
    """Why a fact does or does not still need asking about."""

    USABLE = "USABLE"
    CONFLICTING = "CONFLICTING"
    INVALID = "INVALID"
    AMBIGUOUS = "AMBIGUOUS"
    STALE = "STALE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


#: The statuses whose value a later statement replaces instead of contradicting.
#:
#: Both of these are recorded precisely because the value was *not* an answer:
#: `INVALID` failed its own validation, and `AMBIGUOUS` matched more than one
#: thing. Weighing either against the value that later settled the question
#: would read a refinement as a contradiction -- and the agent narrows by
#: refinement, so it is the normal path rather than an edge case. An associate
#: who says "Alvara", is shown five Alvarados and picks ANTONIO ALVARADO has
#: answered once, not twice; carrying the search term forward as a rival left
#: the name `CONFLICTING`, which is `REASKABLE`, so the agent asked for it again
#: immediately after it had been confirmed.
#:
#: Two *resolved* values still conflict. Choosing between two things the
#: associate actually established is not a choice this code gets to make.
SUPERSEDABLE = frozenset({FactStatus.INVALID, FactStatus.AMBIGUOUS})


#: The statuses that make a fact worth raising with the associate again.
#: Everything else has been answered and must be left alone.
REASKABLE = frozenset(
    {
        FactStatus.CONFLICTING,
        FactStatus.INVALID,
        FactStatus.AMBIGUOUS,
        FactStatus.STALE,
        FactStatus.CONFIRMATION_REQUIRED,
    }
)


@dataclass(frozen=True, slots=True)
class FactDefinition:
    """One thing the conversation can establish, from the clarification policy.

    Read from `clarification_policy.fields` rather than from a list of fact
    names written here. That configuration already names everything the agent
    may ask an associate for -- return reason, condition, colour, ZIP, PO
    number -- with labels and priorities, and inventing a parallel list would
    put the same catalogue in two places and let them disagree.
    """

    name: str
    label: str
    priority: int
    field_group: str
    answer_ttl_seconds: int | None
    confirmation_required: bool
    validation: re.Pattern[str] | None

    def status_for(
        self, values: Sequence[Any], *, ambiguous: bool, observed_at: datetime, as_of: datetime
    ) -> FactStatus:
        distinct = {str(value).strip().casefold() for value in values if value is not None}
        if len(distinct) > 1:
            return FactStatus.CONFLICTING
        if not distinct:
            return FactStatus.INVALID
        only = next(iter(values))
        if self.validation is not None and self.validation.search(str(only)) is None:
            return FactStatus.INVALID
        if ambiguous:
            return FactStatus.AMBIGUOUS
        if self.answer_ttl_seconds is not None:
            if as_of - observed_at > timedelta(seconds=self.answer_ttl_seconds):
                return FactStatus.STALE
        if self.confirmation_required:
            return FactStatus.CONFIRMATION_REQUIRED
        return FactStatus.USABLE


@dataclass(frozen=True, slots=True)
class CapturedFact:
    """One stated fact, with the provenance it was stated under.

    Provenance is captured at the moment of statement and carried unchanged to
    the case. Stamping these with the confirmation turn's identity would make
    every first-turn fact look as though it had been established at
    confirmation, which is exactly the record that would stop anyone noticing
    the reason had been re-asked in between.
    """

    name: str
    value: Any
    status: str
    turn_id: str
    source_message_id: str | None
    acquisition: str
    observed_at: str
    label: str = ""

    @property
    def needs_asking_again(self) -> bool:
        return FactStatus(self.status) in REASKABLE

    def to_state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "status": self.status,
            "turnId": self.turn_id,
            "sourceMessageId": self.source_message_id,
            "acquisition": self.acquisition,
            "observedAt": self.observed_at,
            "label": self.label,
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> CapturedFact | None:
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return None
        return cls(
            name=name,
            value=payload.get("value"),
            status=str(payload.get("status", FactStatus.USABLE.value)),
            turn_id=str(payload.get("turnId", "")),
            source_message_id=payload.get("sourceMessageId"),
            acquisition=str(payload.get("acquisition", "STATED")),
            observed_at=str(payload.get("observedAt", "")),
            label=str(payload.get("label", "")),
        )

    def describe(self) -> dict[str, Any]:
        """What the model is shown, so it can see the fact is already known."""
        described: dict[str, Any] = {
            "fact": self.name,
            "value": self.value,
            "status": self.status,
        }
        if self.label:
            described["label"] = self.label
        if self.needs_asking_again:
            described["askAgainBecause"] = self.status
        return described


@dataclass(frozen=True, slots=True)
class FactCatalogue:
    """Every fact the conversation may establish, and the re-ask rule over them."""

    definitions: tuple[FactDefinition, ...] = ()

    def definition_for(self, name: str) -> FactDefinition | None:
        for item in self.definitions:
            if item.name == name:
                return item
        return None

    def capture(
        self,
        existing: Sequence[CapturedFact],
        observed: Sequence[Any],
        *,
        turn_id: str,
        as_of: datetime,
    ) -> tuple[tuple[CapturedFact, ...], tuple[str, ...]]:
        """Merge what this turn stated into what the conversation already knew.

        Returns the merged facts and the names the model offered that no
        configured field claims. Unrecognized names are reported rather than
        stored: a fact nobody configured has no label, no validation and no
        re-ask rule, so keeping it would put an unowned value into a case's
        permanent record.

        A repeated statement of the same value is not a conflict -- associates
        restate things. A *different* value is, and it is kept as a conflict
        rather than silently overwritten, because choosing between two things
        the associate said is not a choice this code gets to make.

        The exception is a previous value that was never an answer: see
        `SUPERSEDABLE`. Those are replaced by whatever settled them.
        """
        merged: dict[str, CapturedFact] = {fact.name: fact for fact in existing}
        unknown: list[str] = []
        for statement in observed:
            name = getattr(statement, "fact", None)
            if not isinstance(name, str) or not name:
                continue
            definition = self.definition_for(name)
            if definition is None:
                unknown.append(name)
                continue
            value = getattr(statement, "value", None)
            ambiguous = bool(getattr(statement, "ambiguous", False))
            previous = merged.get(name)
            values: list[Any] = [value]
            if previous is not None and FactStatus(previous.status) not in SUPERSEDABLE:
                values.insert(0, previous.value)
            status = definition.status_for(
                values, ambiguous=ambiguous, observed_at=as_of, as_of=as_of
            )
            merged[name] = CapturedFact(
                name=name,
                value=value,
                status=status.value,
                turn_id=turn_id,
                source_message_id=getattr(statement, "source_message_id", None),
                acquisition=str(getattr(statement, "acquisition", "STATED")),
                observed_at=as_of.isoformat(),
                label=definition.label,
            )
        return tuple(merged.values()), tuple(dict.fromkeys(unknown))

    def refresh(
        self, facts: Sequence[CapturedFact], *, as_of: datetime
    ) -> tuple[CapturedFact, ...]:
        """Re-evaluate staleness against this turn's as-of.

        Status is otherwise decided when a fact is captured. Staleness is the
        one condition that arrives by the passage of time rather than by
        anything anyone said, so it has to be recomputed rather than stored.
        """
        refreshed: list[CapturedFact] = []
        for fact in facts:
            definition = self.definition_for(fact.name)
            if definition is None or definition.answer_ttl_seconds is None:
                refreshed.append(fact)
                continue
            if FactStatus(fact.status) is not FactStatus.USABLE:
                refreshed.append(fact)
                continue
            try:
                observed_at = datetime.fromisoformat(fact.observed_at)
            except ValueError:
                refreshed.append(fact)
                continue
            if as_of - observed_at > timedelta(seconds=definition.answer_ttl_seconds):
                refreshed.append(
                    CapturedFact(
                        name=fact.name,
                        value=fact.value,
                        status=FactStatus.STALE.value,
                        turn_id=fact.turn_id,
                        source_message_id=fact.source_message_id,
                        acquisition=fact.acquisition,
                        observed_at=fact.observed_at,
                        label=fact.label,
                    )
                )
            else:
                refreshed.append(fact)
        return tuple(refreshed)


def build_fact_catalogue(configured_fields: Any) -> FactCatalogue:
    """Bind `clarification_policy.fields` into the conversation's fact catalogue.

    Takes the sequence rather than the configuration model so this package does
    not import the returns configuration -- `runtime_factory` owns that
    translation, exactly as it does for the identification catalogue.
    """
    definitions = tuple(
        FactDefinition(
            name=item.field,
            label=item.label,
            priority=item.priority,
            field_group=item.field_group,
            answer_ttl_seconds=item.answer_ttl_seconds,
            confirmation_required=item.confirmation_required,
            validation=re.compile(item.validation_pattern) if item.validation_pattern else None,
        )
        for item in configured_fields
    )
    return FactCatalogue(definitions=definitions)
