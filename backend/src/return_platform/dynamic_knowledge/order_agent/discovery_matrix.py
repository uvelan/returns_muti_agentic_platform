"""The fifteen cases Order Discovery must pass before a release.

The audit could not exercise Order Discovery at all: every model attempt timed
out, so the primary user journey went unverified and the release verdict rested
on a surface that had never been shown to work. This declares what "works" means
for it, as data rather than as prose, so the matrix can be counted by a gate
instead of read by a person.

**Why a declaration and not just a test module.** Two of these cases are about
*configuration* -- every runtime-configured identification field must be
exercised, and a configured identifier with no seeded value must be handled --
so the matrix cannot be a fixed list of test functions without going stale the
moment an operator adds a field. The list below names the cases; the field-level
expansion is derived from `IdentificationCatalogue.intent_keys` at test time.

**Closure means one case and one workflow start, not one HTTP 200.** Several of
these -- repeated confirmation, concurrent confirmation, retry after a client
timeout that already committed -- are indistinguishable from success if you only
look at the response. They are the reason the matrix exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class Evidence(Enum):
    """What a case has to prove, which decides where it can run."""

    #: Derivable from configuration. Runs in the normal suite.
    CONFIGURATION = "configuration"
    #: Needs the graph and the API. Runs against real infrastructure.
    STACK = "stack"
    #: Needs a live model route as well. Blocked until one exists.
    MODEL_ROUTE = "model-route"


@dataclass(frozen=True, slots=True)
class MatrixCase:
    id: str
    description: str
    evidence: Evidence
    #: What distinguishes passing from a response that merely looks right.
    proves: str


MATRIX: Final[tuple[MatrixCase, ...]] = (
    MatrixCase(
        id="every-configured-identification-field",
        description="Every runtime-configured identification field is exercised",
        evidence=Evidence.CONFIGURATION,
        proves=(
            "The set is read from `discovery.identification_fields` at test time, "
            "so adding a tenth field extends the matrix instead of silently "
            "leaving it untested."
        ),
    ),
    MatrixCase(
        id="exact-match-resolution",
        description="An exact identifier resolves to one candidate",
        evidence=Evidence.STACK,
        proves="The happy path, and the baseline the ambiguous cases are read against.",
    ),
    MatrixCase(
        id="ambiguous-resolution",
        description="An ambiguous identifier returns every candidate, ranked",
        evidence=Evidence.STACK,
        proves="Ambiguity is surfaced rather than resolved by picking the first row.",
    ),
    MatrixCase(
        id="partial-customer-name-confirms",
        description="A partial customer name requires confirmation",
        evidence=Evidence.STACK,
        proves="A partial match never auto-confirms into a case.",
    ),
    MatrixCase(
        id="partial-product-name-confirms",
        description="A partial product name requires confirmation",
        evidence=Evidence.STACK,
        proves="Same, on the other axis operators actually type.",
    ),
    MatrixCase(
        id="initial-facts-retained-with-provenance",
        description="Facts given in the opening utterance are kept, with provenance, and never re-asked",
        evidence=Evidence.MODEL_ROUTE,
        proves=(
            "Re-asking for something the associate already said is the failure "
            "the audit saw most often, and provenance is what stops a remembered "
            "fact being presented as one the graph supplied."
        ),
    ),
    MatrixCase(
        id="confirmation-creates-exactly-one-case",
        description="Confirming an order produces exactly one case and one workflow start",
        evidence=Evidence.STACK,
        proves=(
            "Counted in the datastore and in Temporal, not inferred from a 201. "
            "This is the assertion the whole matrix is built around."
        ),
    ),
    MatrixCase(
        id="repeated-confirmation-same-key",
        description="Repeating a confirmation with the same idempotency key yields one outcome",
        evidence=Evidence.STACK,
        proves="A second identical request is answered, and creates nothing.",
    ),
    MatrixCase(
        id="concurrent-confirmation",
        description="Two concurrent confirmations of the same candidate yield one case",
        evidence=Evidence.STACK,
        proves=(
            "Two operators on the same call, or one double-click. Both get an "
            "answer; the datastore gets one case."
        ),
    ),
    MatrixCase(
        id="retry-after-client-timeout-that-committed",
        description="A retry after a client timeout whose first request had committed yields one case",
        evidence=Evidence.STACK,
        proves=(
            "The hardest of the three, because the client genuinely does not "
            "know whether the first attempt landed."
        ),
    ),
    MatrixCase(
        id="stale-candidate-set-confirmation",
        description="Confirming against a candidate set that has since moved is refused",
        evidence=Evidence.STACK,
        proves="The confirmation names a candidate the operator never saw, and is refused rather than guessed.",
    ),
    MatrixCase(
        id="confirmation-after-generation-change",
        description="Confirming after the active graph generation changed is refused or re-resolved",
        evidence=Evidence.STACK,
        proves=(
            "Candidate ids are generation-scoped. Silently confirming one from a "
            "retired generation is how a case gets attached to an order that no "
            "longer reads that way."
        ),
    ),
    MatrixCase(
        id="ambiguous-identifier-resolves-differently-after-refresh",
        description="An ambiguous partial identifier that resolves differently after a refresh is handled",
        evidence=Evidence.STACK,
        proves="The result set is pinned to what was shown, not re-derived at confirmation.",
    ),
    MatrixCase(
        id="configured-identifier-with-no-seeded-value",
        description="A configured identifier with no representative value in the corpus is reported, not crashed",
        evidence=Evidence.CONFIGURATION,
        proves=(
            "`IdentificationCatalogue.unresolved` already exists for this. A "
            "field naming an entity the active schema lacks must be visible at "
            "construction rather than discovered per turn."
        ),
    ),
    MatrixCase(
        id="conflicting-facts-in-one-utterance",
        description="Conflicting facts in a single utterance are surfaced, not silently reconciled",
        evidence=Evidence.MODEL_ROUTE,
        proves="Picking one of two contradictory facts without saying so is the worst available behaviour.",
    ),
)


def cases_for(evidence: Evidence) -> tuple[MatrixCase, ...]:
    return tuple(case for case in MATRIX if case.evidence is evidence)
