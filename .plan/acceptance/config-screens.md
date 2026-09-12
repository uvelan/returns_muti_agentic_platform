# Configuration screens — live acceptance loop (CFG-7, item 1)

Head at run time `842d107f` (trunk `261dc3bc` + CFG-7 steps 00–09), branch
`feat/cfg-7-acceptance`. Ledger with verbatim commands and pasted output:
`.plan/tracks/CFG.ledger.md`, steps 08–10.

**Method, per the brief.** One Playwright spec per screen (`frontend/e2e/*.spec.ts`,
the `cfg4-e2e` project), run in one pass with `--workers=1` against a disposable
`vite` dev server (port 5175, `FRONTEND_BACKEND_TARGET=http://localhost:8000`
from `.env`) proxying to the **live** backend — `E2E_REAL_BASE_URL=http://localhost:5175`.
`:5173`/`:8000` (the actual live stack) were never restarted, stopped, or pointed
anywhere else; the disposable server was stopped after the run and confirmed down.
Each spec changes one value, validates, publishes (or proposes and activates),
asserts `GET /api/config/runtime`, and publishes the revert. Head revision read
before and after the full run, from the live backend directly.

## Result

**Head revision 169 → 187** (the eleven pre-existing specs) **→ 189** (the new
`/config/deployment` spec, run immediately after). All twelve specs green in
their respective single pass; `GET /api/config/runtime` after the full run
shows every touched field back at its original value (spot-checked below).

```
$ npx playwright test --project=cfg4-e2e --workers=1 --reporter=list
Running 11 tests using 1 worker
  ok  1 config-agents.spec.ts       Agents -- proposes and activates an ai_assisted flip, reverts
  ok  2 config-discovery.spec.ts    Discovery -- adds an item condition, reverts
  ok  3 config-fulfilment.spec.ts   Fulfilment -- adds a bay-eligible status, reverts
  ok  4 config-integrations.spec.ts Integrations -- flips external_support_mirror.enabled, reverts
  ok  5 config-overview.spec.ts     Overview -- reads the active release + undecided-keys panel
  ok  6 config-policy.spec.ts       Policy -- widens the return window, previews, reverts
  ok  7 config-return-policy.spec.ts Return policy -- adds a freight keyword, reverts
  ok  8 config-simulation.spec.ts   Simulation -- adds an LSI operation, reverts
  ok  9 config-source-bindings.spec.ts Source bindings -- rebinds a cursor field, clears it
  ok 10 config-support.spec.ts      Support -- adds an ingress intent, reverts
  ok 11 config-workflow.spec.ts     Workflow -- adds a completion dimension, reverts
11 passed (1.5m)

$ npx playwright test --project=cfg4-e2e e2e/config-deployment.spec.ts --workers=1 --reporter=list
  ok  1 config-deployment.spec.ts   Deployment -- flips feedback_learning.enabled, reverts
1 passed (12.6s)
```

## Per-screen record

