# Approvals

**Route** `/approvals` · **Capability** `governance.proposal.read` ·
**Component** `frontend/src/domains/approvals/ApprovalsPage.tsx`

## Purpose

Decide what is waiting on you, with the whole basis for the decision in front of
you.

## Why it exists (UI-01)

`ProposalKernel` has recorded every change awaiting a human since it was built,
and served that queue to **nobody**: there was no route in the shell and no
directory under `domains/`. A schema draft could be validated, an agent
configuration edited and a feedback improvement raised, and the record of
"someone has to decide this" existed only in Mongo.

## Why an independent domain, not a tab

A reviewer's question is "what is waiting on me". That is not a question about
configuration, or about the analyzer, or about AI — **it spans all three**, which
is exactly why the kernel is one inbox. Nesting it under any one of them would
have made the other two invisible from it.

For the same reason the domain needs **no per-type capability**:
`governance.proposal.read` is the governance read, which is exactly the question
this domain asks.

## UI regions

**Queue** — every proposal awaiting a decision, with type, risk, requester and
age. The status filter narrows the list; it does not switch what the screen is.

**Proposal detail**:

- what is being changed, and by whom;
- the **risk pill**;
- the **before/after diff**;
- the decisions currently legal for this proposal's status;
- the kernel's own refusal message when it rejects a decision.

**Contextual rail** — proposal facts and notes.

## Risk

Risk is derived from the **shape** of the change. A removal is `HIGH` because it
is the only class that destroys something a running system may still read. The
pill is coloured accordingly, so "why is this `HIGH`" has an answer on the same
row rather than in a separate legend.

## The diff

Derived from before/after **by the kernel**, not by the screen. That has one
consequence worth stating explicitly:

> An empty diff means the documents **agree** — not that the diff failed to load.

The screen says "the before and after documents are identical" in those words,
because "no changes shown" is ambiguous and this is not.

A diff leaf whose value is `undefined` renders as `-`, meaning "this side does not
have the key" — distinct from a key present with an empty value.

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| Open a proposal | `GET /api/proposals/{proposal_id}` | none | Yes |
| Approve | `POST /api/proposals/{proposal_id}/approve` | Marks approved. Does **not** apply the change. | No |
| Reject | `POST /api/proposals/{proposal_id}/reject` | Terminal | No |
| Activate | `POST /api/proposals/{proposal_id}/activate` | **Applies the approved change.** For a schema proposal this can trigger a graph migration. | No |

Approve and activate are separate on purpose. Approving records a human decision;
activating executes it. A schema activation runs the strategy its migration plan
named — `BACKFILL`, `AFFECTED_SCOPE_RESYNC` or a `FULL_REBUILD` generation
cutover — and an operator should be able to approve during review hours and
activate during a maintenance window.

## The kernel owns the lifecycle

There is **no transition logic on this screen** beyond `DECISIONS_BY_STATUS`,
which mirrors the backend's own table so an operator is not offered a button that
returns 409.

And when the kernel refuses anyway, the refusal is surfaced verbatim rather than
translated. A mirrored table can drift; the kernel cannot be wrong about its own
lifecycle.

## Governance forbidden keys

Some configuration keys no proposal may touch, enforced by
`platform/governance/key_policy.py`. A proposal that names one is refused by the
kernel, and the refusal names the key. This screen does not maintain its own copy
of that policy.

## Backend APIs consumed

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/proposals` | The queue |
| `GET` | `/api/proposals/{proposal_id}` | Detail, before/after, diff, risk |
| `POST` | `/api/proposals/{proposal_id}/approve` | Record approval |
| `POST` | `/api/proposals/{proposal_id}/reject` | Reject |
| `POST` | `/api/proposals/{proposal_id}/activate` | Apply |

## Live-state behaviour

Polled on interval and on focus. A decision made by another reviewer while this
tab is open is picked up on the next refetch, and the mirrored decision table
means the stale button is short-lived — and the kernel's 409 covers the window.

## Loading, error and empty states

| State | Renders | Distinguished from broken by |
|---|---|---|
| Empty queue | "Nothing is waiting on a decision" | Explicit; the intended steady state, not a problem |
| Identical documents | "The before and after documents are identical" | Says *agree*, not *empty* |
| Decision refused (409) | The kernel's own message | The operator learns why, not just that it failed |
| Load failure | Error panel with correlation id | |

An empty Approvals queue is the **normal healthy state**. The wording avoids
suggesting otherwise.

## Persistence and data source

`ProposalKernel` records in **Platform MongoDB**. Proposal documents carry
before/after snapshots, so the diff does not depend on the underlying object still
existing in its pre-change form.

## Audit effects

Every decision is audited with the deciding principal, the timestamp, and the
proposal's before/after. Activation additionally records what the applied change
did — for a schema proposal, the migration classification and the strategy run.

## Configuration dependencies

| Family | Effect | Restart |
|---|---|---|
| `platform/governance/key_policy.py` forbidden keys | Which proposals the kernel refuses outright | Code-level |
| Proposal type registrations | What can appear in the queue | Hot |

## Known constraints

- No bulk approve/reject.
- No delegation or assignment — the queue is "everything waiting", not "waiting on
  me specifically".
- No comment thread on a proposal.
