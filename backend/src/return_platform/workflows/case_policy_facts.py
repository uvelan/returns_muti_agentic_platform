"""The case fact log, read as `PolicyEvaluationInput` (3A.3, 3A.7 step 1).

The evaluator is pure and takes resolved values. This module is the adapter
that resolves them from `case_facts`, and it is a separate module rather than a
method on the activity for one reason: it performs no IO, so the admission rule
that decides what the evaluator is allowed to see is testable on a list of
dictionaries.

**Tri-state is the whole point.** Every policy-critical field defaults to
`UNKNOWN` and only a fact that says otherwise moves it. `known false != not
mentioned`: a fact log with no `installed` entry produces
`installed = UNKNOWN`, which sends the case to review, and never
`installed = FALSE`, which would let it approve. That direction of failure is
the one thing this file exists to guarantee, so an unparseable value, an
unrecognised acquisition method and a missing fact all resolve the same way --
to `UNKNOWN`, never to `FALSE`.

**Admission, not just reading.** `PolicyFactEnvelope` and `admitted_value` in
`policy/evaluation_input.py` already state the rule ("a fact awaiting
validation, rejected, or superseded by a later capture is not evidence"); this
module *enforces* it against the three provenance fields the log actually
carries:

```text
superseded by a later fact   -> SUPERSEDED           -> UNKNOWN
acquisitionMethod INFERRED   -> PENDING_VALIDATION   -> UNKNOWN
acquisitionMethod unknown    -> PENDING_VALIDATION   -> UNKNOWN
STATED | OBSERVED | DERIVED  -> ACCEPTED             -> the stated value
```

`INFERRED` is inadmissible deliberately. It is what a model suggested, and the
programme's second architectural decision is that the model is advisory while
the deterministic evaluator is authoritative -- so a model's guess may inform a
question to the associate and may never be the evidence a decision rests on.
Plan sect. 7.3 puts it in as many words: "a model finding no evidence of
installation must never yield `installed = false`".

**What is deliberately not read here.** Fee declarations
(`manufacturer_restocking_fee`, `seller_restocking_fee`, ...) stay at their
`UNKNOWN` default. A `FeeDeclaration` carrying an amount must name the
authority that set it, and the fact log holds scalars with no such authority --
so reading a number out of it would be the fabricated `estimatedRefund` in a
new place. The seller fee schedule is an open decision in the plan's own
decision log, and until it is configuration there is nothing honest to read.

`approximate_purchase_date`, which the conversation *does* capture, is not read
as `purchase_date` for the same class of reason: a window boundary decided from
a date the associate described as approximate is a boundary nobody can defend.

**Where the window's date does come from.** The confirmed order. It is the
authoritative record of when the purchase happened, the associate has nothing to
state about it, and every one of the 100 orders in the reference extract carries
it. `purchase_date_from_confirmed_order` resolves it against the paths the
release binds in `source_resolution.order_date_paths`, and
`assemble_policy_evaluation_input` takes the resolved instant as
`confirmed_order_purchase_date`. It outranks a `purchase_date` fact on the log,
because a source-of-record date beats one somebody typed; the displaced fact is
reported in `excluded` rather than dropped silently. When the order carries no
usable date the field stays `None` and the case reviews on
`PURCHASE_DATE_UNKNOWN`, which is the correct answer and not a defect.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Final

from return_platform.policy.evaluation_input import (
    EvidenceState,
    PolicyEvaluationInput,
    PolicyFactEnvelope,
    admitted_value,
)
from return_platform.policy.vocabulary import (
    DamageCause,
    ManufacturerAcceptance,
    ReturnReason,
    TriState,
)

__all__ = [
    "CONFIRMED_ORDER_PURCHASE_DATE",
    "CONTRACT_OVERRIDE_FACT",
    "DATE_FACT_FIELDS",
    "ENUM_FACT_FIELDS",
    "TRI_STATE_FACT_NAMES",
    "AssembledPolicyFacts",
    "assemble_policy_evaluation_input",
    "evidence_state_of",
    "purchase_date_from_confirmed_order",
    "tri_state_of",
]

#: Fact names whose value is a policy-critical tri-state, mapped onto the
#: identically named field of `PolicyEvaluationInput`.
#:
#: The names are the evaluator's own, not new vocabulary. Nothing writes most of
#: them yet -- conversational extraction currently captures `return_reason` and
#: `product_presence` and no more -- and that is the expected baseline the plan
#: describes: most returns evaluate to `REVIEW_REQUIRED` until extraction
#: catches up, which is the system working and will not look like it.
TRI_STATE_FACT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "seller_stocked",
        "special_order",
        "non_stock",
        "condition_new",
        "suitable_for_resale",
        "original_packaging",
        "packaging_undamaged",
        "all_original_parts",
        "used",
        "installed",
        "modified",
        "rebuilt",
        "reconditioned",
        "repaired",
        "altered",
        "damaged",
        "buyer_accepts_manufacturer_fees",
        "seller_fee_waiver",
    }
)

#: Fact name -> (input field, closed vocabulary). A value outside the
#: vocabulary resolves to that enum's `UNKNOWN` member rather than raising: a
#: source system that starts emitting a reason this release has never heard of
#: must produce a case a human looks at, not a workflow that dies.
ENUM_FACT_FIELDS: Final[
    dict[str, tuple[str, type[ReturnReason | DamageCause | ManufacturerAcceptance]]]
] = {
    "return_reason": ("return_reason", ReturnReason),
    "damage_cause": ("damage_cause", DamageCause),
    "manufacturer_return_acceptance": (
        "manufacturer_return_acceptance",
        ManufacturerAcceptance,
    ),
}

#: Fact name -> input field, for the two instants the window rules read.
DATE_FACT_FIELDS: Final[dict[str, str]] = {
    "purchase_date": "purchase_date",
    "delivery_date": "delivery_date",
}

#: The negotiated agreement a case may carry. Only its reference travels: the
#: evaluator cannot read a contract, so a case that has one is a case a human
#: decides.
CONTRACT_OVERRIDE_FACT: Final = "contract_override_reference"

#: What `AssembledPolicyFacts.admitted` records when the window's date came from
#: the confirmed order rather than from the fact log. A distinct name, not
#: `purchase_date`, so `policy_facts_admitted` on the case says which of the two
#: it was without anyone having to reconstruct it.
CONFIRMED_ORDER_PURCHASE_DATE: Final = "confirmed_order_purchase_date"

_TRUE_TOKENS: Final[frozenset[str]] = frozenset({"TRUE", "YES", "Y"})
_FALSE_TOKENS: Final[frozenset[str]] = frozenset({"FALSE", "NO", "N"})

#: Acquisition methods that make a fact admissible evidence. `INFERRED` is
#: absent on purpose -- see the module docstring.
_ADMISSIBLE_ACQUISITION: Final[frozenset[str]] = frozenset({"STATED", "OBSERVED", "DERIVED"})


@dataclass(frozen=True, slots=True)
class AssembledPolicyFacts:
    """The evaluator's input, plus what was left out of it and why.

    `excluded` is persisted as provenance by the caller. A case that went to
    review because its only `installed` fact was a model's inference is a
    different case from one nobody ever asked, and an operator who cannot tell
    them apart cannot act on either.
    """

    facts: PolicyEvaluationInput
    admitted: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]


def tri_state_of(value: object) -> TriState:
    """A stored fact value as a tri-state. Anything unrecognised is `UNKNOWN`.

    Booleans and the obvious spellings only. An integer is deliberately not
    truthy here: a `1` in a field the log types as heterogeneous is as likely to
    be a quantity as a yes, and guessing wrong in the `TRUE` direction is how a
    return gets approved on a fact nobody stated.
    """
    if isinstance(value, bool):
        return TriState.TRUE if value else TriState.FALSE
    if isinstance(value, str):
        token = value.strip().upper()
        if token in _TRUE_TOKENS:
            return TriState.TRUE
        if token in _FALSE_TOKENS:
            return TriState.FALSE
    return TriState.UNKNOWN


def evidence_state_of(
    document: Mapping[str, Any], *, superseded_ids: frozenset[str]
) -> EvidenceState:
    """Whether this fact document may enter an evaluation.

    Three inputs, all of them already on the log: whether a later capture
    supersedes it, how it was acquired, and nothing else. There is no stored
    `validationState` field -- `PolicyFactEnvelope` documents it as the shape the
    adapter resolves rather than a column -- so this function is where the
    plan's admission rule becomes enforceable.
    """
    if str(document.get("factId") or "") in superseded_ids:
        return EvidenceState.SUPERSEDED
    method = str(document.get("acquisitionMethod") or "").upper()
    if method in _ADMISSIBLE_ACQUISITION:
        return EvidenceState.ACCEPTED
    return EvidenceState.PENDING_VALIDATION


def _aware(value: object) -> datetime | None:
    """A stored instant as an aware datetime, or `None` if it is not one.

    Mongo hands back naive UTC datetimes; a string arrives from a fact written
    by a producer that serialised its own. Both are stamped UTC rather than
    guessed at, which matches how the rest of the operational store reads them.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _business_day_instant(value: object) -> datetime | None:
    """A source date field as an instant that keeps its calendar day.

    The sales extract stores an order date as a **calendar date**, encoded as
    midnight UTC: every one of the 100 orders in the reference extract has
    `orderDate` at exactly `T00:00:00.000Z`. Read literally, that instant is the
    *previous* day in every zone west of Greenwich -- 2025-10-14 becomes
    2025-10-13 in `America/New_York`, which is the deployment's declared
    business zone -- and the evaluator counts local calendar days, so the
    customer would silently lose a day of their window.

    So a value that carries no time of day is placed at midday UTC, which reads
    as the same calendar date from UTC-11 through UTC+11 and therefore in every
    zone this platform is deployed in. A value that *does* carry a time of day
    is a real instant and is passed through untouched -- the correction applies
    to a date the source wrote as a date, and nothing else.

    Mongo extended JSON (`{"$date": ...}`) is accepted because the reference
    dataset is stored that way; the driver itself hands back `datetime`.
    """
    if isinstance(value, Mapping):
        inner = value.get("$date")
        return None if inner is None else _business_day_instant(inner)
    if isinstance(value, datetime):
        resolved = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if resolved.astimezone(UTC).timetz() == time(0, 0, tzinfo=UTC):
            return datetime.combine(resolved.astimezone(UTC).date(), time(12, 0), tzinfo=UTC)
        return resolved
    if isinstance(value, date):
        return datetime.combine(value, time(12, 0), tzinfo=UTC)
    if isinstance(value, str):
        parsed = _aware(value)
        return None if parsed is None else _business_day_instant(parsed)
    return None


