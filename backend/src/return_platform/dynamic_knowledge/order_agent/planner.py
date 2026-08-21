"""Which signal is worth asking for next, given what is already known.

Order Discovery's sequencing was a fixed program. Every populated signal became a
plan in catalogue order, and the choice of what to ask for next came from a
static `clarification_priority` -- an order decided once, by an operator, for
every conversation. That is a questionnaire: it asks for the ZIP code ninth
whether or not the ZIP code would tell the two remaining candidates apart, and it
asks for a customer name at the same rank whether the search just returned four
hundred candidates or none.

The engine the audit asks for runs the other way round:

    current facts -> enabled searchable fields -> selectivity -> evidence
    strength -> next best discriminator -> execute -> observe result count ->
    re-evaluate

This module is that ranking step, and only that step. It adds no configuration
surface: everything it reads already exists. `clarification_priority`, aliases
and labels come from the identification catalogue (DISC-01); `distinct_ratio`
and `identifier_likelihood` come from the analyzer's `FieldSelectivity` on the
active schema; the candidate set and result count come from the turn that just
ran.

**Two things it deliberately does not do.**

It does not choose. `clarification_policy.field_selection_owner` is `LLM`, and
that stays true: this produces a ranked, reasoned list that travels in the turn
context, and the model picks. Ranking is evidence; choosing is judgement.

It does not treat missing selectivity as low selectivity. A field nothing has
profiled reports `UNKNOWN`, and `FieldSelectivity`'s own docstring is explicit
that a consumer treating that as `UNLIKELY` would be ranking on the absence of
evidence. Unmeasured fields fall back to the configured priority and say so in
`basis`, so a reader can tell a measurement from a default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from return_platform.dynamic_knowledge.order_agent.identification import (
    IdentificationCatalogue,
    IdentificationField,
    ParsedIntent,
    normalize_value,
)


class DiscriminatorBasis(StrEnum):
    """What the ranking of one field actually rests on.

    Carried into the model's context because the two are not equally strong and
    a reader that cannot tell them apart will over-trust the weaker one.
    """

    #: The analyzer profiled this property and counted distinct values.
    MEASURED = "MEASURED_SELECTIVITY"
    #: The source declares the property unique. Stronger than any measurement:
    #: it is a statement about every row, not about a sample.
    DECLARED_UNIQUE = "DECLARED_UNIQUE"
    #: Nothing profiled it, so the operator's configured question order is the
    #: best available evidence. Not a measurement, and labelled so.
    CONFIGURED = "CONFIGURED_PRIORITY"


@dataclass(frozen=True, slots=True)
class Discriminator:
    """One field worth asking for, with the reasoning that put it there."""

    intent_key: str
    label: str
    aliases: tuple[str, ...]
    score: float
    basis: str
    reason: str
    #: How many distinct values the current candidates hold for this field.
    #: `None` when no candidate carries it -- unknown, not zero, and the two
    #: rank differently.
    splits_candidates: int | None

    def describe(self) -> dict[str, Any]:
        described: dict[str, Any] = {
            "intentKey": self.intent_key,
            "label": self.label,
            "score": round(self.score, 4),
            "basis": self.basis,
            "reason": self.reason,
        }
        if self.aliases:
            described["aliases"] = list(self.aliases)
        if self.splits_candidates is not None:
            described["distinctValuesAmongCandidates"] = self.splits_candidates
        return described


def _selectivity_of(field: IdentificationField) -> tuple[float, str]:
    """How well this field narrows, and on what authority.

    The best of its searches wins: a field searched against two properties is
    as selective as the most selective one, because a match on that property is
    what the answer would produce.
    """
    best_ratio: float | None = None
    declared = False
    for search in field.searches:
        if search.identifier_likelihood == "DECLARED_UNIQUE":
            declared = True
        if search.distinct_ratio is not None:
            best_ratio = (
                search.distinct_ratio
                if best_ratio is None
                else max(best_ratio, search.distinct_ratio)
            )
    if declared:
        return 1.0, DiscriminatorBasis.DECLARED_UNIQUE.value
    if best_ratio is not None:
        return max(0.0, min(1.0, best_ratio)), DiscriminatorBasis.MEASURED.value
    return -1.0, DiscriminatorBasis.CONFIGURED.value


#: The most an unprofiled field can score on the configured question order.
#:
#: The two scales are not the same kind of statement, and this is the whole of
#: how they are reconciled. Below the cap, a configured priority beats a weak
#: measurement -- an unprofiled order number should outrank an order status
#: profiled at 0.02 distinct, because the measurement says the status barely
#: narrows. At the cap, a near-unique measurement wins, because "4,900 distinct
#: values in 5,000 sampled rows" is stronger evidence about narrowing power than
#: any position in a hand-written list.
#:
#: The alternative -- letting the top configured field reach 1.0 -- makes
#: profiling the schema unable to change the question order, which would leave
#: this a questionnaire with extra arithmetic.
_CONFIGURED_SCORE_CEILING = 0.95


def _configured_score(field: IdentificationField, highest_priority: int) -> float:
    if highest_priority <= 0:
        return 0.0
    ratio = field.clarification_priority / highest_priority
    return max(0.0, min(_CONFIGURED_SCORE_CEILING, ratio * _CONFIGURED_SCORE_CEILING))


#: The most a field the candidates say nothing about may score while narrowing.
#:
#: It exists because the previous arithmetic had the ranking upside down. A
#: narrowing field was scaled by `score * (distinct / len) + score * 0.25`, which
#: is **below 1 for anything short of a perfect split**, while a field no
#: candidate carried kept its configured score untouched. Measured live: against
#: four candidates sharing a state and differing in city, `cities` -- the only
#: field that split them at all -- scored 0.338 and ranked twelfth, behind eleven
#: fields carrying no information about those candidates whatsoever, led by the
#: order number at 0.95. The associate was asked for paperwork they did not have
#: while the question that would have resolved it sat at the bottom.
_UNKNOWN_CEILING = 0.5


def _narrowing_score(configured: float, *, distinct: int, candidates: int) -> float:
    """Where a field that demonstrably splits the candidates ranks.

    Always above `_UNKNOWN_CEILING`, so a measured splitter beats every field the
    candidates are silent about. Within that band, ordered mostly by how finely
    it cuts -- a field that separates every candidate is the ideal question -- and
    partly by the operator's configured priority, which breaks ties between cuts
    of equal sharpness and keeps the release's opinion meaningful.
    """
    share = (distinct - 1) / max(1, candidates - 1)
    headroom = 1.0 - _UNKNOWN_CEILING
    return min(1.0, _UNKNOWN_CEILING + headroom * (0.6 * min(1.0, share) + 0.4 * configured))


def _distinct_values_among(
    field: IdentificationField, candidates: Sequence[dict[str, Any]]
) -> int | None:
    """How many different answers the current candidates would give for this field.

    This is the anti-questionnaire mechanism and the whole reason the ranking
    cannot be precomputed. A field every remaining candidate agrees on splits
    nothing: asking for it spends the associate's turn and removes no candidate,
    however selective the field is across the table as a whole. A field on which
    they all differ resolves the ambiguity in one question.

    `None` when no candidate carries the field at all -- that is unknown rather
    than useless, and it must not be ranked as though it had been measured and
    found flat.
    """
    values: set[str] = set()
    seen = False
    for candidate in candidates:
        data = candidate.get("data") if isinstance(candidate, dict) else None
        if not isinstance(data, dict):
            continue
        for search in field.searches:
            if search.field_id not in data:
                continue
            raw = data.get(search.field_id)
            if raw is None:
                continue
            seen = True
            values.add(normalize_value(raw, field.normalization))
    if not seen:
        return None
    return len(values)


def rank_discriminators(
    catalogue: IdentificationCatalogue,
    parsed: ParsedIntent,
    *,
    candidates: Sequence[dict[str, Any]] = (),
    result_count: int | None = None,
    limit: int = 5,
) -> tuple[Discriminator, ...]:
    """The signals worth asking for next, best first.

    `result_count` closes the loop the audit describes. It is not decoration:

    * Zero results means the signals gathered so far did not find anything, so
      the next question should be an *alternative anchor* -- something that can
      identify the order on its own -- rather than another way to narrow a set
      that is already empty. Selectivity is exactly the right ranking for that.
    * Several results means the search worked and the job is now to tell them
      apart, so a field the candidates actually disagree on beats a more
      selective field they all share.

    Fields the associate has already answered are excluded, unless what they
    gave was rejected as invalid -- in which case the value is not usable and
    asking again is the only way forward, which is a different thing from
    re-asking a question that was already answered well.
    """
    answered = {
        signal.field.intent_key
        for signal in parsed.signals
        if signal.values and not signal.rejected
    }
    invalid = set(parsed.invalid_signals)
    highest_priority = max(
        (item.clarification_priority for item in catalogue.fields if item.is_usable), default=0
    )
    narrowing = bool(candidates) and (result_count or 0) > 1

    ranked: list[Discriminator] = []
    for field in catalogue.fields:
        if not field.is_usable:
            # A signal nothing can search is a signal not worth asking for. The
            # associate is told it could not be used; they are not asked for it.
            continue
        if field.intent_key in answered and field.intent_key not in invalid:
            # Answered, normally. But "answered" here means the associate said
            # SOMETHING for this field, not that what they said settles it -- and
            # a field the remaining candidates still DISAGREE on has not been
            # settled by it. "find order for BOYLE" against six customers who
            # share a surname and differ by first name was excluded as answered,
            # so the ranking could never propose asking for a fuller name and
            # offered an email address instead. The associate gave a prefix; the
            # question that finishes it is the best one available.
            #
            # Only while narrowing, and only on real disagreement: with one
            # candidate, or with every candidate carrying the same value, the
            # field genuinely is answered and re-asking is the re-asking this
            # catalogue exists to prevent.
            settled = _distinct_values_among(field, candidates)
            if not narrowing or settled is None or settled <= 1:
                continue

        measured, basis = _selectivity_of(field)
        configured = _configured_score(field, highest_priority)
        score = configured if measured < 0.0 else measured
        reason_parts: list[str] = []
        if basis == DiscriminatorBasis.DECLARED_UNIQUE.value:
            reason_parts.append(
                "the source declares this unique, so one answer identifies one record"
            )
        elif basis == DiscriminatorBasis.MEASURED.value:
            reason_parts.append(f"profiled distinctness {score:.2f}")
        else:
            reason_parts.append("not profiled; ranked by the configured question order")

        distinct = _distinct_values_among(field, candidates)
        if narrowing:
            if distinct is None:
                # The candidates say nothing about this field. Unknown rather
                # than useless -- so it keeps its configured standing but is held
                # below anything measured, because "might narrow" must never
                # outrank "demonstrably does".
                score = min(score, _UNKNOWN_CEILING)
                reason_parts.append("no candidate carries this field")
            elif distinct <= 1:
                # Every remaining candidate agrees. Asking removes nothing.
                score = 0.0
                reason_parts.append("every remaining candidate has the same value")
            else:
                score = _narrowing_score(score, distinct=distinct, candidates=len(candidates))
                reason_parts.append(f"splits the {len(candidates)} candidates into {distinct}")
        elif result_count == 0:
            reason_parts.append("nothing matched so far, so an independent anchor is worth more")

        if field.intent_key in invalid:
            reason_parts.append("the value given could not be validated")
        elif field.intent_key in answered:
            reason_parts.append(
                "the associate already gave part of this, and the candidates still differ on it, "
                "so what they gave was a partial value rather than an answer"
            )

        ranked.append(
            Discriminator(
                intent_key=field.intent_key,
                label=field.label,
                aliases=field.aliases,
                score=score,
                basis=basis,
                reason="; ".join(reason_parts),
                splits_candidates=distinct,
            )
        )

    ranked.sort(
        key=lambda item: (-item.score, -_priority_of(catalogue, item.intent_key), item.intent_key)
    )
    return tuple(ranked[:limit])


def _priority_of(catalogue: IdentificationCatalogue, intent_key: str) -> int:
    field = catalogue.field_for(intent_key)
    return field.clarification_priority if field is not None else 0


def order_searches_by_discrimination(
    planned: Sequence[Any], catalogue: IdentificationCatalogue
) -> tuple[Any, ...]:
    """Run the most discriminating searches first.

    Only observable when the per-turn query budget truncates the plan set -- and
    that is exactly when it matters. An associate who gave six signals could
    previously have the order number pass dropped because it happened to sit
    behind five broad address passes in catalogue order, and see "no results"
    for a search that never asked the one question that would have answered it.

    A stable sort, so searches of equal standing keep the order the catalogue
    put them in and a turn stays reproducible.
    """
    ordering = {
        field.intent_key: _selectivity_of(field)[0]
        if _selectivity_of(field)[0] >= 0.0
        else field.clarification_priority / 10_000
        for field in catalogue.fields
    }
    return tuple(sorted(planned, key=lambda item: -ordering.get(item.intent_key, 0.0)))
