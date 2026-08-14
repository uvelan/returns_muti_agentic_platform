"""The runtime-owned catalogue of things an associate can be identified by.

Order Discovery used to know its identification fields by heart. Seventeen names
were written into `OrderSearchIntent`, the same seventeen into a signature tuple,
a numbered branch per field into the plan builder, a hardcoded pair list for
addresses, a constant for the date field, another for unsupported signals, and a
scoring block per field in the ranker. Adding the eighteenth meant editing all
seven places and shipping a release -- while a rich `discovery:` block in
`config/returns/production.yaml` described fields in detail and was read only by
the frozen `associate_flow`. The configuration existed and was bound to the wrong
implementation.

This module is the binding. It takes the operator's catalogue and the active
graph schema and produces, per field: which of its configured searches this
schema can actually answer, what a match is worth, how a value is normalized and
validated, and what the reasoning model should be told the field is called.
Nothing downstream names a field.

Three things are deliberately *not* resolved here:

* An unusable field is not an error. A signal the schema cannot answer is
  reported to the turn (`unusable_signals`) so the associate learns that the
  colour they gave could not be used, rather than reading an empty result as
  "no such order".
* Ranking weights are configuration, not judgement. This module reports the
  configured weight; `rank_search_results` applies it.
* Nothing here asks a question. Clarification metadata is carried for the
  policy that does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import Any

from return_platform.dynamic_knowledge.schema import ActiveSchema, IdentifierLikelihood

#: Strategies that are ordinary schema operators, mapped straight onto a
#: `QueryCondition.operator`. `FULLTEXT` is the exception and is handled as its
#: own plan shape (see `search_strategy.build_progressive_plans`).
FULLTEXT_STRATEGY = "FULLTEXT"

#: Value types whose signal is one bound of a date window rather than a value to
#: match. Kept as a set rather than as three `if` branches so the planner can
#: ask "is this a date bound" without knowing which field it is looking at.
DATE_VALUE_TYPES = frozenset({"DATE_LOWER_BOUND", "DATE_UPPER_BOUND", "DATE_POINT"})


class ValueForm(StrEnum):
    AS_TYPED = "AS_TYPED"
    DIGITS = "DIGITS"
    LOWERCASE = "LOWERCASE"


class Normalization(StrEnum):
    NONE = "NONE"
    TRIM = "TRIM"
    LOWER_ALPHANUMERIC = "LOWER_ALPHANUMERIC"
    DIGITS = "DIGITS"


def apply_value_form(value: Any, form: str) -> Any:
    """Reshape a value the way one particular search wants to ask for it.

    Applied per search, not per field: the same phone number is asked for as
    typed against one property and as bare digits against another, because the
    two are stored differently and neither form finds both.
    """
    if not isinstance(value, str):
        return value
    if form == ValueForm.DIGITS:
        return re.sub(r"[^0-9]+", "", value)
    if form == ValueForm.LOWERCASE:
        return value.lower()
    return value


def normalize_value(value: Any, normalization: str) -> str:
    """The comparable form of a value, for ranking rather than for querying."""
    text = str(value)
    if normalization == Normalization.DIGITS:
        return re.sub(r"[^0-9]+", "", text)
    if normalization == Normalization.LOWER_ALPHANUMERIC:
        return re.sub(r"[^a-z0-9]+", "", text.lower())
    if normalization == Normalization.TRIM:
        return text.strip()
    return text


@dataclass(frozen=True, slots=True)
class ResolvedSearch:
    """One configured graph read that the active schema can actually answer."""

    entity_id: str
    field_id: str
    strategy: str
    limit: int
    result_fields: tuple[str, ...]
    value_form: str
    applies_when: re.Pattern[str] | None
    narrow_with: str | None
    fulltext_index: str | None
    only_when_nothing_found: bool = False
    match_label: str = ""
    deferred_score_ceiling: float = 0.6
    #: What the analyzer measured about how well this property narrows, carried
    #: from the schema at resolve time. `None` means nothing profiled it -- which
    #: is not the same as "profiled and found useless", and the planner is
    #: required to tell those apart rather than rank on the absence of evidence.
    distinct_ratio: float | None = None
    identifier_likelihood: str = IdentifierLikelihood.UNKNOWN.value

    @property
    def label(self) -> str:
        return self.match_label or f"{self.field_id}_{self.strategy.lower()}"

    def accepts(self, value: Any) -> bool:
        """Whether this search is the one to issue for this particular value.

        The mechanism behind "a complete email matches exactly, a fragment can
        only be matched loosely" without either case being a branch in Python.
        """
        if self.applies_when is None:
            return True
        return self.applies_when.search(str(value)) is not None


@dataclass(frozen=True, slots=True)
class UnusableSearch:
    """A configured search this schema cannot answer, and why.

    Kept rather than discarded because "the operator configured colour and this
    graph has no colour property" and "the operator configured a typo" look
    identical from an empty result, and only one of them is a mistake.
    """

    entity_id: str
    field_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class IdentificationField:
    """One catalogue entry, bound to the schema that has to answer it."""

    field_id: str
    intent_key: str
    label: str
    description: str
    aliases: tuple[str, ...]
    value_type: str
    multiple: bool
    normalization: str
    validation: re.Pattern[str] | None
    sensitivity: str
    ranking_weight: float
    exact_match_bonus: float
    clarification_priority: int
    searches: tuple[ResolvedSearch, ...]
    unusable: tuple[UnusableSearch, ...] = ()

    @property
    def is_date_bound(self) -> bool:
        return self.value_type in DATE_VALUE_TYPES

    @property
    def is_usable(self) -> bool:
        """Whether anything can actually be searched for this signal."""
        return bool(self.searches)

    def values_from(self, raw: Any) -> tuple[list[Any], list[Any]]:
        """Split what the model supplied into usable values and rejected ones.

        Rejection is by the configured `validation_pattern` only. A field with
        no pattern rejects nothing, which is the right default: a validation
        rule nobody wrote must not become a filter nobody can see.
        """
        if raw is None:
            return [], []
        candidates: list[Any] = []
        if isinstance(raw, (list, tuple)):
            candidates = [item for item in raw if item is not None and item != ""]
        elif raw != "":
            candidates = [raw]
        if not self.multiple:
            candidates = candidates[:1]
        accepted: list[Any] = []
        rejected: list[Any] = []
        for candidate in candidates:
            if self.validation is not None and self.validation.search(str(candidate)) is None:
                rejected.append(candidate)
            else:
                accepted.append(candidate)
        # Deduplicated by what was typed. Two searches asking the graph the same
        # question is one wasted query against the per-turn budget.
        return list(dict.fromkeys(accepted)), rejected

    def describe(self) -> dict[str, Any]:
        """What the reasoning model is told about this field.

        The point of this method: an operator adding a field to configuration
        makes it appear in the model's context on the next turn. Nobody edits a
        prompt, and nobody adds a name to a list in Python.
        """
        described: dict[str, Any] = {
            "intentKey": self.intent_key,
            "label": self.label,
            "multiple": self.multiple,
            "valueType": self.value_type,
            "searchable": self.is_usable,
        }
        if self.description:
            described["description"] = self.description
        if self.aliases:
            described["aliases"] = list(self.aliases)
        if self.clarification_priority:
            described["clarificationPriority"] = self.clarification_priority
        if not self.is_usable:
            # Stated, so the model does not spend a clarifying question asking
            # for something no search can use.
            described["unsearchableReason"] = (
                "no field in the active knowledge graph answers this signal"
            )
        return described


@dataclass(frozen=True, slots=True)
class SignalValues:
    """What one turn's intent actually said about one catalogue field."""

    field: IdentificationField
    values: tuple[Any, ...]
    rejected: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class ParsedIntent:
    """A search intent read through the catalogue rather than through a class.

    `unknown_keys` is what the model populated that no configured field claims.
    Reported rather than refused: the previous contract was `extra="forbid"`, so
    an answer to the agent's own clarifying question could be rejected outright
    by the request layer -- the associate saw a validation failure for having
    answered the question they were asked.
    """

    signals: tuple[SignalValues, ...] = ()
    unknown_keys: tuple[str, ...] = ()

    def by_key(self, intent_key: str) -> SignalValues | None:
        for signal in self.signals:
            if signal.field.intent_key == intent_key:
                return signal
        return None

    @property
    def searchable(self) -> tuple[SignalValues, ...]:
        return tuple(signal for signal in self.signals if signal.field.is_usable)

    @property
    def unusable_signals(self) -> tuple[str, ...]:
        """Populated signals no configured search can answer.

        What `unsupported_signals` used to read off a hand-maintained tuple, now
        derived from the catalogue and the live schema: colour is unusable
        because nothing in this graph records colour, and it stops being
        unusable the day a colour property exists and an operator points a
        search at it.
        """
        return tuple(
            signal.field.intent_key
            for signal in self.signals
            if signal.values and not signal.field.is_usable
        )

    @property
    def invalid_signals(self) -> tuple[str, ...]:
        return tuple(signal.field.intent_key for signal in self.signals if signal.rejected)


