"""Acceptance item 21 — byte-identical `assemble_case_context` **across a restart**.

`tests/platform/test_case_context_assembly.py` already asserts determinism
thoroughly, and this module does not repeat it: identical inputs hash the same,
input order does not reach the output, ties break on `factId`, and the tokenizer
version is refused rather than approximated when unknown. Twenty-one tests.

**All of them run in one process.** That is the gap item 21 names, and it is not
a quibble: every in-process determinism check shares one `PYTHONHASHSEED`, so a
result that depended on `str` hash randomisation — a `set` iterated into the
output, a `dict` built from an unordered comparison, anything reached through
`id()` — is *identical on both sides of the comparison* and invisible. A kill and
restart is precisely a new seed.

So this module assembles the same fact log in **two fresh interpreters with
deliberately different `PYTHONHASHSEED` values** and requires the same hash and
the same payload bytes. That is what "byte-identical across kill/restart" means
for a pure function whose whole contract is that a replay reproduces it.

**The tokenizer is pinned by the same run**, per item 21's second clause: each
child reports the `tokenizer_version` its estimate was produced under, and the
two must agree — a context that hashed the same under two different estimators
would be reporting a version it did not use.

**The gate that runs it** (RV rule 13): normal suite, no `live_infra` marker,
so `.github/workflows/checks.yml`'s backend job runs it on every push. The
subprocesses use `sys.executable` and inherit `PYTHONPATH`, so they are the same
interpreter and the same source tree CI is testing — not an installed copy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]

#: Two seeds, both fixed. Not `random`: a flake that appears one run in fifty is
#: worse than no test, and the property under test does not need a search --
#: **any** two different seeds expose a hash-order dependence, because the
#: randomisation applies to the same strings both times.
_SEEDS = ("0", "12345")

#: The fact log, as JSON so it crosses the process boundary byte-for-byte
#: rather than being rebuilt by each child from code that could drift.
#:
#: Chosen to exercise what a seed can reach: names that collide in no obvious
#: order, two record scopes, a shared instant that must tie-break on `factId`,
#: a superseded fact, and a pinned name.
_FACTS = [
    {
        "factId": "f-03",
        "factName": "carrier",
        "value": "FEDEX",
        "recordedAt": "2026-08-31T09:02:00+00:00",
        "record_scope": None,
    },
    {
        "factId": "f-01",
        "factName": "return_reason",
        "value": "damaged",
        "recordedAt": "2026-08-31T09:01:00+00:00",
        "record_scope": None,
    },
    {
        "factId": "f-02",
        "factName": "carrier",
        "value": "UPS",
        "recordedAt": "2026-08-31T09:01:00+00:00",
        "record_scope": None,
    },
    {
        "factId": "f-05",
        "factName": "tracking",
        "value": "1Z-BBB",
        "recordedAt": "2026-08-31T09:03:00+00:00",
        "record_scope": "rr-2",
    },
    {
        "factId": "f-04",
        "factName": "tracking",
        "value": "1Z-AAA",
        "recordedAt": "2026-08-31T09:03:00+00:00",
        "record_scope": "rr-1",
    },
    {
        "factId": "f-06",
        "factName": "return_reason",
        "value": "wrong size",
        "recordedAt": "2026-08-31T09:04:00+00:00",
        "record_scope": None,
    },
]

_PINNED = ("return_reason",)

#: Small enough that the budget genuinely evicts. Verified rather than guessed:
#: at 120 nothing is omitted (the projection alone accounts for the drop from
#: six facts to four), at 60 exactly one unpinned fact goes and the pinned name
#: survives. The test asserts the omission rather than trusting this number.
_SQUEEZING_BUDGET = 60

#: Run in the child. Deliberately tiny and deliberately *not* importing the test
#: module: the point is a cold interpreter that shares nothing with this one.
_CHILD = """
import json, sys
from return_platform.configuration.context_assembly_configuration import (
    ContextAssemblyConfiguration,
)
from return_platform.platform.reasoning.case_context import assemble_case_context

