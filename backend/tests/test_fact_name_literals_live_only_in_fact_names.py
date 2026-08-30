"""A fact name is written once, in `operations/fact_names.py`, and imported everywhere else.

RV greps for this every review round (contracts.md sect. 3). A grep run by a
reviewer catches what that reviewer looks at; this catches what anybody merges,
which is the difference between a convention and a rule.

The cost of the literal is not tidiness. `fact_names.py` says it plainly: a
rename must be a one-line change, and a typo must be an import error rather
than a fact that is written under one spelling and read back under another --
i.e. a fact nobody ever reads back, discovered in production as an empty panel
section rather than in CI as a red test.

**The vocabulary is read from `fact_names.py` itself.** A hardcoded copy of the
list here would be a second home for exactly the strings this file exists to
keep in one home, and it would rot silently: a constant added in a later slice
would be unguarded, and the test would still pass. So the module is imported
and its constants are discovered. The consequence is deliberate -- appending a
constant there extends this guard in the same commit, with nothing to remember.

**AST, not text.** The ban in contracts.md sect. 4 is on *string literals*, and
prose is not a literal: `fact_names.py`'s own neighbours must stay free to
explain what `support_artifact_ambiguous` means in a docstring without tripping
a guard, or the rule starts costing explanation. So docstrings are excluded and
every other string constant is examined -- including ones a text grep would
miss shape of, such as a name sitting inside a `Literal[...]` annotation or a
dict key. `_fact_name_literals_in` is exercised against a source that does
carry a literal (below), because a scanner that finds nothing is
indistinguishable from a scanner that looks for nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from return_platform.operations import fact_names

BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"

#: The one file allowed to spell them. Resolved from the module rather than
#: written as a path, so moving the module moves the exemption with it.
FACT_NAMES_MODULE = Path(fact_names.__file__).resolve()


def _declared_fact_names() -> dict[str, str]:
    """`{constant name: fact name}` as `fact_names.py` currently declares them.

    Public upper-case strings, which is what the module is: a flat list of
    `NAME: Final[str] = "name"`. `Final` itself is not a string and drops out
    on its own, so nothing here needs to know which typing helpers the module
    happens to import.
    """
    return {
        name: value
        for name, value in vars(fact_names).items()
        if name.isupper() and not name.startswith("_") and isinstance(value, str)
    }


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """`id()` of every string constant that is a docstring.

    Prose about the vocabulary is not a use of the vocabulary. Matched by
    identity of the node rather than by value so that a module whose docstring
    and whose code both carry the same string still has only the docstring
    forgiven.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    return docstrings


def _fact_name_literals_in(source: str, known: set[str]) -> set[str]:
    """Every fact name that appears as a string literal in `source`, docstrings aside."""
    tree = ast.parse(source)
    exempt = _docstring_nodes(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in exempt:
            continue
        if node.value in known:
            found.add(node.value)
    return found


def _source_modules() -> list[Path]:
    return sorted(
        path
        for path in BACKEND_SRC.rglob("*.py")
        if "__pycache__" not in path.parts and path.resolve() != FACT_NAMES_MODULE
    )


def test_the_vocabulary_is_discovered_rather_than_copied() -> None:
    """A guard over an empty vocabulary passes forever while enforcing nothing.

    Not an assertion about *which* names exist -- naming them would be the
    hardcoded copy this file refuses to keep. Only that there are some, so an
    import that silently resolved to an empty module fails here instead of
    turning every assertion below into a tautology.
    """
    declared = _declared_fact_names()

    assert declared, (
        "no fact-name constants were discovered in "
        f"{FACT_NAMES_MODULE.name} -- either the module moved and this guard is "
        "now scanning for nothing, or the declaration shape changed from "
        "`NAME: Final[str] = \"name\"`"
    )
    assert all(declared.values()), f"a fact-name constant is empty: {declared}"


def test_the_scanner_finds_a_literal_where_one_exists() -> None:
    """The detector is proved against a violation, not only against a clean tree.

    Everything else here asserts an absence, and an absence is what a broken
    scanner reports too. This is the one place a fact name is written as a
    literal on purpose -- it is a test fixture, in `tests/`, and the ban is on
    `backend/src/`.
    """
    declared = _declared_fact_names()
    name, value = next(iter(declared.items()))
    known = set(declared.values())

    offending = f'"""A docstring naming {value} in prose."""\nWRITTEN = {value!r}\n'
    assert _fact_name_literals_in(offending, known) == {value}, (
        f"the scanner failed to see {name} written as a literal"
    )

    forgiven = f'"""A docstring naming {value} in prose."""\n'
    assert _fact_name_literals_in(forgiven, known) == set(), (
        "prose in a docstring was reported as a literal use"
    )

    importing = f"from return_platform.operations.fact_names import {name}\nWRITTEN = {name}\n"
    assert _fact_name_literals_in(importing, known) == set(), (
        "the sanctioned form -- importing the constant -- was reported as a violation"
    )


def test_no_module_under_src_writes_a_fact_name_as_a_string_literal() -> None:
    """The rule itself (contracts.md sect. 4)."""
    known = set(_declared_fact_names().values())
    offenders: dict[str, list[str]] = {}

    for path in _source_modules():
        found = _fact_name_literals_in(path.read_text(encoding="utf-8"), known)
        if found:
            offenders[path.relative_to(BACKEND_SRC).as_posix()] = sorted(found)

    assert not offenders, (
        "these modules write a fact name as a string literal instead of importing "
        f"the constant from {FACT_NAMES_MODULE.name}: {offenders}. Import the "
        "constant -- a rename must stay a one-line change, and a typo must be an "
        "ImportError rather than a fact written under one spelling and read back "
        "under another."
    )