@dataclass(frozen=True, slots=True)
class IdentificationCatalogue:
    """Every configured identification field, resolved against one schema."""

    fields: tuple[IdentificationField, ...] = ()
    #: Fields whose configuration named an entity or property the active schema
    #: does not have. Logged once at construction; a deployment should not be
    #: discovering this per turn.
    unresolved: tuple[UnusableSearch, ...] = dataclass_field(default=())

    @property
    def intent_keys(self) -> tuple[str, ...]:
        """The identifying keys, in configured order.

        This is what the pagination signature is computed over. It moves when
        configuration moves, which is the point: a "show next" must not page
        through a cached result set gathered under a different set of signals.
        """
        return tuple(item.intent_key for item in self.fields)

    def field_for(self, intent_key: str) -> IdentificationField | None:
        for item in self.fields:
            if item.intent_key == intent_key:
                return item
        return None

    def parse(self, values: dict[str, Any]) -> ParsedIntent:
        """Read a turn's raw intent payload through the catalogue."""
        signals: list[SignalValues] = []
        for item in self.fields:
            accepted, rejected = item.values_from(values.get(item.intent_key))
            if accepted or rejected:
                signals.append(
                    SignalValues(field=item, values=tuple(accepted), rejected=tuple(rejected))
                )
        unknown = tuple(
            key
            for key in values
            if key not in {item.intent_key for item in self.fields} and values.get(key)
        )
        return ParsedIntent(signals=tuple(signals), unknown_keys=unknown)

    def describe(self) -> list[dict[str, Any]]:
        """The catalogue as the reasoning model sees it, most wanted first."""
        return [
            item.describe()
            for item in sorted(
                self.fields,
                key=lambda entry: (-entry.clarification_priority, entry.intent_key),
            )
        ]