def _resolve_path(document: Mapping[str, Any], path: str) -> Any:
    """One dotted path against the source document, or `None`.

    Dotted rather than a segment list because that is how
    `source_resolution` already writes every other binding in the release, and
    two spellings of the same idea is how one of them stops being maintained.
    """
    current: Any = document
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def purchase_date_from_confirmed_order(
    document: Mapping[str, Any] | None, *, paths: Sequence[str]
) -> datetime | None:
    """The confirmed order's own date, as the window's basis.

    `paths` comes from `source_resolution.order_date_paths` in the active
    release. No physical path is written here, for the reason
    `operations/order_lines/source.py` gives for the same decision: a literal
    would be correct only until the next release re-binds the field, and the
    failure would be a window silently decided from nothing.

    The first path that resolves to a usable value wins, so the release states
    the preference. `None` when the document is absent, when no path resolves,
    or when what resolved is not a date -- and `None` means the case reviews on
    `PURCHASE_DATE_UNKNOWN`. There is no fallback to a neighbouring field:
    `invoiceDate` differs from `orderDate` on 67 of the 100 reference orders and
    is missing from one of them, and `entryDate` is when the ERP was keyed
    rather than when the customer bought. Substituting either would answer a
    30-day boundary with a date that is not the purchase.
    """
    if document is None:
        return None
    for path in paths:
        resolved = _business_day_instant(_resolve_path(document, path))
        if resolved is not None:
            return resolved
    return None


