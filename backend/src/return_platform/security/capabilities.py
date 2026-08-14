"""The capability model: what a principal may do, independent of who they are.

Phase 17. This is the piece that genuinely did not exist.

**Why a capability layer at all, when roles already work.** Phases 18-21 each
need to decide whether to show a queue, enable an action, make a field
editable, or offer an approval. If those screens branch on role names, the role
vocabulary becomes a frontend API: adding a role means editing four screens,
and every screen encodes its own idea of what `warehouse_associate` may do.
They will drift, and the drift is invisible until someone sees a button they
should not have. A capability is the question a screen actually wants to ask --
"may I do this" -- and it is answerable without knowing the role table.

**Backend authorization stays authoritative.** The fast plan's Wave E section
is explicit that UI hiding is presentation only. Every capability here is
enforced server-side by `authorization.require_capability`; the principal
endpoint exists so the UI can avoid *offering* an action it would be refused,
not so the UI can decide anything.

**Roles remain the source of truth.** Capabilities are derived from roles by
`capabilities_for_roles`, not stored alongside them. A principal arrives with
roles (that is what `Principal` carries and what every existing dependency
checks), so deriving keeps one input and makes the mapping below the single
place the two vocabularies meet.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from return_platform.security import roles as r

# Returns domain -- `/api/returns`.
RETURNS_SESSION_READ: Final = "returns.session.read"
RETURNS_SESSION_WRITE: Final = "returns.session.write"
RETURNS_SUPPORT_ACT: Final = "returns.support.act"
RETURNS_LOGISTICS_ACT: Final = "returns.logistics.act"
RETURNS_WAREHOUSE_ACT: Final = "returns.warehouse.act"
RETURNS_AUDIT_READ: Final = "returns.audit.read"

# Configuration domain -- `/api/config`.
CONFIG_RUNTIME_READ: Final = "config.runtime.read"
CONFIG_RELEASE_READ: Final = "config.release.read"
CONFIG_RELEASE_PROMOTE: Final = "config.release.promote"
CONFIG_SOURCE_READ: Final = "config.source.read"
CONFIG_SOURCE_WRITE: Final = "config.source.write"
#: Repointing a dataset at different infrastructure -- `PUT`/`DELETE
#: /api/source-bindings/{dataset}`.
#:
#: **Separate from `config.source.write`, and deliberately narrower.** A resync
#: re-reads where the platform already reads; a rebind decides where it reads
#: production data from at all, takes effect on the next direct source read with
#: no approval in between, and is not the act of an operator with rights over one
#: return. `api/graph_sync.py` states that distinction explicitly at the resync
#: route, and this is the other half of it.
#:
#: **This changes no policy.** Only `ALL_CAPABILITIES` carries it, so the
#: principals who hold it are exactly `roles.ADMIN_ROLES` -- the set the two
#: routes already enforced through `require_admin_roles`. What it changes is
#: whether the gate is *expressible*: the console decides what to offer from
#: `/api/principal`'s capability list, no capability mirrored `ADMIN_ROLES`, and
#: a UI that cannot ask therefore had to offer Rebind to a `WORKSPACE_EDITOR`
#: who would receive a 403.
CONFIG_SOURCE_REBIND: Final = "config.source.rebind"

# Graph Schema Analyzer domain -- `/api/graph-schema`.
GRAPH_SCHEMA_DRAFT_READ: Final = "graph_schema.draft.read"
GRAPH_SCHEMA_DRAFT_WRITE: Final = "graph_schema.draft.write"
GRAPH_SCHEMA_GENERATION_ACTIVATE: Final = "graph_schema.generation.activate"

# Governance domain -- `/api/proposals`, and every surface that proposes a
# change. Deliberately *not* per-proposal-type: the whole point of the shared
# kernel is that a schema draft, an agent configuration edit and a feedback
# improvement are one decision queue with one grant behind it. Three approval
# capabilities would be three approval paths wearing a shared type.
GOVERNANCE_PROPOSAL_READ: Final = "governance.proposal.read"
GOVERNANCE_PROPOSAL_WRITE: Final = "governance.proposal.write"
GOVERNANCE_PROPOSAL_APPROVE: Final = "governance.proposal.approve"
GOVERNANCE_PROPOSAL_ACTIVATE: Final = "governance.proposal.activate"

# AI Control Center domain -- `/api/ai`.
AI_REQUEST_READ: Final = "ai.request.read"
AI_METRICS_READ: Final = "ai.metrics.read"
AI_INTERCEPTION_READ: Final = "ai.interception.read"
AI_INTERCEPTION_ACT: Final = "ai.interception.act"
AI_REPLAY_READ: Final = "ai.replay.read"
AI_ROUTE_WRITE: Final = "ai.route.write"

ALL_CAPABILITIES: Final = frozenset(
    {
        RETURNS_SESSION_READ,
        RETURNS_SESSION_WRITE,
        RETURNS_SUPPORT_ACT,
        RETURNS_LOGISTICS_ACT,
        RETURNS_WAREHOUSE_ACT,
        RETURNS_AUDIT_READ,
        CONFIG_RUNTIME_READ,
        CONFIG_RELEASE_READ,
        CONFIG_RELEASE_PROMOTE,
        CONFIG_SOURCE_READ,
        CONFIG_SOURCE_WRITE,
        CONFIG_SOURCE_REBIND,
        GRAPH_SCHEMA_DRAFT_READ,
        GRAPH_SCHEMA_DRAFT_WRITE,
        GRAPH_SCHEMA_GENERATION_ACTIVATE,
        GOVERNANCE_PROPOSAL_READ,
        GOVERNANCE_PROPOSAL_WRITE,
        GOVERNANCE_PROPOSAL_APPROVE,
        GOVERNANCE_PROPOSAL_ACTIVATE,
        AI_REQUEST_READ,
        AI_METRICS_READ,
        AI_INTERCEPTION_READ,
        AI_INTERCEPTION_ACT,
        AI_REPLAY_READ,
        AI_ROUTE_WRITE,
    }
)

_READ_ONLY_EVERYWHERE: Final = frozenset(
    {
        RETURNS_SESSION_READ,
        CONFIG_RUNTIME_READ,
        CONFIG_RELEASE_READ,
        CONFIG_SOURCE_READ,
        GRAPH_SCHEMA_DRAFT_READ,
        GOVERNANCE_PROPOSAL_READ,
        AI_REQUEST_READ,
        AI_METRICS_READ,
        AI_INTERCEPTION_READ,
    }
)

# `ai.replay.read` is deliberately NOT in the read-only bundle. Phase 14 gates
# it separately because a replay envelope reproduces the original request,
# including the source rows a prompt carried -- it is a different question from
# "may this person see that a request happened".
_ROLE_CAPABILITIES: Final[dict[str, frozenset[str]]] = {
    r.CONSOLE_ADMIN: ALL_CAPABILITIES,
    r.CONSOLE_VIEWER: _READ_ONLY_EVERYWHERE,
    r.WORKSPACE_VIEWER: _READ_ONLY_EVERYWHERE,
    # An editor may propose and may not decide. That split is the reason the
    # approval capability exists at all: before it, `POST /drafts/{id}/approve`
    # carried no dependency and accepted any actor the middleware had let in.
    #
    # `CONFIG_SOURCE_REBIND` is on the "decide" side and so is absent here: a
    # rebinding is not a proposal anybody approves, it repoints production reads
    # on the next request.
    r.WORKSPACE_EDITOR: _READ_ONLY_EVERYWHERE
    | {CONFIG_SOURCE_WRITE, GRAPH_SCHEMA_DRAFT_WRITE, GOVERNANCE_PROPOSAL_WRITE},
    r.RETURN_ASSOCIATE: frozenset({RETURNS_SESSION_READ, RETURNS_SESSION_WRITE}),
    r.RETURN_SUPPORT: frozenset({RETURNS_SESSION_READ, RETURNS_SESSION_WRITE, RETURNS_SUPPORT_ACT}),
    r.LOGISTICS_COORDINATOR: frozenset(
        {RETURNS_SESSION_READ, RETURNS_SESSION_WRITE, RETURNS_LOGISTICS_ACT}
    ),
    r.WAREHOUSE_ASSOCIATE: frozenset(
        {RETURNS_SESSION_READ, RETURNS_SESSION_WRITE, RETURNS_WAREHOUSE_ACT}
    ),
    r.RETURN_AUDITOR: frozenset(
        {
            RETURNS_SESSION_READ,
            RETURNS_AUDIT_READ,
            CONFIG_RUNTIME_READ,
            CONFIG_RELEASE_READ,
            GOVERNANCE_PROPOSAL_READ,
            AI_REQUEST_READ,
            AI_METRICS_READ,
        }
    ),
    # A service account, not a person. It carries the write capabilities the
    # platform's own inter-module calls need and none of the operator-judgment
    # ones: it must never promote a release, activate a generation, or answer
    # an interception on a human's behalf.
    r.RETURN_PLATFORM_SERVICE: frozenset(
        {
            RETURNS_SESSION_READ,
            RETURNS_SESSION_WRITE,
            RETURNS_SUPPORT_ACT,
            RETURNS_LOGISTICS_ACT,
            RETURNS_WAREHOUSE_ACT,
            CONFIG_RUNTIME_READ,
            CONFIG_RELEASE_READ,
            GRAPH_SCHEMA_DRAFT_READ,
            AI_REQUEST_READ,
        }
    ),
}


def capabilities_for_roles(principal_roles: Iterable[str]) -> frozenset[str]:
    """Union the capabilities of every role the principal holds.

    Unknown roles contribute nothing rather than raising. A principal minted by
    an upstream identity provider may legitimately carry roles this platform
    does not model, and refusing the whole request because one role is
    unrecognized would make an unrelated system's role addition an outage here.
    """
    granted: set[str] = set()
    for role in principal_roles:
        granted |= _ROLE_CAPABILITIES.get(role, frozenset())
    return frozenset(granted)
