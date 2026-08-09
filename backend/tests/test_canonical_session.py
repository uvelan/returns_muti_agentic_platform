"""`GET /api/session` — the last thing Wave D owed Wave E.

The platform always resolved a `Principal` per request but never returned one,
so the frontend's capability hook **fails open**: it reports `granted` when no
principal is available, because failing closed would have blanked the console
for every user until this existed. This endpoint is what lets that stop being
true.

Two behaviours here are load-bearing for the UI and easy to get subtly wrong:

* **A signed-in caller with no usable role must get 200 and an empty capability
  list**, not 403. They are exactly the caller who most needs an answer, and a
  403 renders as a failed request rather than "you have no access".
* **Capabilities are derived from the same frozensets the routes enforce.** The
  frontend previously mirrored the role sets in TypeScript; two copies of an
  authorization rule drift silently, and the copy that drifts is always the one
  nobody re-reads.
"""

from __future__ import annotations

from return_platform.api.canonical_session import capabilities_for
from return_platform.security.roles import (
    BUSINESS_READ_ROLES,
    CONSOLE_READ_ROLES,
    READ_ROLES,
    WRITE_ROLES,
)


def test_a_reader_gets_read_capabilities_only() -> None:
    """Picks a *sorted* read-only role, not `next(iter(...))`.

    An earlier version took an arbitrary element of the frozenset, which varies
    with PYTHONHASHSEED -- it passed on the host and failed in the container
    because the arbitrary pick was sometimes CONSOLE_ADMIN, which is also a
    write role. A test whose subject depends on hash ordering is not testing
    what it says.
    """
    read_only = sorted(CONSOLE_READ_ROLES - WRITE_ROLES)
    assert read_only, "expected at least one read-only console role"

    granted = capabilities_for(frozenset({read_only[0]}))

    assert granted, "a console reader must be able to see something"
    assert all(capability.endswith(":read") for capability in granted)


def test_a_writer_gets_both_read_and_write() -> None:
    """Every write role is also a read role in this platform, and the UI relies
    on that: a user who can edit must also be able to view."""
    granted = capabilities_for(frozenset({sorted(WRITE_ROLES)[0]}))

    assert any(capability.endswith(":write") for capability in granted)
    assert any(capability.endswith(":read") for capability in granted)


def test_an_unrecognised_role_grants_nothing() -> None:
    """Fail closed on the *capability* computation. The endpoint stays 200 --
    that decision is about the response code, not about inventing access."""
    assert capabilities_for(frozenset({"some-role-that-does-not-exist"})) == []


def test_capabilities_cover_exactly_the_four_canonical_domains() -> None:
    """If a fifth canonical domain is added, this fails and makes someone decide
    what its capability is called -- rather than the UI silently having no way
    to gate it."""
    granted = capabilities_for(WRITE_ROLES | READ_ROLES)
    domains = {capability.split(":", 1)[0] for capability in granted}

    assert domains == {"returns", "config", "graph-schema", "ai"}


def test_a_business_role_is_not_treated_as_a_console_role() -> None:
    """`READ_ROLES` is the union of console and business roles. A returns
    associate must get read access without accidentally acquiring write."""
    associate_only = sorted(BUSINESS_READ_ROLES - WRITE_ROLES)
    assert associate_only, "expected at least one read-only business role"

    granted = capabilities_for(frozenset({associate_only[0]}))

    assert granted
    assert not any(capability.endswith(":write") for capability in granted)


def test_the_derivation_uses_the_same_sets_the_routes_enforce() -> None:
    """The anti-drift property, stated as a test rather than a comment.

    Anything in `READ_ROLES` must produce read capabilities; anything outside
    both sets must produce none. If someone adds a role to `roles.py` and
    forgets this endpoint, the first half fails.
    """
    for role in READ_ROLES:
        assert capabilities_for(frozenset({role})), f"{role} is a read role but grants nothing"
    for role in WRITE_ROLES:
        assert any(
            capability.endswith(":write") for capability in capabilities_for(frozenset({role}))
        ), f"{role} is a write role but grants no write capability"
