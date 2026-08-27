"""Search strategy and ranking for order intent.

Progressive search decomposes the associate's free-text intent into a set of
independent, narrowly-scoped graph reads — one per identifying signal — rather
than a single query that requires every signal to match at once. This lets a
customer be found from partial or combined information (an order number
*or* a customer name *or* a delivery-date window *or* any combination of
those), because each signal is tried on its own and the results are then
merged and scored by :func:`rank_search_results`.

**No identification field is named in this module.** Which signals exist, which
entity and property answer each one, with which operator, at what limit, and
what a match is worth are all read from the runtime identification catalogue
(see :mod:`identification` and ``discovery.identification_fields`` in
``config/returns/production.yaml``). This module is the machinery that turns
that catalogue into plans and turns rows back into ranked candidates.

Signals the catalogue cannot answer are *reported* rather than dropped:
silently ignoring "it was the blue one" looks like "no results" to the
associate with no indication why. That report is now derived — a signal is
unusable because no configured search binds to the active schema, not because
its name appears in a list here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import FULLTEXT_SCORE_FIELD
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.identification import (
    FULLTEXT_STRATEGY,
    IdentificationCatalogue,
    ParsedIntent,
    ResolvedSearch,
    SignalValues,
    apply_value_form,
    normalize_value,
)
from return_platform.dynamic_knowledge.order_agent.planner import (
    order_searches_by_discrimination,
)

logger = logging.getLogger("return_platform.dynamic_knowledge.order_agent.search_strategy")

# How many ranked candidates a single search keeps around for pagination
# ("show next") versus how many are ever shown to the reasoning model or the
# associate in one turn. Keeping these bounded and separate is what lets
# follow-up "show more" turns page through a cached result set instead of
# either re-querying the graph or dumping every match into the LLM context
# at once.
MAX_CACHED_CANDIDATES = 25
RESULT_PAGE_SIZE = 5

# Misspelled customer names are resolved through the Neo4j full-text index
# customer_name_search_v2 (created by migration 0013 and verified ONLINE at
# bootstrap by apply_neo4j_migrations.py). A full-text query with a fuzzy term
# searches the *complete* customer set server-side and returns matches ranked by
# score, so the correct customer cannot fall outside a client-side window.
#
# This does not require APOC. An earlier implementation fetched an unfiltered
# batch of rows and scored them with difflib on the assumption that Neo4j had no
# server-side approximate match; that bounded the search to an arbitrary, unordered
# subset and could silently miss the correct order at production scale. The index
# name is configuration (progressive.customer_fulltext_index), not a constant, so
# an operator can repoint it without a code change.
#
# Invariant: candidate limits may bound returned results. They must never bound the
# searchable corpus.

#: Words a name extraction may carry through from the associate's sentence.
#: Dropped before the query is built: "customer Smith" must search for Smith,
#: not require every customer row to also contain the word "customer".
#:
#: `and`, `or` and `not` earn their place twice over. They are noise in a name,
#: and they are Lucene's boolean keywords -- a name that tokenized to a bare
#: `OR` would be assembled into a query whose structure the associate typed.
_NAME_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "customer",
        "find",
        "for",
        "is",
        "name",
        "named",
        "not",
        "or",
        "order",
        "return",
        "the",
        "this",
        "to",
        "who",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class CustomerFulltextPolicy:
    """How the indexed customer-name search is asked and how far down it reads.

    Defaults mirror `ProgressiveDiscoveryConfiguration` so a process that never
    resolves runtime configuration still behaves the way the configured one
    does, rather than silently searching differently.

    `candidate_limit` bounds how many *ranked* rows come back. It is not a scan
    bound: the index has already ranked every customer by the time the limit
    applies. `relative_score_floor` is the narrowing bound -- a row is kept when
    its relevance is within that fraction of the best row's, so a clearly-best
    match is not padded out with four unrelated names just because there was
    room for them.
    """

    enabled: bool = True
    index_name: str = "customer_name_search_v2"
    max_edit_distance: int = 2
    one_edit_min_token_length: int = 4
    two_edit_min_token_length: int = 8
    candidate_limit: int = MAX_CACHED_CANDIDATES
    relative_score_floor: float = 0.55


def search_intent_signature(intent: OrderSearchIntent, catalogue: IdentificationCatalogue) -> str:
    """Stable fingerprint of the identifying signals in a search intent.

    Two intents with the same signature are considered "the same search" for
    pagination purposes, regardless of metadata fields like confidence or
    wantsMoreResults.

    Computed over the catalogue's keys rather than a tuple written here, and
    that is load-bearing rather than tidy: a "show next" is only allowed to page
    a cached result set gathered under the same signals, so the definition of
    "the same signals" has to move when an operator adds a field. It also covers
    any key the model supplied that the catalogue does not know, because two
    searches differing only in an unrecognized key are still two different
    searches.
    """
    values = intent.signal_values
    payload = {key: values.get(key) for key in catalogue.intent_keys}
    payload["_unrecognized"] = {
        key: value for key, value in sorted(values.items()) if key not in set(catalogue.intent_keys)
    }
    return sha256_digest(payload)


def normalize_string(val: str) -> str:
    """Normalize a string for loose, punctuation/whitespace-insensitive matching."""
    return re.sub(r"[\s\-]+", "", val.lower())


@dataclass(frozen=True, slots=True)
class PlannedSearch:
    """One plan, and the configured search that produced it.

    The search travels with the plan because the ranker needs to know which
    field a returned row was matched on and what that match is called, and
    re-deriving it from the compiled plan would be guessing.
    """

    plan: LogicalQueryPlan
    search: ResolvedSearch
    intent_key: str


@dataclass(frozen=True, slots=True)
class SearchProgram:
    """Everything one turn's intent asks of the graph.

    `deferred` holds the searches an operator marked `only_when_nothing_found`.
    Separating them is what keeps an expensive approximate search from running
    alongside the cheap exact ones and diluting them -- it earns its turn only
    when nothing else found anything.
    """

    parsed: ParsedIntent
    primary: tuple[PlannedSearch, ...] = ()
    deferred: tuple[PlannedSearch, ...] = ()


def build_search_program(
    intent: OrderSearchIntent,
    catalogue: IdentificationCatalogue,
    *,
    fulltext_policy: CustomerFulltextPolicy | None = None,
) -> SearchProgram:
    """Translate a (possibly partial) search intent into the reads it implies.

    Every populated signal the catalogue can answer gets its own narrowly-scoped
    plan; ``rank_search_results`` combines and scores whatever comes back.
    Signals the catalogue cannot answer are carried on
    ``SearchProgram.parsed`` rather than dropped.
    """
    parsed = catalogue.parse(intent.signal_values)
    policy = fulltext_policy or CustomerFulltextPolicy()
    primary: list[PlannedSearch] = []
    deferred: list[PlannedSearch] = []
    # Deduped by the question actually asked rather than by what was typed:
    # "(214) 555-0142" and "2145550142" are the same number reshaped two ways,
    # and asking the graph twice spends a turn's query budget on one answer.
    asked: set[tuple[str, str, str, str]] = set()

    # **Primaries first, and that ordering is the whole point.** One `asked` set
    # spans every signal, so whichever field reaches a question first claims it
    # -- and in catalogue order a *deferred* search could claim a question that a
    # later field asks as a *primary*, silently demoting it to the recovery pass.
    #
    # Observed exactly that way. `customer_name` configures contact-name searches
    # as a deferred fallback for "the name was a person"; `contact_name`, further
    # down the catalogue, asks the identical question as its primary. With both
    # signals populated the fallback registered first, `contact_name` contributed
    # no primary at all, every primary came back empty, and the turn fell through
    # to the deferred pass for a search that should have run in the first one.
    #
    # Two passes over the same signals fix it without a precedence rule to
    # maintain: a deferred search can only ever claim a question no primary
    # wanted, which is what "runs only when everything else failed" already means.
    for deferred_pass in (False, True):
        for signal in parsed.searchable:
            if signal.field.is_date_bound:
                continue
            for planned in _plans_for_signal(
                signal, parsed, policy=policy, asked=asked, deferred=deferred_pass
            ):
                (deferred if deferred_pass else primary).append(planned)

    for planned in _date_plans(parsed, catalogue):
        primary.append(planned)

    if parsed.unusable_signals:
        logger.warning(
            "order_search_unusable_intent_signals",
            extra={
                "fields": parsed.unusable_signals,
                "search_mode": intent.searchMode,
            },
        )
    if parsed.unknown_keys:
        # Not a rejection. The model populated something no configured field
        # claims, which is either a stale prompt or a field an operator has not
        # configured yet -- both are worth seeing, neither is worth failing the
        # associate's turn over.
        logger.warning(
            "order_search_unrecognized_intent_keys",
            extra={"keys": parsed.unknown_keys, "search_mode": intent.searchMode},
        )
    if parsed.invalid_signals:
        logger.info(
            "order_search_invalid_signal_values",
            extra={"fields": parsed.invalid_signals},
        )

    # Most discriminating first (DISC-03). Only observable when the per-turn
    # query budget truncates the set -- which is precisely the case where the
    # order decides whether the one pass that could have answered the associate
    # ever ran.
    return SearchProgram(
        parsed=parsed,
        primary=order_searches_by_discrimination(primary, catalogue),
        deferred=tuple(deferred),
    )


def _condition_for(search: ResolvedSearch, value: Any) -> QueryCondition:
    return QueryCondition(
        entity_id=search.entity_id,
        field_id=search.field_id,
        operator=search.strategy,
        value=value,
    )


def _narrowing_condition(search: ResolvedSearch, parsed: ParsedIntent) -> QueryCondition | None:
    """The companion filter that turns a weak signal into a real narrowing.

    A quantity on its own matches thousands of order lines. A quantity together
    with a product description is a search. When the companion is absent the
    quantity pass still runs -- losing it entirely would be worse than running
    it broad.
    """
    if search.narrow_with is None:
        return None
    companion = parsed.by_key(search.narrow_with)
    if companion is None or not companion.values:
        return None
    for companion_search in companion.field.searches:
        if (
            companion_search.entity_id == search.entity_id
            and companion_search.strategy != FULLTEXT_STRATEGY
        ):
            value = apply_value_form(companion.values[0], companion_search.value_form)
            return _condition_for(companion_search, value)
    return None


def _plans_for_signal(
    signal: SignalValues,
    parsed: ParsedIntent,
    *,
    policy: CustomerFulltextPolicy,
    asked: set[tuple[str, str, str, str]],
    deferred: bool,
) -> list[PlannedSearch]:
    """The plans one signal contributes to one bucket.

    `deferred` selects the bucket rather than filtering afterwards, because the
    `asked` set is populated as a side effect: a deferred search that was
    generated and then discarded would still have claimed its question, which is
    the demotion `build_search_program` runs two passes to prevent.
    """
    planned: list[PlannedSearch] = []
    for search in signal.field.searches:
        if search.only_when_nothing_found is not deferred:
            continue
        if search.strategy == FULLTEXT_STRATEGY:
            fulltext = _fulltext_plan(signal, search, policy=policy)
            if fulltext is None:
                continue
            # Indexed reads are deduped too, and on the index rather than the
            # field: two configured searches naming one index with one query are
            # one question however they are spelled, and running both returned
            # every matching row twice. `contact_name` and `customer_name`'s
            # fallback both point at `contact_name_search_v1`, so an ambiguous
            # name asked it twice and doubled its own candidate list.
            question = (
                search.entity_id,
                str(search.fulltext_index),
                FULLTEXT_STRATEGY,
                fulltext.plan.fulltext_query or "",
            )
            if question in asked:
                continue
            asked.add(question)
            planned.append(fulltext)
            continue
        narrowing = _narrowing_condition(search, parsed)
        for value in signal.values:
            if not search.accepts(value):
                continue
            shaped = apply_value_form(value, search.value_form)
            if shaped is None or shaped == "":
                continue
            question = (search.entity_id, search.field_id, search.strategy, str(shaped))
            if question in asked:
                continue
            asked.add(question)
            filters = [_condition_for(search, shaped)]
            if narrowing is not None:
                filters.append(narrowing)
            planned.append(
                PlannedSearch(
                    plan=LogicalQueryPlan(
                        operation=QueryOperation.SEARCH,
                        start_entity_id=search.entity_id,
                        fields=search.result_fields,
                        filters=tuple(filters),
                        limit=search.limit,
                    ),
                    search=search,
                    intent_key=signal.field.intent_key,
                )
            )
    return planned


def _fulltext_plan(
    signal: SignalValues, search: ResolvedSearch, *, policy: CustomerFulltextPolicy
) -> PlannedSearch | None:
    """One ranked index read covering every value of this signal at once.

    Per signal rather than per value: the index scores a document against a
    whole query, so two spellings of one customer are alternatives inside one
    query rather than two searches whose scores cannot be compared.
    """
    if not policy.enabled or search.fulltext_index is None:
        return None
    query = build_fulltext_query(tuple(str(value) for value in signal.values), policy)
    if not query:
        return None
    return PlannedSearch(
        plan=LogicalQueryPlan(
            operation=QueryOperation.FULLTEXT_SEARCH,
            start_entity_id=search.entity_id,
            fields=search.result_fields,
            fulltext_index=search.fulltext_index,
            fulltext_field_id=search.field_id,
            fulltext_query=query,
            limit=max(1, min(search.limit, MAX_CACHED_CANDIDATES)),
        ),
        search=search,
        intent_key=signal.field.intent_key,
    )


def _between_capable(
    catalogue: IdentificationCatalogue, target: tuple[str, str]
) -> ResolvedSearch | None:
    """A configured search on this property that declares BETWEEN, if there is one.

    Read from the catalogue rather than from this turn's signals, because the
    capability belongs to the configuration and not to what the associate
    happened to say. Looking only at valued signals is how a `dateFrom` plus a
    `dateTo` ended up as two open-ended range reads while the very field that
    proves BETWEEN is available sat unused in the same catalogue.
    """
    for item in catalogue.fields:
        if not item.is_date_bound:
            continue
        for search in item.searches:
            if (search.entity_id, search.field_id) == target and search.strategy == "BETWEEN":
                return search
    return None


def _date_plans(parsed: ParsedIntent, catalogue: IdentificationCatalogue) -> list[PlannedSearch]:
    """At most one plan per date field, assembled from whichever bounds were given.

    A lower and an upper bound on the same property are one window, not two
    searches, and an approximate date is a same-day window. Which configured
    field is which is read off `value_type`, so a second date field (an invoice
    date, say) needs configuration and no code.

    A merged window is only issued when some configured search on that property
    declares BETWEEN -- that declaration is the proof the schema enables the
    operator. Without it the bounds are issued separately, which is correct if
    less precise, rather than guessed at and refused by the schema guard.
    """
    by_target: dict[tuple[str, str], dict[str, tuple[SignalValues, ResolvedSearch]]] = {}
    for signal in parsed.searchable:
        if not signal.field.is_date_bound or not signal.values:
            continue
        search = signal.field.searches[0]
        by_target.setdefault((search.entity_id, search.field_id), {})[signal.field.value_type] = (
            signal,
            search,
        )

    planned: list[PlannedSearch] = []
    for target, bounds in by_target.items():
        lower = bounds.get("DATE_LOWER_BOUND")
        upper = bounds.get("DATE_UPPER_BOUND")
        point = bounds.get("DATE_POINT")
        between = _between_capable(catalogue, target)
        if lower and upper and between is not None:
            condition = _condition_for(
                between, {"from": lower[0].values[0], "to": upper[0].values[0]}
            )
            planned.append(_date_planned((lower[0], between), condition))
            continue
        if point and not lower and not upper and between is not None:
            value = point[0].values[0]
            planned.append(
                _date_planned(
                    (point[0], between), _condition_for(between, {"from": value, "to": value})
                )
            )
            continue
        for entry in (lower, upper):
            if entry is None:
                continue
            signal, search = entry
            if search.strategy == "BETWEEN":
                condition = _condition_for(
                    search, {"from": signal.values[0], "to": signal.values[0]}
                )
            else:
                condition = _condition_for(search, signal.values[0])
            planned.append(_date_planned(entry, condition))
    return planned


def _date_planned(
    entry: tuple[SignalValues, ResolvedSearch], condition: QueryCondition
) -> PlannedSearch:
    signal, search = entry
    return PlannedSearch(
        plan=LogicalQueryPlan(
            operation=QueryOperation.SEARCH,
            start_entity_id=search.entity_id,
            fields=search.result_fields,
            filters=(condition,),
            limit=search.limit,
        ),
        search=search,
        intent_key=signal.field.intent_key,
    )


def _digits_of(value: str) -> str:
    return re.sub(r"[^0-9]+", "", value)


def _name_tokens(value: str) -> tuple[str, ...]:
    """The alphanumeric words worth searching for, in order.

    Alphanumeric by construction, which is also the injection guard: every
    Lucene metacharacter (``~ * ? : ^ " ( ) [ ] { } \\ + - && ||``) is dropped
    before a query string is assembled, so an associate cannot type query syntax
    into a name field and have it reach the index as syntax. Capped so a pasted
    paragraph cannot turn into a hundred-clause query.
    """
    return tuple(
        token
        for token in re.findall(r"[A-Za-z0-9]+", value)[:16]
        if token.lower() not in _NAME_STOP_WORDS and len(token) >= 2
    )[:8]


def _edit_distance_for(token: str, policy: CustomerFulltextPolicy) -> int:
    """How far a token may be wrong before it stops being the same word.

    Scaled by length for the obvious reason: two edits on a four-letter token
    reaches most four-letter tokens, and a fuzzy term that matches everything
    ranks nothing.
    """
    if len(token) >= policy.two_edit_min_token_length:
        return min(policy.max_edit_distance, 2)
    if len(token) >= policy.one_edit_min_token_length:
        return min(policy.max_edit_distance, 1)
    return 0


def build_fulltext_query(values: tuple[str, ...], policy: CustomerFulltextPolicy) -> str:
    """The Lucene query for one or more searched values of one signal.

    Each token becomes a prefix term OR'd with a fuzzy term: the prefix carries
    the abbreviation an associate types ("Smi" for Smith), the fuzzy term
    carries the misspelling ("Jhon" for John). Tokens within one value are
    AND'ed -- every word the associate gave has to be accounted for, or a
    surname alone would drag in every customer sharing it -- and separate values
    are OR'ed, because they are alternatives rather than a compound.

    Written for customer names and correct for any short free-text identifier a
    full-text index covers, which is why it takes values rather than names: the
    product description index created by the same migration is reachable by
    configuring a `FULLTEXT` search against it, with no code here to change.

    Returns ``""`` when nothing searchable survives tokenization; callers treat
    that as "no query to ask" rather than as a query matching everything.
    """
    alternatives: list[str] = []
    for name in dict.fromkeys(values):
        clauses: list[str] = []
        for token in _name_tokens(name):
            edits = _edit_distance_for(token, policy)
            clauses.append(f"({token}* OR {token}~{edits})" if edits else f"{token}*")
        if clauses:
            alternatives.append(" AND ".join(clauses))
    if not alternatives:
        return ""
    if len(alternatives) == 1:
        return alternatives[0]
    return " OR ".join(f"({alternative})" for alternative in alternatives)


def narrow_fulltext_matches(
    rows: list[dict[str, Any]],
    *,
    policy: CustomerFulltextPolicy,
) -> list[tuple[dict[str, Any], float]]:
    """Keep the rows whose relevance is close to the best one's.

    The bound is a score, never a row count. A row-count bound over an ordered
    result is harmless where a row-count bound over an *unordered* one is the
    P0 defect this replaced, but it still answers the wrong question: five rows
    is the right answer when five customers are plausible and the wrong one when
    only one is. The floor is relative rather than absolute because a Lucene
    score has no fixed range -- it depends on the query, the corpus and the term
    frequencies in it, so "at least 2.5" means something different every turn
    while "within 55% of the best" does not.

    Returns ``(row, score)`` pairs, most relevant first, with the score column
    removed from the row: it is search metadata, not a property of the customer,
    and leaving it in the row would put it in the model's context and in the
    evidence as though the graph had returned it.
    """
    scored: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        raw_score = row.get(FULLTEXT_SCORE_FIELD)
        if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
            continue
        score = float(raw_score)
        if score <= 0.0:
            continue
        scored.append(
            ({key: value for key, value in row.items() if key != FULLTEXT_SCORE_FIELD}, score)
        )
    if not scored:
        return []
    scored.sort(key=lambda item: item[1], reverse=True)
    floor = scored[0][1] * policy.relative_score_floor
    return [(row, score) for row, score in scored if score >= floor]


def candidate_key(row: dict[str, Any]) -> str:
    """The stable identity a candidate row is deduplicated and referenced by --
    shared between `rank_search_results` and any fallback path (e.g. fuzzy
    customer matching) that builds candidates outside the normal ranking loop,
    so every candidate a turn ever surfaces gets one consistent `candidate_id`
    a later turn's `CandidateSet.validate_selection()` can match against."""
    key = row.get("sales_order_number") or row.get("customer_id") or row.get("sku")
    if key:
        return str(key)
    # A deterministic fallback -- str(id(row)) is Python object identity, never
    # stable across process restarts (or even across two calls in the same
    # process for equivalent data), so it cannot serve as a CandidateSet member.
    return sha256_digest(row)


def rank_search_results(
    intent: OrderSearchIntent,
    raw_results: list[dict[str, Any]],
    *,
    program: SearchProgram,
) -> dict[str, Any]:
    """Score and merge rows returned by a ``SearchProgram``.

    Each entry in ``raw_results`` is a ``Neo4jKnowledgeGateway.execute`` result,
    shaped as ``{"rows": [...], "count": N}``.

    Scoring reads the catalogue, not a block per field. A row scores a signal's
    configured weight when it carries a column that signal searched on and the
    value agrees, plus the configured exact bonus when the agreement is exact
    rather than partial. That is the same shape the hand-written version had --
    an exact email at 0.45 outranking a shared city at 0.15 -- with the numbers
    moved to where an operator can change them and the field names gone.

    The base 0.5 for appearing in any pass at all is kept: a row the graph
    returned is evidence even when the column that matched is not one of the
    returned columns.
    """
    candidates: dict[str, dict[str, Any]] = {}
    searches_by_key: dict[str, list[ResolvedSearch]] = {}
    for planned in (*program.primary, *program.deferred):
        searches_by_key.setdefault(planned.intent_key, []).append(planned.search)

    for res in raw_results:
        rows = res.get("rows", []) if isinstance(res, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = candidate_key(row)
            candidate = candidates.setdefault(
                key, {"candidate_id": key, "data": row, "score": 0.0, "matches": []}
            )
            # A row for the same key may arrive from more than one plan with a
            # different (possibly narrower) field selection -- merge rather
            # than overwrite so evidence gathered by one plan isn't lost.
            candidate["data"] = {**row, **candidate["data"]}

            score = 0.5  # base score for appearing in any matching plan
            for signal in program.parsed.searchable:
                for search in searches_by_key.get(signal.field.intent_key, ()):
                    matched = _match_strength(signal, search, row)
                    if matched is None:
                        continue
                    score += matched[0]
                    if matched[1] not in candidate["matches"]:
                        candidate["matches"].append(matched[1])

            candidate["score"] = min(1.0, candidate["score"] + score)

    sorted_candidates = sorted(candidates.values(), key=lambda item: item["score"], reverse=True)

    return {
        "intent": intent.model_dump(),
        "candidates": sorted_candidates[:MAX_CACHED_CANDIDATES],
        "total_found": len(sorted_candidates),
        "unsupported_signals": list(program.parsed.unusable_signals),
        "unrecognized_signals": list(program.parsed.unknown_keys),
        "invalid_signals": list(program.parsed.invalid_signals),
    }


def _match_strength(
    signal: SignalValues, search: ResolvedSearch, row: dict[str, Any]
) -> tuple[float, str] | None:
    """What this row is worth for this signal, and what the match is called.

    ``None`` when the row does not carry the column this search asked about, or
    carries it with a value that does not agree -- which is not the same as a
    zero score, because a zero would still append a match label claiming the
    field had matched.
    """
    if search.field_id not in row:
        return None
    row_value = row.get(search.field_id)
    if row_value is None:
        return None
    weight = signal.field.ranking_weight

    if signal.field.is_date_bound:
        # A date window cannot be re-checked here: the graph applied the range,
        # so the row being present *is* the match. Comparing the returned date
        # to a bound would re-implement the filter and disagree with it at the
        # edges.
        return weight, search.label

    normalized_row = normalize_value(row_value, signal.field.normalization)
    for value in signal.values:
        shaped = apply_value_form(value, search.value_form)
        normalized_value = normalize_value(shaped, signal.field.normalization)
        if not normalized_value:
            continue
        if normalized_value == normalized_row:
            return weight + signal.field.exact_match_bonus, f"{search.field_id}_exact"
        if normalized_value in normalized_row:
            return weight, f"{search.field_id}_contains"
    return None