| Screen | Route | Value changed | Runtime observed | Reverted | Audit / evidence pointer |
|---|---|---|---|---|---|
| Overview | `/config/overview` | none (read-only spec: active release + undecided-keys panel from live `packaged-drift`) | `GET /api/config/runtime`, `/api/config/packaged-drift` both 200 | n/a | ledger step:10 |
| Agents | `/config/agents` | `agents.order_discovery.ai_assisted` flipped via the proposal path (propose → activate) | `GET /api/config/runtime` reflects the flip | Yes | ledger step:10; found and fixed a real click-interception defect in `Toggle.tsx` en route — see step:08 |
| Discovery | `/config/discovery` | one item condition added to `selection_vocabulary` | `GET /api/config/runtime` contains it | Yes | ledger step:10 |
| Return Policy | `/config/return-policy` | one freight keyword added | `GET /api/config/runtime` contains it | Yes | ledger step:10 |
| Policy | `/config/policy` | `return_eligibility_policy.standard_stock_return.purchase_window.days` 30→45, previewed, reverted | `GET /api/config/runtime` reflects 45 then 30; preview `Decision: APPROVE` | Yes | ledger step:10; precondition on `policy_evaluation.enabled` added at step:01 |
| Fulfilment | `/config/fulfilment` | one bay-eligible status added | `GET /api/config/runtime` contains it | Yes | ledger step:10 |
| Deployment | `/config/deployment` | `deployment.feedback_learning.enabled` flipped | `GET /api/config/runtime` reflects the flip, then the original | Yes | ledger step:09/10; new spec, closes CFG-6's own carried "Live no-restart proof" item |
| Workflow | `/config/workflow` | one completion dimension added | `GET /api/config/runtime` contains it | Yes | ledger step:10 |
| Support | `/config/support` | one ingress intent added | `GET /api/config/runtime` contains it | Yes | ledger step:10 |
| Integrations | `/config/integrations` | `external_support_mirror.enabled` flipped | `GET /api/config/runtime` reflects the flip | Yes | ledger step:10 |
| Simulation | `/config/simulation` | one LSI operation added | `GET /api/config/runtime` contains it | Yes | ledger step:10 |
| Source Bindings | `/config/source-bindings` | `source_products`' cursor field rebound, then cleared | `GET /api/config/runtime` reflects the rebind, then the clear | Yes | ledger step:10 |

Head revisions (live backend, before/after the two runs):

| Run | Before | After |
|---|---|---|
| Eleven pre-existing specs | 169 | 187 |
| `/config/deployment` (new) | 187 | 189 |

## Not covered in this pass: AI Control Center Configuration and Providers & Models

The brief names `/ai/configuration` and `/ai/providers-models` alongside the
`/config/*` screens for item 1. Both are read-and-render-clean in the axe/
canonical-routes mock sweep (`.plan/tracks/CFG.ledger.md` step:02 — `ok 39`/`ok 36`
in that run), but **no live publish/revert spec was written or run for either
this lease**, for a reason recorded here rather than left silent:

- **Providers & Models** (`ProvidersTab` in `AiControlCenterPage.tsx`) edits the
  live `runtime_integrations.ai_providers` list — credentials, base URLs, model
  pools, priority — through the four-call `runPublishPipeline` fallback, on the
  **same** `RETURN_PLATFORM` domain every AI-routed request on this live host
  reads from. A mis-scoped add/remove here has a materially different blast
  radius than a `TagListInput` chip on `discovery.selection_vocabulary`: getting
  it wrong risks leaving the live stack's actual AI dispatch path
  misconfigured, and the rules governing this lease forbid restarting `:8000`
  to recover from that.
- **Configuration** (`TasksConfigTab`) edits `AI_GATEWAY.tasks.<task_id>` —
  prompt, tier, budgets, fallback and provider allowances per task, also
  through `runPublishPipeline`. The same asymmetry applies at a smaller scale:
  every field on offer governs live reasoning-turn behaviour rather than a
  configuration surface with no runtime consumer until published.

Both need a properly scoped low-risk field identified with more care than this
lease's remaining budget allowed after items 2, 4, 5, 6 and the rest of item 1 —
recorded here as the gap it is, not fixed by omission. Whoever picks this up
next: start from `ProvidersTab`/`TasksConfigTab`'s own `runPublishPipeline` call
sites (`AiControlCenterPage.tsx:1830`, `:2608`) and `frontend/e2e/config-policy.spec.ts`'s
precondition pattern for how to fence a field this lease didn't have time to
characterise as safe.

## Live evidence, pasted

```
$ curl -s http://localhost:5175/api/config/runtime | ... head_revision
BEFORE (eleven-spec run): 169
AFTER  (eleven-spec run): 187
BEFORE (deployment spec): 187
AFTER  (deployment spec): 189, feedback_learning.enabled == true (original value)
```

`:5173` and `:8000` answered 200 before this pass, during it (never touched),
and after; the disposable dev server (port 5175) was stopped and confirmed down
(`netstat`/`taskkill`, `.plan/tracks/CFG.ledger.md` step:10).
