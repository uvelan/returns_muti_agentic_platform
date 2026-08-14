"""Masking and scope policy, as contracts the host supplies.

`ports/__init__.py` has always claimed that "every dependency on anything
outside this module is declared here as a Protocol". Two were not. The
application layer imported `platform.redaction.SampleMasker` and
`platform.redaction.AllowlistRedactor` concretely, and -- more limiting than the
import itself -- *constructed* them, in four places. A host composing this
analyzer could supply its own sources, its own graph target, its own AI gateway
and its own persistence, and still had no way to supply its own masking or its
own retention scope: it got this platform's, or it patched module internals.

Both are policy, not mechanism, which is exactly the kind of thing a second
application has to be able to decide for itself. What counts as sensitive, how a
value is tokenized, and which fields may be retained at all are answers that
belong to whoever is running the analyzer over their own data.

**Why a factory as well as an instance.** A masker carries a salt for the
lifetime of one analysis, deliberately: the same customer id must tokenize to
the same value across every object read in that analysis, or the joins the
analyzer exists to find become invisible; and it must tokenize differently in
the next analysis, so tokens carry no meaning between them. A port that only
handed over one instance would either leak salt across analyses or make the
lifetime the caller's problem. The factory is what preserves that semantic
across a host boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "PayloadRedactionPort",
    "RedactionPolicyFactory",
    "SampleMaskerFactory",
    "SampleMaskingPort",
]


@runtime_checkable
class SampleMaskingPort(Protocol):
    """Masks sensitive values in sampled source rows.

    Implementations must be stable within their own lifetime -- the same input
    value masks to the same token every time -- and must not drop or reorder
    rows. Row count is itself structural: `ObjectProfile.sampled_rows` reports
    it, so a masker that filtered would make two statements about one read
    disagree.
    """

    def mask_row(self, row: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def mask_rows(self, rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]: ...


class SampleMaskerFactory(Protocol):
    """Produces one masker per analysis. See the module docstring on salt lifetime."""

    def __call__(self) -> SampleMaskingPort: ...


@runtime_checkable
class PayloadRedactionPort(Protocol):
    """Narrows a payload to what policy allows to be retained.

    Fail-closed is part of the contract, not an implementation detail: a field
    absent from the policy must not be emitted, so a newly-appearing source
    field is withheld until someone decides otherwise rather than being
    persisted because nobody had ruled on it yet.
    """

    def redact(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class RedactionPolicyFactory(Protocol):
    """Builds a redactor for one allowlist.

    Takes the allowed field names rather than a policy object, because the
    allowlist is the only part of the retention policy this contract needs and
    passing the whole policy would couple the host to the analyzer's own
    configuration model.
    """

    def __call__(self, allowed_fields: frozenset[str]) -> PayloadRedactionPort: ...
