"""Search strategy and ranking for order intent.

Progressive search decomposes the associate's free-text intent into a set of
independent, narrowly-scoped graph reads — one per identifying signal — rather
than a single query that requires every signal to match at once. This lets a
customer be found from partial or combined information (an order number
*or* a customer name *or* a delivery-date window *or* any combination of
those), because each signal is tried on its own and the results are then
merged and scored by :func:`rank_search_results`.

Not every field on :class:`OrderSearchIntent` has a backing field in the active
knowledge-graph schema, and where one is missing the signal is *reported* rather
than dropped: silently ignoring "it was the blue one" looks like "no results" to
the associate with no indication why. :func:`build_progressive_plans` therefore
names what it could not use in ``unsupported_signals``.

``colors`` is the only such field left. No entity carries a colour property, and
matching a colour word against ``product_description`` would put "Blue Ridge
Faucet" in front of someone who said the tap was blue -- a wrong order presented
with no sign that the colour had been matched loosely.

Address, city, state, postal code, email and phone were on that list too, and
were being dropped long after the schema grew real properties for all six. That
is the worse failure of the two: an associate answering the agent's own
clarifying question -- the policy in ``config/returns/production.yaml`` ranks
email at 95 and phone at 90, above every narrowing signal -- contributed nothing
to the search. They are ordinary passes now.
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

logger = logging.getLogger("return_platform.dynamic_knowledge.order_agent.search_strategy")

# Fields the model may extract into OrderSearchIntent that have no backing
# graph field yet. Kept in one place so this list and the module docstring
# above stay in sync as the schema evolves.
_UNSUPPORTED_INTENT_FIELDS: tuple[str, ...] = ("colors",)

# Where an address or contact detail lives, and how precisely it can be matched.
#
# Two homes, deliberately, because they answer different questions: the contact
# point is where the *customer* is registered, `sales_order` records where this
# particular order was sent. An associate saying "Dallas" could mean either, and
# a search that asked only one would miss an order shipped to a job site.
#
# The operator per field is the one the schema actually enables -- `state` and
# `postal_code` are EXACT-only, and asking for CONTAINS on them would be refused
# by the schema guard, taking the whole pass with it.
_CONTACT_ENTITY = "contact_point"
_ORDER_ENTITY = "sales_order"

# Date fields only expose range operators (see the active schema): an exact-date
# match is expressed as a same-day BETWEEN.
#
# `order_date`, not `delivered_at`. The schema is now derived from the real
# salesInv documents and there is no delivery timestamp in them -- the dates
# that exist are order, commit, invoice and shipped. `delivered_at` was a field
# of the earlier synthetic schema, so every date-bearing search was being
# rejected by the schema guard as an unknown field and silently dropped from the
# plan set: the associate's "it was around the 14th" contributed nothing.
# `order_date` is also the one an associate is most likely to recall and the
# only one populated across every order in the dataset. If a genuine delivery
# timestamp is ever sourced, this is the line that should move to it.
_DATE_FIELD_ENTITY = "sales_order"
_DATE_FIELD_ID = "order_date"

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

#: The entity and field the customer full-text index covers. The index itself is
#: named by configuration; which field it indexes is a property of the schema
#: (migration 0013 indexes `Customer.customer_name`), so it lives here.
_CUSTOMER_ENTITY = "customer"
_CUSTOMER_NAME_FIELD = "customer_name"

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


# Intent fields that identify *what* was searched for, as opposed to
# metadata about the search itself (searchMode, confidence, wantsMoreResults).
# Used to detect whether a "show next" follow-up still refers to the same
# search or the associate has moved on to a different one.
_SIGNATURE_FIELDS: tuple[str, ...] = (
    "orderIds",
    "orderNumbers",
    "customerNames",
    "streetAddresses",
    "cities",
    "states",
    "postalCodes",
    "emails",
    "phones",
    "dateFrom",
    "dateTo",
    "approximateDate",
    "skus",
    "productNames",
    "colors",
    "quantities",
    "freeTextTerms",
)


def search_intent_signature(intent: OrderSearchIntent) -> str:
    """Stable fingerprint of the identifying signals in a search intent.

    Two intents with the same signature are considered "the same search" for
    pagination purposes, regardless of metadata fields like confidence or
    wantsMoreResults.
    """
    payload = {field: getattr(intent, field) for field in _SIGNATURE_FIELDS}
    return sha256_digest(payload)


def normalize_string(val: str) -> str:
    """Normalize a string for loose, punctuation/whitespace-insensitive matching."""
    return re.sub(r"[\s\-]+", "", val.lower())


def unsupported_signals(intent: OrderSearchIntent) -> tuple[str, ...]:
    """Identifying signals this intent carries that no plan can express.

    Public because two docstrings already promised it and nothing defined it:
    callers were told to check a function that did not exist, and the only way
    to read the answer was to run a search and inspect the ranked result.
    """
    return tuple(
        field_name for field_name in _UNSUPPORTED_INTENT_FIELDS if getattr(intent, field_name)
    )


def build_progressive_plans(intent: OrderSearchIntent) -> list[LogicalQueryPlan]:
    """Translate a (possibly partial) search intent into a sequence of query plans.

    Every populated signal on ``intent`` gets its own narrowly-scoped plan;
    ``rank_search_results`` is responsible for combining and scoring whatever
    comes back. Callers that want visibility into intent fields that could not
    be turned into a plan should check :func:`unsupported_signals`.
    """
    plans: list[LogicalQueryPlan] = []

    # 1. Exact order identifiers.
    for num in dict.fromkeys(intent.orderNumbers + intent.orderIds):
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="sales_order",
                filters=(
                    QueryCondition(
                        entity_id="sales_order",
                        field_id="sales_order_number",
                        operator="EXACT",
                        value=num,
                    ),
                ),
                limit=1,
            )
        )

    # 2. Customer name.
    for name in dict.fromkeys(intent.customerNames):
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="customer",
                filters=(
                    QueryCondition(
                        entity_id="customer",
                        field_id="customer_name",
                        operator="CONTAINS",
                        value=name,
                    ),
                ),
                limit=5,
            )
        )

    # 3. SKU.
    for sku in dict.fromkeys(intent.skus):
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="product",
                filters=(
                    QueryCondition(
                        entity_id="product",
                        field_id="sku",
                        operator="EXACT",
                        value=sku,
                    ),
                ),
                limit=5,
            )
        )

    # 4. Product description (partial product info, e.g. "blue faucet" once a
    # colour field exists, or "faucet" / "Moen kitchen faucet" today). Reads
    # from order_line so the order the line belongs to comes back for free.
    for product_name in dict.fromkeys(intent.productNames):
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="order_line",
                fields=("sales_order_number", "product_description", "ordered_quantity"),
                filters=(
                    QueryCondition(
                        entity_id="order_line",
                        field_id="product_description",
                        operator="CONTAINS",
                        value=product_name,
                    ),
                ),
                limit=5,
            )
        )

    # 5. Ordered quantity, combined with product info when both are given.
    for quantity in dict.fromkeys(intent.quantities):
        filters = [
            QueryCondition(
                entity_id="order_line",
                field_id="ordered_quantity",
                operator="EXACT",
                value=quantity,
            )
        ]
        if intent.productNames:
            filters.append(
                QueryCondition(
                    entity_id="order_line",
                    field_id="product_description",
                    operator="CONTAINS",
                    value=intent.productNames[0],
                )
            )
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="order_line",
                fields=("sales_order_number", "product_description", "ordered_quantity"),
                filters=tuple(filters),
                limit=5,
            )
        )

    # 6. Contact details the associate was asked for.
    #
    # Email and phone are the highest-priority customer anchors in the
    # clarification policy, so they are tried ahead of the broader narrowing
    # signals below.
    for email in dict.fromkeys(intent.emails):
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id=_CONTACT_ENTITY,
                fields=("account_id", "customer_id", "email"),
                filters=(
                    QueryCondition(
                        entity_id=_CONTACT_ENTITY,
                        field_id="email",
                        # A complete address is an identifier and matches
                        # exactly; a fragment is all the associate could read
                        # off the screen, and CONTAINS is the only way it finds
                        # anything at all.
                        operator="EXACT" if "@" in email else "CONTAINS",
                        value=email,
                    ),
                ),
                limit=5,
            )
        )

    # Deduped by what is actually asked, not by what was typed: "(214)
    # 555-0142" and "2145550142" are the same number, and `dict.fromkeys` only
    # sees two different strings.
    asked: set[tuple[str, str, str]] = set()
    for phone in dict.fromkeys(intent.phones):
        digits = _digits_of(phone)
        for value, operator in _phone_attempts(phone, digits):
            if ("phone_number", value, operator) in asked:
                continue
            asked.add(("phone_number", value, operator))
            plans.append(
                LogicalQueryPlan(
                    operation=QueryOperation.SEARCH,
                    start_entity_id=_CONTACT_ENTITY,
                    fields=("account_id", "customer_id", "phone_number"),
                    filters=(
                        QueryCondition(
                            entity_id=_CONTACT_ENTITY,
                            field_id="phone_number",
                            operator=operator,
                            value=value,
                        ),
                    ),
                    limit=5,
                )
            )
        if digits and ("ship_to_phone", digits, "EXACT") not in asked:
            asked.add(("ship_to_phone", digits, "EXACT"))
            # `ship_to_phone` is EXACT-only, so only the digits form is worth
            # asking for: a formatted value would have to match character for
            # character to return anything.
            plans.append(
                LogicalQueryPlan(
                    operation=QueryOperation.SEARCH,
                    start_entity_id=_ORDER_ENTITY,
                    fields=("sales_order_number", "customer_id", "ship_to_phone"),
                    filters=(
                        QueryCondition(
                            entity_id=_ORDER_ENTITY,
                            field_id="ship_to_phone",
                            operator="EXACT",
                            value=digits,
                        ),
                    ),
                    limit=5,
                )
            )

    # 7. Where the customer is, or where this order went.
    for value, contact_field, order_field, operator in _location_signals(intent):
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id=_CONTACT_ENTITY,
                fields=("account_id", "customer_id", contact_field),
                filters=(
                    QueryCondition(
                        entity_id=_CONTACT_ENTITY,
                        field_id=contact_field,
                        operator=operator,
                        value=value,
                    ),
                ),
                limit=10,
            )
        )
        if order_field is None:
            continue
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id=_ORDER_ENTITY,
                fields=("sales_order_number", "customer_id", order_field),
                filters=(
                    QueryCondition(
                        entity_id=_ORDER_ENTITY,
                        field_id=order_field,
                        operator=operator,
                        value=value,
                    ),
                ),
                limit=10,
            )
        )

    # 8. Order date window: dateFrom/dateTo, a single open bound, or a same-day
    # match from approximateDate. The date field only supports range operators
    # (GT/GTE/LT/LTE/BETWEEN) in the active schema, never EXACT.
    date_condition = _date_condition(intent)
    if date_condition is not None:
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id=_DATE_FIELD_ENTITY,
                fields=("sales_order_number", "customer_id", _DATE_FIELD_ID),
                filters=(date_condition,),
                limit=10,
            )
        )

    # 9. Fallback free-text search. sales_order_number only supports EXACT
    # match in the schema (CONTAINS is not an enabled operator for it), so an
    # unclassified free-text term is searched against product_description
    # instead, which does support CONTAINS and is the more likely match for
    # leftover descriptive text anyway.
    for term in dict.fromkeys(intent.freeTextTerms):
        plans.append(
            LogicalQueryPlan(
                operation=QueryOperation.SEARCH,
                start_entity_id="order_line",
                fields=("sales_order_number", "product_description", "ordered_quantity"),
                filters=(
                    QueryCondition(
                        entity_id="order_line",
                        field_id="product_description",
                        operator="CONTAINS",
                        value=term,
                    ),
                ),
                limit=5,
            )
        )

    unsupported = unsupported_signals(intent)
    if unsupported:
        logger.warning(
            "order_search_unsupported_intent_signals",
            extra={"fields": unsupported, "search_mode": intent.searchMode},
        )

    return plans


def _digits_of(value: str) -> str:
    return re.sub(r"[^0-9]+", "", value)


def _phone_attempts(raw: str, digits: str) -> tuple[tuple[str, str], ...]:
    """The forms of a phone number worth asking the graph for.

    The raw value exactly, in case it is stored formatted the way the associate
    read it out, and the digits as a CONTAINS so a stored form with different
    punctuation -- or a country code nobody mentioned -- still matches. Deduped,
    because a number typed plainly would otherwise be searched twice.
    """
    attempts: list[tuple[str, str]] = [(raw, "EXACT")]
    if digits and digits != raw:
        attempts.append((digits, "CONTAINS"))
    return tuple(attempts)


def _location_signals(
    intent: OrderSearchIntent,
) -> tuple[tuple[str, str, str | None, str], ...]:
    """Each address signal, paired with the fields that can answer it.

    Returns ``(value, contact_point field, sales_order field, operator)``.
    `address_line1` has no order-side counterpart -- `sales_order` records the
    ship-to city, state and postal code but not the street -- so that entry
    carries ``None`` rather than a field name that would fail the schema guard
    and take the pass with it.
    """
    signals: list[tuple[str, str, str | None, str]] = []
    for street in dict.fromkeys(intent.streetAddresses):
        signals.append((street, "address_line1", None, "CONTAINS"))
    for city in dict.fromkeys(intent.cities):
        signals.append((city, "city", "ship_to_city", "CONTAINS"))
    for state in dict.fromkeys(intent.states):
        signals.append((state, "state", "ship_to_state", "EXACT"))
    for postal_code in dict.fromkeys(intent.postalCodes):
        signals.append((postal_code, "postal_code", "ship_to_postal_code", "EXACT"))
    return tuple(signals)


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


def build_customer_name_query(names: tuple[str, ...], policy: CustomerFulltextPolicy) -> str:
    """The Lucene query for one or more searched customer names.

    Each token becomes a prefix term OR'd with a fuzzy term: the prefix carries
    the abbreviation an associate types ("Smi" for Smith), the fuzzy term
    carries the misspelling ("Jhon" for John). Tokens within one name are
    AND'ed -- every word the associate gave has to be accounted for, or a
    surname alone would drag in every customer sharing it -- and separate names
    are OR'ed, because they are alternatives rather than a compound.

    Returns ``""`` when nothing searchable survives tokenization; callers treat
    that as "no query to ask" rather than as a query matching everything.
    """
    alternatives: list[str] = []
    for name in dict.fromkeys(names):
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


def build_customer_fulltext_plan(
    names: tuple[str, ...], policy: CustomerFulltextPolicy
) -> LogicalQueryPlan | None:
    """A ranked, server-side approximate search over every indexed customer.

    ``None`` when the policy disables it or no searchable token survives, so the
    caller reports "no misspelling recovery available" rather than issuing a
    query that would match the whole table.
    """
    if not policy.enabled:
        return None
    query = build_customer_name_query(names, policy)
    if not query:
        return None
    # `account_id`, not `customer_key`. The customer's natural key is
    # (account_id, customer_id); `customer_key` was a surrogate on the earlier
    # synthetic schema and does not exist, which made the guard reject this plan
    # as an unknown result field -- so the misspelling fallback never ran at all.
    # `candidate_key` identifies a customer row by `customer_id`, so the pair is
    # what the caller needs to name the branch the match was found in.
    return LogicalQueryPlan(
        operation=QueryOperation.FULLTEXT_SEARCH,
        start_entity_id=_CUSTOMER_ENTITY,
        fields=("account_id", "customer_id", _CUSTOMER_NAME_FIELD),
        fulltext_index=policy.index_name,
        fulltext_field_id=_CUSTOMER_NAME_FIELD,
        fulltext_query=query,
        limit=max(1, min(policy.candidate_limit, MAX_CACHED_CANDIDATES)),
    )


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


def _date_condition(intent: OrderSearchIntent) -> QueryCondition | None:
    if intent.dateFrom and intent.dateTo:
        value: Any = {"from": intent.dateFrom, "to": intent.dateTo}
        operator = "BETWEEN"
    elif intent.dateFrom:
        value = intent.dateFrom
        operator = "GTE"
    elif intent.dateTo:
        value = intent.dateTo
        operator = "LTE"
    elif intent.approximateDate:
        # Treat an approximate date as a same-day window. This assumes
        # delivered_at is stored at day granularity; if the source ever
        # carries a time component this window will need widening.
        value = {"from": intent.approximateDate, "to": intent.approximateDate}
        operator = "BETWEEN"
    else:
        return None
    return QueryCondition(
        entity_id=_DATE_FIELD_ENTITY,
        field_id=_DATE_FIELD_ID,
        operator=operator,
        value=value,
    )


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
) -> dict[str, Any]:
    """Score and merge rows returned by the plans from ``build_progressive_plans``.

    Each entry in ``raw_results`` is a ``Neo4jKnowledgeGateway.execute`` result,
    shaped as ``{"rows": [...], "count": N}``.
    """
    candidates: dict[str, dict[str, Any]] = {}

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
            # different (possibly narrower) field selection — merge rather
            # than overwrite so evidence gathered by one plan isn't lost.
            candidate["data"] = {**row, **candidate["data"]}

            score = 0.5  # base score for appearing in any matching plan
            norm_key = normalize_string(str(key))

            for intent_id in intent.orderNumbers + intent.orderIds:
                if normalize_string(intent_id) == norm_key:
                    score += 0.5
                    candidate["matches"].append("order_number_exact")

            if "customer_name" in row:
                norm_name = normalize_string(row["customer_name"])
                for intent_name in intent.customerNames:
                    if normalize_string(intent_name) in norm_name:
                        score += 0.3
                        candidate["matches"].append("customer_name_contains")

            if "product_description" in row:
                norm_description = normalize_string(row["product_description"])
                for product_name in intent.productNames:
                    if normalize_string(product_name) in norm_description:
                        score += 0.2
                        candidate["matches"].append("product_description_contains")

            if "ordered_quantity" in row:
                for quantity in intent.quantities:
                    if row["ordered_quantity"] == quantity:
                        score += 0.1
                        candidate["matches"].append("ordered_quantity_exact")

            # The contact anchors, weighted the way the clarification policy
            # ranks them: an exact email is the strongest customer signal after
            # an order number, and without a bump here a contact matched on a
            # ZIP shared by a thousand customers ranked level with one matched
            # on the address the associate read out.
            if "email" in row:
                norm_email = normalize_string(str(row["email"]))
                for intent_email in intent.emails:
                    norm_intent = normalize_string(intent_email)
                    if norm_intent == norm_email:
                        score += 0.45
                        candidate["matches"].append("email_exact")
                    elif norm_intent and norm_intent in norm_email:
                        score += 0.2
                        candidate["matches"].append("email_contains")

            for phone_field in ("phone_number", "ship_to_phone"):
                if phone_field not in row:
                    continue
                row_digits = _digits_of(str(row[phone_field]))
                for intent_phone in intent.phones:
                    intent_digits = _digits_of(intent_phone)
                    # Compared as digits on both sides. A stored "2145550142"
                    # and a spoken "(214) 555-0142" are the same number, and
                    # scoring them as a miss is how the right customer ends up
                    # below the wrong one.
                    if intent_digits and intent_digits == row_digits:
                        score += 0.4
                        candidate["matches"].append("phone_exact")
                    elif intent_digits and row_digits and intent_digits in row_digits:
                        score += 0.2
                        candidate["matches"].append("phone_contains")

            # Where the customer is, or where the order went. Weaker on
            # purpose: a city or a state is shared by many customers, so it
            # narrows a result set rather than identifying anyone, and it must
            # not outrank a name or a contact detail.
            for value, row_fields, weight in (
                (intent.streetAddresses, ("address_line1",), 0.3),
                (intent.cities, ("city", "ship_to_city"), 0.15),
                (intent.states, ("state", "ship_to_state"), 0.05),
                (intent.postalCodes, ("postal_code", "ship_to_postal_code"), 0.2),
            ):
                for row_field in row_fields:
                    if row_field not in row:
                        continue
                    norm_row = normalize_string(str(row[row_field]))
                    for wanted in value:
                        if normalize_string(wanted) and normalize_string(wanted) in norm_row:
                            score += weight
                            candidate["matches"].append(f"{row_field}_match")

            # `order_date`, not `delivered_at`. The schema lost `delivered_at`
            # when it was rebuilt from the real salesInv documents, so this
            # branch scored nothing for years of date-window searches: an
            # associate's "around the 14th" narrowed the plan set and then
            # contributed no rank at all.
            if _DATE_FIELD_ID in row and (
                intent.dateFrom or intent.dateTo or intent.approximateDate
            ):
                score += 0.2
                candidate["matches"].append("order_date_in_range")

            candidate["score"] = min(1.0, candidate["score"] + score)

    sorted_candidates = sorted(candidates.values(), key=lambda item: item["score"], reverse=True)

    return {
        "intent": intent.model_dump(),
        "candidates": sorted_candidates[:MAX_CACHED_CANDIDATES],
        "total_found": len(sorted_candidates),
        "unsupported_signals": list(unsupported_signals(intent)),
    }