def _latest_by_name(
    documents: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Newest document per fact name, by exactly `latest_case_facts`' rule.

    Reimplemented over the raw log rather than calling the repository
    projection because the admission rule needs the *whole* log: supersession is
    a relation between two documents, and a projection that has already
    discarded the superseding one cannot express it.
    """
    latest: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        name = str(document.get("factName") or "")
        if not name:
            continue
        current = latest.get(name)
        if current is None:
            latest[name] = document
            continue
        if (
            _aware(document.get("recordedAt")) or datetime.min.replace(tzinfo=UTC),
            str(document.get("factId") or ""),
        ) >= (
            _aware(current.get("recordedAt")) or datetime.min.replace(tzinfo=UTC),
            str(current.get("factId") or ""),
        ):
            latest[name] = document
    return latest


def assemble_policy_evaluation_input(
    documents: Iterable[Mapping[str, Any]],
    *,
    request_date: datetime,
    confirmed_order_purchase_date: datetime | None = None,
    configuration_release_id: str | None = None,
    policy_version: str | None = None,
) -> AssembledPolicyFacts:
    """Build one evaluation input from a case's whole fact log.

    `confirmed_order_purchase_date` is the date on the order the associate
    confirmed, already resolved by the caller -- this module performs no IO, so
    reading the sales document belongs on the activity side, and
    `purchase_date_from_confirmed_order` above is the pure half of that read.
    It is the authoritative basis of the standard return window and therefore
    outranks any `purchase_date` on the fact log, which is reported as
    `SUPERSEDED_BY_CONFIRMED_ORDER` in `excluded` rather than dropped. Omitted,
    the log's own value stands and the behaviour is exactly what it was.

    Raises `ValueError` when the admitted facts contradict each other -- a
    purchase date after the request, a damage cause stated for an item
    established as undamaged. `PolicyEvaluationInput` enforces both at
    construction and neither is repaired here: a contradiction is a case a human
    looks at, and silently dropping one side of it would decide which of two
    stated facts to believe.
    """
    log = list(documents)
    superseded_ids = frozenset(
        str(document["supersedesFactId"])
        for document in log
        if document.get("supersedesFactId") is not None
    )
    latest = _latest_by_name(log)

    values: dict[str, Any] = {
        "request_date": request_date,
        "configuration_release_id": configuration_release_id,
        "policy_version": policy_version,
    }
    admitted: list[str] = []
    excluded: list[tuple[str, str]] = []

    for name, document in sorted(latest.items()):
        state = evidence_state_of(document, superseded_ids=superseded_ids)
        if not _is_policy_fact(name):
            continue
        if state is not EvidenceState.ACCEPTED:
            # Recorded and dropped. The evaluator sees `UNKNOWN`, which is the
            # only safe reading of evidence that has not been accepted.
            excluded.append((name, state.value))
            continue
        resolved = _resolve(name, document, state)
        if resolved is None:
            excluded.append((name, "UNPARSEABLE"))
            continue
        field, value = resolved
        values[field] = value
        admitted.append(name)

    if confirmed_order_purchase_date is not None:
        if "purchase_date" in values:
            excluded.append(("purchase_date", "SUPERSEDED_BY_CONFIRMED_ORDER"))
            admitted.remove("purchase_date")
        values["purchase_date"] = confirmed_order_purchase_date
        admitted.append(CONFIRMED_ORDER_PURCHASE_DATE)

    return AssembledPolicyFacts(
        facts=PolicyEvaluationInput(**values),
        admitted=tuple(admitted),
        excluded=tuple(excluded),
    )


def _is_policy_fact(name: str) -> bool:
    return (
        name in TRI_STATE_FACT_NAMES
        or name in ENUM_FACT_FIELDS
        or name in DATE_FACT_FIELDS
        or name == CONTRACT_OVERRIDE_FACT
        or name == "quantity"
    )


def _resolve(
    name: str, document: Mapping[str, Any], state: EvidenceState
) -> tuple[str, Any] | None:
    """One admitted fact as a field and value, or `None` if it says nothing.

    Tri-states go through `PolicyFactEnvelope` and `admitted_value` rather than
    being read directly, so the admission rule the policy package publishes is
    the one applied here -- two implementations of "may this fact count" is how
    one of them ends up permitting what the other refuses.
    """
    value = document.get("value")
    if name in TRI_STATE_FACT_NAMES:
        envelope = PolicyFactEnvelope(
            value=tri_state_of(value),
            provenance=str(
                document.get("sourcePath") or document.get("sourceSystem") or "CASE_FACT_LOG"
            ),
            acquisition_method=str(document.get("acquisitionMethod") or "UNKNOWN"),
            validation_state=state,
            captured_at=_aware(document.get("observedAt"))
            or _aware(document.get("recordedAt"))
            or datetime.now(UTC),
        )
        resolved = admitted_value(envelope)
        if resolved is TriState.UNKNOWN:
            return None
        return name, resolved
    if name in ENUM_FACT_FIELDS:
        field, vocabulary = ENUM_FACT_FIELDS[name]
        if not isinstance(value, str):
            return None
        try:
            member = vocabulary(value.strip().upper())
        except ValueError:
            return None
        if member.value == "UNKNOWN":
            return None
        return field, member
    if name in DATE_FACT_FIELDS:
        instant = _aware(value)
        return None if instant is None else (DATE_FACT_FIELDS[name], instant)
    if name == CONTRACT_OVERRIDE_FACT:
        if not isinstance(value, str) or not value.strip():
            return None
        return "contract_override_reference", value.strip()
    if name == "quantity":
        # Carried because baseline section 10 lists it, and read by no rule.
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None
        return "quantity", value
    return None  # pragma: no cover - guarded by `_is_policy_fact`
