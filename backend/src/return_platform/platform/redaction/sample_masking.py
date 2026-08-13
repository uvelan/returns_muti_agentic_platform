"""Masking that removes the value and keeps everything the shape depends on.

`ai.gateway.redaction` answers the provider-boundary question -- a sensitive
scalar becomes the constant `[REDACTED]` -- and that answer is right there and
wrong here. A schema analysis exists to infer structure from data: which fields
identify a row, which field of one object joins to which field of another, how
many distinct values a column holds. Every one of those inferences is drawn from
*value equality across rows*, and a constant makes every masked value equal to
every other. Masking with `[REDACTED]` would leave the analyzer looking at a
table whose customer id is the same for all fifty sampled rows, and it would
conclude the column is a constant rather than a key.

So the surrogate is deterministic instead of constant: equal inputs produce equal
tokens, unequal inputs produce unequal ones. That preserves exactly the signal
the analyzer reads and nothing else --

* **Field names** are never touched. They are metadata, they are what the model
  reasons over first, and they are already visible through `describe_object`.
* **Shape** survives: dicts stay dicts with the same keys, lists stay lists of
  the same length, a scalar stays a scalar.
* **Cardinality** survives, within and across objects. `sales_order.customer_id`
  and `customer.customer_id` holding the same underlying value mask to the same
  token, which is what keeps a foreign key inferable.
* **Distribution metadata** survives as a stated fact rather than as content: the
  surrogate carries the original type and size, so "these are 8-to-12 character
  strings" is still legible while none of the characters are.

**The surrogate never looks like real data.** A mask that produced a plausible
value would be worse than no mask: nothing downstream -- a prompt log, an
exported sample, a human reading a trace -- could tell it from the real thing,
and a "masked" record indistinguishable from an unmasked one will eventually be
treated as unmasked. `[MASKED:str:8:9f31c0aa4d72]` is unmistakable at a glance.
That is why the surrogate is always a string even for an integer input, with the
original type named inside it: rewriting a card number into a different 16-digit
integer would preserve the JSON type and produce something that reads as a card
number.

**The token is salted per masker and the salt never leaves the process.** Without
one, the token is an unsalted hash of the value and anyone holding the output can
confirm a guess -- "is this row Jane Doe?" -- by hashing the name. With a random
per-instance salt, the tokens are stable for exactly as long as they need to be
(one analysis, so cross-object joins line up) and meaningless afterwards.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from hashlib import blake2s
from typing import Any, Final

from return_platform.platform.redaction.sensitive_keys import is_sensitive_key

__all__ = ["MASK_PREFIX", "SampleMasker"]

MASK_PREFIX: Final = "[MASKED:"

# Hex characters of the digest kept in a surrogate. Twelve is 48 bits: collisions
# within a bounded sample (the analyzer caps at 100 rows) are not a practical
# concern, and a shorter token keeps the prompt cheap.
_TOKEN_LENGTH: Final = 12

# Nested JSON reached through a document field cannot drive unbounded recursion.
# Matched to `ai.gateway.redaction` so a payload accepted by one boundary is not
# silently truncated differently by the other.
_MAX_DEPTH: Final = 12


class SampleMasker:
    """Masks sensitive values in sampled rows, one salt per instance.

    Construct one per analysis and reuse it for every object read: a masker built
    per call would give the same customer id a different token in each object,
    and the join the analyzer is looking for would be invisible. Construct a new
    one for a new analysis, so tokens carry no meaning between them.
    """

    __slots__ = ("_salt",)

    def __init__(self, *, salt: bytes | None = None) -> None:
        self._salt = secrets.token_bytes(16) if salt is None else salt

    def mask_rows(self, rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        """Every row, masked, in the order given and without dropping any.

        Row count is itself structural -- it is the basis `ObjectProfile`
        reports as `sampled_rows` -- so a masker that filtered rows would make
        two statements about the same read disagree.
        """
        return tuple(self.mask_row(row) for row in rows)

    def mask_row(self, row: Mapping[str, Any]) -> Mapping[str, Any]:
        masked = self._mask(dict(row), depth=0, sensitive=False)
        # `_mask` returns what it was given for a dict; assert rather than cast so
        # a later change cannot quietly start returning a scalar for a row.
        assert isinstance(masked, dict)
        return masked

    def _mask(self, value: Any, *, depth: int, sensitive: bool) -> Any:
        if depth > _MAX_DEPTH:
            # Fail closed, regardless of the key. Past the depth bound this
            # cannot tell whether it is under a sensitive key any more, and the
            # one wrong answer here is "not sensitive" on a value that was.
            return self._surrogate(value)
        if isinstance(value, Mapping):
            # A sensitive key holding a container marks everything under it: an
            # `address` object's `line1` and `postcode` are the address, and a
            # rule that only looked at the key it found would let the value
            # through one level down. The gateway redactor leaves containers
            # alone because `compact_schema` puts *metadata* under names like
            # `customer_name`; a source sample has no such convention -- every
            # key here names a field of the data itself.
            return {
                str(key): self._mask(
                    item, depth=depth + 1, sensitive=sensitive or is_sensitive_key(str(key))
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._mask(item, depth=depth + 1, sensitive=sensitive) for item in value]
        if not sensitive:
            return value
        return self._surrogate(value)

    def _surrogate(self, value: Any) -> Any:
        """The masked stand-in for one sensitive scalar.

        `None` and `bool` pass through. Absence is not sensitive, and masking it
        would tell the model a value exists where none does -- the same rule the
        provider boundary already applies. A boolean identifies nobody and holds
        one bit; replacing it would remove the only structural signal it carries
        and disclose nothing in exchange.

        **Masking is idempotent**, and that is load-bearing rather than tidy. A
        sample can reach a model by more than one route, and the safe way to
        cover them is to mask at each. Without this check the second pass would
        mask the first pass's surrogate -- every field would come out
        `str` of the same length, so the type and size the surrogate exists to
        preserve would be the *surrogate's* type and size, and the distribution
        metadata would be destroyed by the very mechanism meant to keep it.
        """
        if value is None or isinstance(value, bool) or _is_surrogate(value):
            return value
        type_name = type(value).__name__
        return f"{MASK_PREFIX}{type_name}:{_size_of(value)}:{self._token(type_name, value)}]"

    def _token(self, type_name: str, value: Any) -> str:
        digest = blake2s(digest_size=_TOKEN_LENGTH // 2)
        digest.update(self._salt)
        # The type name is hashed as well as reported, so the string "1" and the
        # integer 1 are two values rather than one. They are two values to the
        # source, and collapsing them would report a cardinality the data does
        # not have.
        digest.update(f"{type_name}:{value!r}".encode())
        return digest.hexdigest()


def _is_surrogate(value: Any) -> bool:
    """Whether this value is already something this module produced.

    Recognised by shape rather than by remembering what was masked, so it holds
    for a surrogate that arrived from another `SampleMasker` -- across a process
    boundary, or from a row that was masked at one boundary and re-checked at the
    next. A genuine source value that happens to be spelled `[MASKED:...]` would
    pass through unmasked; that is a string an attacker would have to have
    written into the source already, and the alternative is a mask that corrupts
    its own output on the common path to defend against the absurd one.
    """
    return isinstance(value, str) and value.startswith(MASK_PREFIX) and value.endswith("]")


def _size_of(value: Any) -> int:
    """The size class kept alongside the type: characters, bytes, or digits.

    Reported because it is distribution metadata the analyzer legitimately reads
    -- a 5-character code column and a 40-character free-text one are different
    fields even when both are masked -- and because it is a property of the
    shape rather than of the person. Anything without a natural size reports 0
    rather than a number invented for it.
    """
    if isinstance(value, (str, bytes, bytearray)):
        return len(value)
    if isinstance(value, int):
        return len(str(abs(value)))
    return 0