payload = json.loads(sys.stdin.read())
policy = ContextAssemblyConfiguration(
    pinned_fact_names=tuple(payload["pinned"]),
    token_budget=payload["budget"],
    tokenizer_version=payload["tokenizer"],
)
assembled = assemble_case_context(payload["facts"], policy)
sys.stdout.write(
    json.dumps(
        {
            "hash": assembled.content_hash,
            "payload": assembled.payload(),
            "consumed_fact_ids": list(assembled.consumed_fact_ids),
            "omitted_fact_ids": list(assembled.omitted_fact_ids),
            "tokenizer_version": assembled.tokenizer_version,
            "seed": os.environ.get("PYTHONHASHSEED"),
        },
        sort_keys=True,
    )
)
"""


def _assemble_in_a_fresh_interpreter(seed: str, *, budget: int, tokenizer: str) -> dict:
    environment = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(_BACKEND / "src")}
    completed = subprocess.run(  # noqa: S603 - argv is built here
        [sys.executable, "-c", "import os\n" + _CHILD],
        input=json.dumps(
            {"facts": _FACTS, "pinned": _PINNED, "budget": budget, "tokenizer": tokenizer}
        ),
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(_BACKEND),
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"the child interpreter failed under PYTHONHASHSEED={seed}:\n{completed.stderr}"
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def tokenizer_version() -> str:
    """The version the shipped release pins, read from it rather than typed.

    A literal here would keep passing after a release changed the estimator,
    which is the one thing item 21's second clause is about.
    """
    from return_platform.configuration.return_configuration import load_return_configuration
    from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH

    released = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration
    return released.context_assembly.tokenizer_version


class TestTheContextSurvivesANewInterpreter:
    def test_two_interpreters_with_different_hash_seeds_agree_byte_for_byte(
        self, tokenizer_version: str
    ) -> None:
        """Item 21. A restart is a new seed, and the bytes must not notice.

        Budget deliberately generous, so nothing is omitted and the comparison
        is over the whole projection rather than over whatever survived a
        squeeze. The squeezed case is the next test.
        """
        first, second = (
            _assemble_in_a_fresh_interpreter(seed, budget=100_000, tokenizer=tokenizer_version)
            for seed in _SEEDS
        )

        assert first["seed"] != second["seed"], (
            "both children ran under the same PYTHONHASHSEED, so this compared a "
            "process with itself -- exactly the in-process check this module exists "
            "to go beyond"
        )
        assert first["hash"] == second["hash"]
        assert first["payload"] == second["payload"]
        # The bytes, not just the structures: `payload()` feeds a canonical
        # serialisation and two dicts can compare equal while serialising apart.
        assert json.dumps(first["payload"], sort_keys=True) == json.dumps(
            second["payload"], sort_keys=True
        )

    def test_the_agreement_holds_where_the_budget_has_to_choose(
        self, tokenizer_version: str
    ) -> None:
        """The interesting half: a restart must evict the same facts.

        Under a budget that fits only some of the log, the assembly makes
        *choices* -- pinned first, then newest-first. A choice reached through
        anything seed-dependent would diverge here and nowhere else, because the
        generous-budget case keeps everything and cannot tell two orderings
        apart.
        """
        first, second = (
            _assemble_in_a_fresh_interpreter(
                seed, budget=_SQUEEZING_BUDGET, tokenizer=tokenizer_version
            )
            for seed in _SEEDS
        )

        # **`omitted_fact_ids`, not a count of what was kept.** The first form of
        # this guard was `len(consumed) < len(_FACTS)`, and it passed at a budget
        # that omitted nothing: the scoped-latest projection already drops the
        # two superseded facts, so four of six were kept for a reason that has
        # nothing to do with the budget. Measured -- at budget 120 the omitted
        # list is empty and the assertion was green, which made this test the
        # previous one wearing a smaller number. Only an omission proves a
        # squeeze.
        assert first["omitted_fact_ids"], (
            "nothing was omitted, so the budget did not bite and this test is the "
            f"generous-budget case again. consumed={first['consumed_fact_ids']}"
        )
        assert first["consumed_fact_ids"], "everything was omitted; that is not a squeeze either"
        assert first["hash"] == second["hash"]
        assert first["payload"] == second["payload"]
        assert first["consumed_fact_ids"] == second["consumed_fact_ids"]
        assert first["omitted_fact_ids"] == second["omitted_fact_ids"], (
            "the two interpreters evicted different facts -- the choice the budget "
            "makes is reachable from something that changes across a restart"
        )

    def test_both_runs_report_the_tokenizer_they_actually_used(
        self, tokenizer_version: str
    ) -> None:
        """Item 21's second clause. The pin travels with the estimate.

        Asserted against the **released** version rather than a literal, so a
        release that changed the estimator without changing what the context
        reports is a failure here rather than a silent re-measure.
        """
        first, second = (
            _assemble_in_a_fresh_interpreter(seed, budget=100_000, tokenizer=tokenizer_version)
            for seed in _SEEDS
        )
        for result in (first, second):
            assert result["tokenizer_version"] == tokenizer_version
