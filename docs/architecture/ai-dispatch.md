# AI dispatch and interception

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The boundary

All AI requests — ordinary completion, structured reasoning, Graph Analyzer,
Feedback, replay and simulation — terminate at **one** `FinalDispatcher`, in
`backend/src/return_platform/ai/gateway/final_dispatch.py`.

```text
caller builds payload
  │
  ▼  DispatchRequest
FinalDispatcher.dispatch()
  │
  ├─ 1. interception decision      ALLOW_PROVIDER | HUMAN_RESPONSE | REJECT
  ├─ 2. precondition check
  ├─ 3. route selection
  ├─ 4. acquire (concurrency, rate limit, circuit)
  ├─ 5. recursive redaction
  ├─ 6. ── the single provider.generate call ──
  ├─ 7. output safety
  ├─ 8. caller validation
  ├─ 9. failover bookkeeping
  └─ 10. priced telemetry
  │
  ▼
caller parses response and persists what else it needs
```

Callers own only what genuinely differs between them: how the payload is built,
how the response is parsed, and what else must be persisted alongside. Those
arrive as `DispatchRequest`, a `validate` callable and a `DispatchObserver`.

**A fourth caller should need no edit to `final_dispatch.py`.**

## Why one boundary

Three execution loops used to call `AIProvider.generate`: the eligibility
decision path (`service.py`), the structured-output path
(`structured_invocation.py`), and the dependency simulator's narrative path
(`dependency_simulation/ai.py`). They shared the route *pool* and the safety
*functions*, which is why the platform could describe itself as having one AI
path — but each carried its own copy of the machinery around the call: its own
candidate loop, its own failover bookkeeping, its own retry policy, its own
attempt telemetry, and in one case its own cost arithmetic.

That is a structural defect, not a stylistic one:

> **A control attached to one loop is absent from the other two.**

Concretely, before the collapse:

- **Interception** was real, audited and operator-visible on the decision path,
  and **did not exist** on the path the Order Agent and the Graph Analyzer
  actually use.
- **Recursive redaction** had to be added to each loop separately, and the
  simulator's loop never got it.
- **Cost** was priced from the released catalog on two loops and computed from a
  local table that returned `0` for an unpriced model on the third.

None of those were bugs anyone introduced. They are what having three loops
*means*.

## Why the interception decision lives in the dispatcher

C7 requires exactly one of `ALLOW_PROVIDER`, `HUMAN_RESPONSE` or `REJECT` before
any external call.

A decision point each caller opts into is a decision point each caller can
forget. Here it is **unconditional**: `dispatch()` asks the policy before it even
looks at a route, so a caller cannot reach a provider without a verdict.

What a caller supplies is *which* policy. The default is `ALLOW_ALL` — an
explicit, greppable choice rather than an omission.

## Redaction runs before the verdict

The ordering is: construct → safety → **recursive redaction** → interception →
decision → dispatch.

Redaction before interception, not after. A human reviewing an intercepted
request must not be shown raw customer data that the model itself would never
have received; and a request that is going to be rejected must still have been
redacted before it was written to the interception record.

Redaction is **recursive** — it walks nested structures rather than scanning a
flattened string. Preserve that.

## `DurableInterceptionProvider` is not the boundary

It is a **MANUAL provider**, gated to development and test, that *replaces* the
model with a human. It is reached through a route like any other provider, and it
reports `MANUAL` so a human answer is never laundered as model output.

Gating dispatch and substituting the thing dispatched to are different
mechanisms. Conflating them is how "we have interception" came to be believed
about paths that had none.

`ManualFileProvider` writes the request to `.manual_llm/requests/` and waits for
a reply in `.manual_llm/responses/`. That is how the AI path is exercised without
real API keys — `ORDER_AGENT_REASONING_V1` already lists `MANUAL` in
`allowedProviders`.

## No raw provider clients

**No provider object or raw provider HTTP client is exposed to business agents.**
A business agent that holds one has, by construction, a path around interception,
redaction, pricing and telemetry.

## Provider validation and routing

A key/model/task route is usable only after the backend has:

1. validated the provider adapter and endpoint allowlist;
2. authenticated with the transient key;
3. discovered accessible models;
4. verified the exact model id;
5. run a minimal synthetic inference;
6. verified required structured output and task capability;
7. recorded the key as usable — **only after all checks pass**;
8. created a receipt bound to provider, model, task, secret fingerprint and
   configuration checksum.

Publication is allowed only when every active key/model/task route has a valid
receipt.

Multiple keys and models are routed through bounded lists. Authentication
failure, throttling, timeout or model failure rotates to the next validated
route. Routes carry per-key concurrency controls, rate limits and circuit state.

Raw keys are accepted only by the backend validation control plane. The frontend
never receives a secret value.

## Interception operations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/ai/interceptions` | The queue of held requests |
| `GET` | `/api/ai/interceptions/{id}/request` | What the model was going to be asked |
| `POST` | `/api/ai/interceptions/{id}/allow` | `ALLOW_PROVIDER` — release to the provider |
| `POST` | `/api/ai/interceptions/{id}/answer` | `HUMAN_RESPONSE` — answer it yourself |
| `POST` | `/api/ai/interceptions/{id}/cancel` | `REJECT` |
| `GET` | `/api/ai/requests`, `/api/ai/metrics`, `/api/ai/metrics/summary` | Observability |
| `POST` | `/api/ai/requests/{trace_id}/replay` | Re-run a recorded request |
| `POST` | `/api/ai/requests/{trace_id}/compare` | Diff a replay against the original |
| `GET` | `/api/ai/routes`, `/api/ai/tasks` | The active route pool |

Replay and compare go through `FinalDispatcher` like everything else. A replay
that bypassed interception would be a way to launder a rejected request.

## Resuming an intercepted request

`workers/interception_resume.py` is the worker that carries an answered or
allowed interception back to the waiting caller. It is one of the six required
process classes for configuration adoption — see
[`configuration-adoption.md`](configuration-adoption.md).

## What AI may never do

- select a customer, an order or a line;
- change workflow state;
- generate database queries;
- bypass confirmation;
- see unredacted sensitive content;
- fabricate an integration success where
  `IntegrationTopicConfiguration.ai_may_fabricate_success` is false.

AI authority is restricted to approved wording and structured interpretation.
Explicit identifiers always beat conflicting AI output.

## Related

- [`../screens/ai-control-center.md`](../screens/ai-control-center.md)
- [`../optimization/model-routing.md`](../optimization/model-routing.md)
- [`security-boundaries.md`](security-boundaries.md)
