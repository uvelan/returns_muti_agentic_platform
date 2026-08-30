"""Assemble a case's facts into the context a model reasons over.

Contracts.md sect. 10. One pure function, and the purity is the point: given
the same facts and the same policy it returns the same bytes, every time, in
every process. `content_hash` is on the result so that "the same context" is a
claim anyone can check rather than one this module asks to be believed.

Four rules, in the order they apply:

**Canonical order is `(recordedAt, factId)`.** The same order S1's
`latest_case_facts_scoped` tie-breaks on, and for the same reason: `recordedAt`
alone is not a total order -- two facts written in one transaction share it --
and a context whose order depends on which document Mongo returned first is not
reproducible.

**The projection is scoped-latest, per `(record_scope, factName)`.** A case
with two return records has two of some facts; collapsing by name alone would
let one record's tracking number shadow the other's. The `None` scope is
exactly the case-level view.

**Pinned names are always present.** They are reserved before the budget is
spent on anything else. A pinned fact trimmed by a budget is precisely the
failure the pin exists to prevent -- the model reasoning without the one fact
the operator was certain it had seen.

**The persisted summary is consumed, never regenerated.** Compaction is a
separate write-once step. If this function could generate a summary, the
context would become a function of *when* it was assembled, and the first rule
would be false. So the `context_summary` fact is read, included, and its own
fact id recorded; nothing here writes one.

Compaction never discards a fact. What a budget can do is leave a fact out of
*this rendering*, and every such fact is named in `omitted_fact_ids` -- the
record itself is untouched, and the caller can see exactly what the model did
not get. `consumed_fact_ids` is the other half, recorded per invocation as the
contract requires.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final, Protocol

from return_platform.operations.fact_names import CONTEXT_SUMMARY

#: One million is 1.0, the codebase's convention for exact released fractions.
MILLIONTHS: Final = 1_000_000


class CompactionPolicy(Protocol):
    """When a compaction summary should be asked for."""

    @property
    def trigger_fraction_millionths(self) -> int: ...

    @property
    def summary_task_id(self) -> str: ...


class ContextPolicy(Protocol):
    """What this function needs of a policy -- its shape, not its class.

    `ContextAssemblyConfiguration` satisfies this, and that is how the released
    block reaches here. The dependency is deliberately structural: `platform/*`
    names no type a domain module owns (design doc sect. 13.1, rule R2a), and
    the rule is right independently of the test that enforces it -- a pure
    function over facts and a policy should not know that policies come from a
    release, or it stops being usable anywhere else.
    """

    @property
    def pinned_fact_names(self) -> tuple[str, ...]: ...

    @property
    def token_budget(self) -> int: ...

    @property
    def tokenizer_version(self) -> str: ...

    @property
    def compaction(self) -> CompactionPolicy: ...


class UnknownTokenizerError(ValueError):
    """The pinned tokenizer version has no estimator here.

    Refused rather than approximated. A pin whose unknown values silently fall
    back to some other estimator is not a pin -- it is a default with extra
    steps, and the budget it produces is measured in units nobody declared.
    """

    def __init__(self, tokenizer_version: str) -> None:
        super().__init__(
            f"no token estimator for pinned tokenizer version {tokenizer_version!r}; "
            f"known versions: {', '.join(sorted(TOKEN_ESTIMATORS))}"
        )
        self.tokenizer_version = tokenizer_version


def _wordpiece_approx_v1(text: str) -> int:
    """A deterministic, tokenizer-free estimate.

    Four characters to a token is the ratio that holds well enough across
    English prose and the short identifier-shaped strings facts are mostly made
    of. It is an *estimate*, and named as one: the version string is what makes
    replacing it with a real tokenizer a released change rather than a silent
    one, since every context assembled under the old name keeps the old
    measurement.
    """
    return (len(text) + 3) // 4


#: Version string -> estimator. Adding a version is how the measurement
#: changes; mutating an existing one would re-measure every context already
#: assembled under that name.
TOKEN_ESTIMATORS: Final[Mapping[str, Callable[[str], int]]] = {
    "wordpiece-approx.v1": _wordpiece_approx_v1,
}


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """One fact as the model will see it."""

    fact_id: str
    fact_name: str
    record_scope: str | None
    value: Any
    recorded_at: datetime
    tokens: int

    def rendered(self) -> dict[str, Any]:
        """The canonical serialization. What the hash is taken over."""
        return {
            "factId": self.fact_id,
            "factName": self.fact_name,
            "recordScope": self.record_scope,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class AssembledContext:
    """The assembled context, and everything needed to audit how it was cut."""

    entries: tuple[ContextEntry, ...]
    #: The persisted compaction summary, if the case holds one. Consumed as-is.
    summary: Any = None
    summary_fact_id: str | None = None
    consumed_fact_ids: tuple[str, ...] = ()
    #: Facts the projection selected but the budget could not fit. Named, never
    #: dropped from the record -- compaction discards nothing.
    omitted_fact_ids: tuple[str, ...] = ()
    estimated_tokens: int = 0
    token_budget: int = 0
    tokenizer_version: str = ""
    pinned_fact_names: tuple[str, ...] = ()
    #: True once the used fraction crosses the configured trigger, or once
    #: anything had to be omitted. The caller schedules the write-once summary
    #: step; this function never writes one.
    compaction_recommended: bool = False
    content_hash: str = ""

    def payload(self) -> dict[str, Any]:
        """The bytes a prompt is built from, in canonical order."""
        return {
            "summary": self.summary,
            "facts": [entry.rendered() for entry in self.entries],
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _fact_field(fact: Mapping[str, Any], *names: str) -> Any:
    """Read a field under either spelling.

    Facts reach this function both as stored documents (`factName`) and as
    `CaseFactView` dumps, and a pure function that only understood one of them
    would work in tests and fail on the path that matters.
    """
    for name in names:
        if name in fact:
            return fact[name]
    return None


def _sort_key(fact: Mapping[str, Any]) -> tuple[str, str]:
    recorded = _fact_field(fact, "recordedAt", "recorded_at")
    return (
        recorded.isoformat() if isinstance(recorded, datetime) else str(recorded),
        str(_fact_field(fact, "factId", "fact_id")),
    )


def assemble_case_context(
    facts: Iterable[Mapping[str, Any]],
    policy: ContextPolicy,
    *,
    extra_pinned_fact_names: Sequence[str] = (),
) -> AssembledContext:
    """Project, order, budget and hash a case's facts. Pure.

    `facts` is the case's fact log -- the whole log, not a projection: the
    scoped-latest collapse happens here, so that the ordering rule and the
    projection rule are applied by the same code in the same order every time.

    `extra_pinned_fact_names` is for a caller that knows something the release
    cannot -- a resolver pinning the fact it is currently reasoning about. It
    adds to the configured names and never replaces them.
    """
    estimator = TOKEN_ESTIMATORS.get(policy.tokenizer_version)
    if estimator is None:
        raise UnknownTokenizerError(policy.tokenizer_version)

    ordered = sorted(facts, key=_sort_key)

    # The persisted summary: consumed, never regenerated. The newest one wins,
    # by the same canonical order as everything else.
    summary_fact: Mapping[str, Any] | None = None
    remaining: list[Mapping[str, Any]] = []
    for fact in ordered:
        if str(_fact_field(fact, "factName", "fact_name")) == CONTEXT_SUMMARY:
            summary_fact = fact
        else:
            remaining.append(fact)

    # Scoped-latest projection. `ordered` is ascending, so the last write per
    # key is the newest, and a plain overwrite is the projection.
    latest: dict[tuple[str | None, str], Mapping[str, Any]] = {}
    for fact in remaining:
        scope = _fact_field(fact, "record_scope", "recordScope")
        key = (
            str(scope) if scope is not None else None,
            str(_fact_field(fact, "factName", "fact_name")),
        )
        latest[key] = fact

    pinned_names = tuple(dict.fromkeys((*policy.pinned_fact_names, *extra_pinned_fact_names)))
    projected = sorted(latest.values(), key=_sort_key)

    entries: list[ContextEntry] = []
    for fact in projected:
        scope_value = _fact_field(fact, "record_scope", "recordScope")
        entry = ContextEntry(
            fact_id=str(_fact_field(fact, "factId", "fact_id")),
            fact_name=str(_fact_field(fact, "factName", "fact_name")),
            record_scope=None if scope_value is None else str(scope_value),
            value=_fact_field(fact, "value"),
            recorded_at=_fact_field(fact, "recordedAt", "recorded_at"),
            tokens=0,
        )
        # Measured over the same bytes the payload will carry, so the budget
        # counts what the model actually receives rather than an approximation
        # of it that happens to be nearby.
        entries.append(replace(entry, tokens=estimator(_canonical_json(entry.rendered()))))

    summary_value = _fact_field(summary_fact, "value") if summary_fact is not None else None
    summary_tokens = estimator(_canonical_json(summary_value)) if summary_fact is not None else 0

    # Pinned first, then the rest newest-first: when something has to go, the
    # oldest unpinned fact is the one whose absence costs least, and the
    # summary is what stands in for it.
    budget = policy.token_budget
    used = summary_tokens
    kept: dict[str, ContextEntry] = {}
    omitted: list[str] = []

    for entry in entries:
        if entry.fact_name in pinned_names:
            kept[entry.fact_id] = entry
            used += entry.tokens

    for entry in reversed(entries):
        if entry.fact_id in kept:
            continue
        if used + entry.tokens > budget:
            omitted.append(entry.fact_id)
            continue
        kept[entry.fact_id] = entry
        used += entry.tokens

    # Emitted in canonical order, whatever order they were admitted in.
    selected = tuple(entry for entry in entries if entry.fact_id in kept)
    consumed = tuple(entry.fact_id for entry in selected)
    if summary_fact is not None:
        consumed = (str(_fact_field(summary_fact, "factId", "fact_id")), *consumed)

    trigger = (budget * policy.compaction.trigger_fraction_millionths) // MILLIONTHS
    assembled = AssembledContext(
        entries=selected,
        summary=summary_value,
        summary_fact_id=(
            None if summary_fact is None else str(_fact_field(summary_fact, "factId", "fact_id"))
        ),
        consumed_fact_ids=consumed,
        omitted_fact_ids=tuple(entry.fact_id for entry in entries if entry.fact_id in set(omitted)),
        estimated_tokens=used,
        token_budget=budget,
        tokenizer_version=policy.tokenizer_version,
        pinned_fact_names=pinned_names,
        compaction_recommended=bool(omitted) or used >= trigger,
        content_hash="",
    )
    # The hash is over the finished payload -- the bytes the prompt is built
    # from, and nothing else. Two contexts with the same facts and the same
    # policy hash alike even if they were assembled a day apart.
    digest = hashlib.sha256(_canonical_json(assembled.payload()).encode("utf-8")).hexdigest()
    return replace(assembled, content_hash=digest)


__all__ = [
    "MILLIONTHS",
    "TOKEN_ESTIMATORS",
    "AssembledContext",
    "CompactionPolicy",
    "ContextEntry",
    "ContextPolicy",
    "UnknownTokenizerError",
    "assemble_case_context",
]
