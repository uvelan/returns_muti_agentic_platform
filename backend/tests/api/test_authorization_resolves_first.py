"""Authorization must be the *first* dependency a route resolves.

FastAPI resolves a handler's dependencies in the order the parameters are
declared and stops at the first one that raises. So a route that declares a
collaborator ahead of its grant answers that collaborator's failure to an
anonymous caller: on the governance routes `resolve_proposal_kernel` returned
503 GOVERNANCE_UNAVAILABLE before `require_capability` was ever consulted, which
told a caller with no credentials whether the proposal kernel is composed in
this process.

That is not something a single behavioural test can hold. Every future route is
one parameter order away from reintroducing it, and the syntax pushes the wrong
way -- `actor: str = Depends(...)` carries a default, and a defaulted parameter
cannot precede one without a default, so the natural spelling forces the grant
to the *end* of the signature. Writing the grant as an `Annotated` alias is what
lets it come first.

This is therefore a structural check over the whole source tree rather than a
test of one router. It reads signatures with `ast` instead of importing the app,
because the defect is in the declaration and importing every API module drags in
their runtime collaborators.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"

#: The dependency callables that decide whether a caller may be here at all.
#: Matched as a substring of the unparsed `Depends(...)` argument so that
#: `require_capability(GOVERNANCE_PROPOSAL_READ)` and a bare
#: `require_read_roles` are both recognised.
_AUTHORIZATION_DEPENDENCIES = (
    "require_capability",
    "require_roles",
    "require_read_roles",
    "require_write_roles",
    "require_admin_roles",
    "require_associate_roles",
    "require_support_roles",
    "require_return_collaboration_roles",
    "require_logistics_roles",
    "require_warehouse_roles",
    "require_audit_roles",
    "resolve_principal",
    "actor_roles",
)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _depends_arguments(node: ast.AST) -> list[str]:
    """The callables named inside any `Depends(...)` reachable from `node`."""
    return [
        ast.unparse(sub.args[0])
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Name)
        and sub.func.id == "Depends"
        and sub.args
    ]


def _module_dependency_aliases(tree: ast.Module) -> dict[str, str]:
    """Module-level `_Kernel = Annotated[X, Depends(y)]` aliases.

    Routers name their collaborators through these, so an alias annotation is a
    dependency even though no `Depends` appears in the signature itself.
    """
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if node.value is None:
            continue
        found = _depends_arguments(node.value)
        if not found:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = found[0]
    return aliases


def _is_route(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in _HTTP_METHODS
        for dec in fn.decorator_list
    )


def _resolution_order(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, aliases: dict[str, str]
) -> list[tuple[str, str]]:
    """`(parameter, dependency callable)` in the order FastAPI resolves them.

    Decorator-level `dependencies=[...]` are excluded deliberately: FastAPI
    inserts those at the front of the dependant, so they always resolve before
    anything in the signature and cannot produce this defect.
    """
    args = fn.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults: dict[str, ast.expr] = {}
    for arg, default in zip(
        positional[len(positional) - len(args.defaults) :], args.defaults, strict=True
    ):
        defaults[arg.arg] = default
    for arg, keyword_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if keyword_default is not None:
            defaults[arg.arg] = keyword_default

    order: list[tuple[str, str]] = []
    for arg in positional + list(args.kwonlyargs):
        found: list[str] = []
        if arg.annotation is not None:
            found += _depends_arguments(arg.annotation)
            if not found and isinstance(arg.annotation, ast.Name):
                found += [aliases[arg.annotation.id]] if arg.annotation.id in aliases else []
        if arg.arg in defaults:
            found += _depends_arguments(defaults[arg.arg])
        order.extend((arg.arg, dependency) for dependency in found)
    return order


def test_no_route_resolves_a_collaborator_before_its_authorization_grant() -> None:
    offenders: list[str] = []
    guarded_routes = 0

    for path in sorted(_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "Depends(" not in source:
            continue
        tree = ast.parse(source, filename=str(path))
        aliases = _module_dependency_aliases(tree)
        relative = path.relative_to(_SRC).as_posix()

        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) or not _is_route(fn):
                continue
            order = _resolution_order(fn, aliases)
            grant_positions = [
                index
                for index, (_param, dependency) in enumerate(order)
                if any(marker in dependency for marker in _AUTHORIZATION_DEPENDENCIES)
            ]
            if not grant_positions:
                continue
            guarded_routes += 1
            first_grant = min(grant_positions)
            if first_grant:
                earlier = ", ".join(dependency for _param, dependency in order[:first_grant])
                offenders.append(
                    f"{relative}:{fn.lineno} {fn.name} resolves {earlier} before "
                    f"{order[first_grant][1]}"
                )

    # Self-verifying: a refactor that renames the authorization helpers, moves
    # the API packages, or breaks the alias resolution would otherwise make this
    # pass by finding nothing at all.
    assert guarded_routes > 100, (
        f"only {guarded_routes} authorization-guarded routes were inspected, so this check "
        "is no longer reading the API surface it is supposed to guard"
    )

    assert offenders == [], (
        "these routes resolve a collaborator before checking whether the caller is "
        "allowed in, so an unauthorized caller receives that collaborator's failure "
        "(a 503 naming an uncomposed subsystem) instead of a 401/403 -- declare the "
        "grant first, as an Annotated alias: " + "; ".join(offenders)
    )
