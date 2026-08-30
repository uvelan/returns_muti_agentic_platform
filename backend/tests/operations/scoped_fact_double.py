"""The **one** scoped-fact double, bound to the signature that receives it.

A sibling of `tests/operations/mongo_double.py`, and here for the reason that
one is: a double two files each keep their own copy of is a double whose
discipline only one of them has.

**The history is the argument.** V1 phase 2 shipped five gate fact writes that
could not have run — `acquisition` where the repository takes
`acquisition_method`, `occurred_at` where it takes `observed_at`, an `actor_id`
that was not a parameter, and the **required** `agent_id` never sent. Every one
would have raised `TypeError` the first time a worker executed it, and
twenty-four green tests could not see it because the double took `**fact` and
recorded whatever it was handed. **The thing being exercised was the double.**

Four of the five were then pinned by a strict double in the gate's own suite —
and the fifth, `support_template_gap`, still was not, because the only test that
reaches that loop lives in a *different* file which had kept the permissive
copy. RV proved it: dropping the required `agent_id` from that one write left
all 51 tests green. So the fix is not another strict double. It is one double,
in one place, that both files import.

What it does, and it is exactly what production does: pop `fact_id`, prefix the
`record_scope` into it the way `append_scoped_fact_once` does, then **bind** the
rest against `OperationalRepository.append_scoped_case_fact`'s real signature.
A renamed, missing or invented parameter fails here rather than in a worker.
"""

from __future__ import annotations

import inspect
from typing import Any

from return_platform.operations.repository import OperationalRepository

__all__ = ["ScopedFactDouble"]


class ScopedFactDouble:
    """Records what was written, and refuses what the repository would refuse."""

    _SIGNATURE = inspect.signature(OperationalRepository.append_scoped_case_fact)

    def __init__(self) -> None:
        self.written: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        record_scope: str | None = None,
        # **Explicit, and not left to ride inside `**fact`.** S1's
        # `ScopedFactAppendPort` is `(*, record_scope, **fact)`, so an
        # `actor_id` kwarg type-checks *through* the port whether or not
        # anything downstream has such a parameter -- which is precisely how
        # this slice's original defect passed a type checker. Naming it here
        # means the double, like the repository, has an opinion about it.
        actor_id: str | None = None,
        **fact: Any,
    ) -> bool:
        derived = str(fact.pop("fact_id"))
        if record_scope is not None:
            derived = f"{derived}::{record_scope}"
        # `self` is bound in production; a placeholder keeps the bind about the
        # keyword arguments, which are what a caller actually controls.
        self._SIGNATURE.bind(
            None,
            fact_id=derived,
            record_scope=record_scope,
            identity_version=1,
            actor_id=actor_id,
            **fact,
        )
        self.written.append(
            {
                "fact_id": derived,
                "record_scope": record_scope,
                "actor_id": actor_id,
                **fact,
            }
        )
        return True

    def named(self, name: str) -> list[dict[str, Any]]:
        return [fact for fact in self.written if fact.get("fact_name") == name]

    def stored(self, name: str) -> list[dict[str, Any]]:
        """One write, in the shape the **document** takes.

        `actor_id` is the parameter; **`actorId` is the stored key**
        (`case_repository.py:450`), and they are not the same question. A test
        asserting the parameter would pass against a repository that accepted
        the argument and dropped it on the floor, which is the failure mode
        that matters for an audit field: the endpoint looks fine and the fact
        log cannot say who decided.
        """
        return [
            {
                **{key: value for key, value in fact.items() if key != "actor_id"},
                "actorId": fact.get("actor_id"),
            }
            for fact in self.named(name)
        ]