def _compiled(pattern: str | None) -> re.Pattern[str] | None:
    if pattern is None:
        return None
    return re.compile(pattern)


def _resolve_search(
    schema: ActiveSchema,
    search: Any,
    *,
    default_fulltext_index: str | None,
) -> tuple[ResolvedSearch | None, UnusableSearch | None]:
    """Bind one configured search to the schema, or explain why it cannot be.

    Every rejection here would otherwise have surfaced as a `GuardRejected` deep
    inside a turn, one plan at a time, with the associate seeing a search that
    quietly returned less than it should have. Resolving at construction turns
    that into one statement at startup.
    """
    entity = schema.entities.get(search.entity)
    if entity is None:
        return None, UnusableSearch(search.entity, search.field, "entity is not in the schema")
    definition = entity.fields.get(search.field)
    if definition is None:
        return None, UnusableSearch(search.entity, search.field, "field is not on the entity")
    if search.strategy == FULLTEXT_STRATEGY:
        if not definition.capabilities.searchable:
            return None, UnusableSearch(search.entity, search.field, "field is not searchable")
    elif search.strategy not in definition.capabilities.operators:
        # The single most common way a pass used to disappear: `state` and
        # `postal_code` are EXACT-only, and a configured CONTAINS on either was
        # refused by the schema guard, taking the whole address pass with it.
        return None, UnusableSearch(
            search.entity,
            search.field,
            f"operator {search.strategy!r} is not enabled for the field",
        )
    unknown_results = tuple(
        result for result in search.result_fields if result not in entity.fields
    )
    if unknown_results:
        return None, UnusableSearch(
            search.entity, search.field, f"unknown result fields: {sorted(unknown_results)}"
        )
    return (
        ResolvedSearch(
            entity_id=search.entity,
            field_id=search.field,
            strategy=search.strategy,
            limit=search.limit,
            result_fields=tuple(search.result_fields),
            value_form=search.value_form,
            applies_when=_compiled(search.applies_when_pattern),
            narrow_with=search.narrow_with,
            fulltext_index=(
                (search.fulltext_index or default_fulltext_index)
                if search.strategy == FULLTEXT_STRATEGY
                else None
            ),
            only_when_nothing_found=search.only_when_nothing_found,
            match_label=search.match_label or "",
            deferred_score_ceiling=search.deferred_score_ceiling_millionths / 1_000_000,
            distinct_ratio=(
                definition.selectivity.distinct_ratio
                if definition.selectivity is not None
                else None
            ),
            identifier_likelihood=(
                definition.selectivity.identifier_likelihood.value
                if definition.selectivity is not None
                else IdentifierLikelihood.UNKNOWN.value
            ),
        ),
        None,
    )


def build_identification_catalogue(
    configured_fields: Any,
    schema: ActiveSchema,
    *,
    default_fulltext_index: str | None = None,
) -> IdentificationCatalogue:
    """Resolve the operator's catalogue against the schema that must answer it.

    `configured_fields` is `discovery.identification_fields`, passed as a
    sequence rather than as the configuration model so this package does not
    import the returns configuration -- `runtime_factory` already owns that
    translation for `progressive`, and this follows it.
    """
    fields: list[IdentificationField] = []
    unresolved: list[UnusableSearch] = []
    for configured in configured_fields:
        if not configured.enabled:
            continue
        searches: list[ResolvedSearch] = []
        unusable: list[UnusableSearch] = []
        for search in configured.searches:
            resolved, failure = _resolve_search(
                schema, search, default_fulltext_index=default_fulltext_index
            )
            if resolved is not None:
                searches.append(resolved)
            elif failure is not None:
                unusable.append(failure)
                unresolved.append(failure)
        fields.append(
            IdentificationField(
                field_id=configured.field_id,
                intent_key=configured.intent_key,
                label=configured.label,
                description=configured.description,
                aliases=tuple(configured.aliases),
                value_type=configured.value_type,
                multiple=configured.multiple,
                normalization=configured.normalization,
                validation=_compiled(configured.validation_pattern),
                sensitivity=configured.sensitivity,
                ranking_weight=configured.ranking_weight_millionths / 1_000_000,
                exact_match_bonus=configured.exact_match_bonus_millionths / 1_000_000,
                clarification_priority=configured.clarification_priority,
                searches=tuple(searches),
                unusable=tuple(unusable),
            )
        )
    return IdentificationCatalogue(fields=tuple(fields), unresolved=tuple(unresolved))
