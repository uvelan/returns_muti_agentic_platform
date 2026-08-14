# Screen documentation

**Current as of 2026-08-14, commit `dcbb7dc`.**

Functional documentation for every screen in the operations UI. Before this
directory existed, screens were documented only by inline TSX comments —
frequently excellent, but not something an operator, a support engineer or a
tester can find.

Every document here follows the same template (Section T-8 of the audit):
purpose, UI regions, actions, backend APIs consumed, live-state behaviour,
loading/error/empty states, persistence and data source, audit effects,
configuration dependencies, known constraints.

## The nine domains and the landing page

| Screen | Route | Doc | Required capability |
|---|---|---|---|
| Platform landing | `/` | [`landing.md`](landing.md) | — |
| Return Business Copilot | `/returns` | [`returns-workspace.md`](returns-workspace.md) | `returns.session.read` |
| Returns Support | `/support` | [`support-console.md`](support-console.md) | `returns.session.read` |
| Configuration | `/config` | [`configuration.md`](configuration.md) | `config.runtime.read` |
| Approvals | `/approvals` | [`approvals.md`](approvals.md) | `governance.proposal.read` |
| Data Sources | `/data-sources` | [`data-sources.md`](data-sources.md) | `config.source.read` |
| Graph Schema Analyzer | `/graph-schema` | [`graph-schema-studio.md`](graph-schema-studio.md) | `graph_schema.draft.read` |
| AI Control Center | `/ai` | [`ai-control-center.md`](ai-control-center.md) | `ai.request.read` |
| Source Sync | `/sync` | [`sync-control.md`](sync-control.md) | `config.source.read` |
| Operations | `/operations` | [`case-operations.md`](case-operations.md) | `config.runtime.read` |

Nine domains, not four. The README described "four canonical domains" long after
Approvals, Data Sources, Support, Sync and Operations were registered in
`frontend/src/domains/registry.ts`.

`requires` is the capability that makes a domain **visible**, and it is
deliberately the domain's cheapest read: a principal who cannot read anything in
a domain has no use for its entry. **Hiding is presentation only** — the backend
refuses regardless. A screen appearing is not an authorization decision.

## Conventions every screen follows

**No fabricated state.** Where the platform publishes no field, a screen says so
in those words rather than showing a plausible placeholder. A fabricated
"HEALTHY" is worse than an admitted gap, because the gap is fixable and the
fabrication is trusted.

**`NO BACKEND YET`** is a card badge on the landing page, set from
`DomainDefinition.status`. It means no backend surface exists for that domain at
all. It is removed the moment one does — and a badge over two working screens
would be the same lie in the other direction.

**Derived progress, never assumed.** A stage that a response cannot speak to
stays pending. The temptation is a bar that advances on a timer, which looks
finished and means nothing.

**Rails are contextual.** Every domain carries a `DomainRail` on the right with
facts and notes scoped to what is selected. Collapsible, and the collapsed state
persists per domain.

**Empty is distinguished from broken.** Each screen states, in its own document,
how an operator tells "there is nothing here" from "we could not load it".

## Related

- [`../architecture/canonical-runtime-flow.md`](../architecture/canonical-runtime-flow.md)
- [`../api/README.md`](../api/README.md)
- `frontend/README.md` — the frontend runbook
