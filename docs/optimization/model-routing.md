# Model routing, cost and prompt caching

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The problem

Route each AI task to a model that is capable enough and no more expensive than
necessary, survive provider failures without failing the business flow, and report
cost accurately enough that spend is governable.

## The scale assumption

Every associate turn may make one or more model calls, so volume scales with return
volume. Cost is therefore per-return, not per-day, and a mispriced model is a
mispriced business.

## The correctness invariant

> *No routing or cost optimization may change what the platform is willing to
> send, or hide what it spent.*

Concretely: escalation must not widen the allowed provider set; failover must not
skip redaction or interception; and an unpriced model must not report `0`.

## Strategy

### One dispatch boundary

All routing happens inside `FinalDispatcher`. There is one candidate loop, one
failover bookkeeping path, one retry policy, one attempt telemetry, one cost
arithmetic. See
[`../architecture/ai-dispatch.md`](../architecture/ai-dispatch.md).

Three loops used to exist, and **cost was one of the three things they disagreed
about**: priced from the released catalog on two loops, and computed from a local
table that returned `0` for an unpriced model on the third. That is not a rounding
difference; it is a cost report that understates spend and is indistinguishable
from a genuinely free call.

### Route selection

A task declares a configured tier and an allowed provider list. Candidates are
drawn from validated routes matching the task, ordered by configured priority.

Each route carries per-key concurrency controls, rate limits and circuit state.

### Failover

Authentication failure, throttling, timeout or model failure rotates to the next
validated route. A rejected key **opens its circuit** so subsequent traffic skips
it rather than re-learning the rejection.

Failover is bookkeeping, not a new decision: the interception verdict and the
redaction already happened before route selection, so a failover attempt cannot
send something the first attempt was not allowed to send.

### Tier escalation

`allow_tier_escalation` is a **per-request opt-in, defaulting to `False`**.

The default is the point. The eligibility decision path has never escalated, and
silently gaining escalation by collapsing three loops into one would have been a
behaviour change smuggled in as a refactor. So escalation is a field a caller sets,
not something the dispatcher decides on a caller's behalf.

When enabled, exhausting the configured tier's candidates escalates to a
higher-capability tier and reports `on_tier_escalation(attempts, last_error)`.
Telemetry records `configured_tier` and `selected_tier` separately, so an
escalation is visible as an escalation rather than as a task that was always
expensive.

### Deterministic fallback

When every route is exhausted, intercepted, rate-limited or rejected, the caller
receives its configured deterministic response and the business flow continues. AI
failure never breaks the flow.

## Prompt caching

**The platform does not construct an invariant prefix.** It records what providers
report.

`ProviderResponse` splits the input count:

| Field | Meaning |
|---|---|
| `input_tokens` | The prompt the provider **actually processed** — the uncached part |
| `cached_input_tokens` | What it served from a prompt cache |
| `output_tokens`, `total_tokens` | As reported |

They are separate because **they are billed at different rates**, and adapters
normalize to this split rather than passing through their vendor's convention:
Anthropic reports cache reads as a *sibling* of `input_tokens`, OpenAI reports them
as a *subset* of the prompt count, and adding a subset to its own superset doubles
the bill.

`None` means the provider said nothing about caching. **That is not the same as
"nothing was cached"** — which is exactly why these default to `None` rather than
`0`. A defaulted `0` would assert a fact no provider stated.

### The decision not to construct a cache prefix

Recorded here because the audit could not locate one and "not found" needs an
answer:

Prompt caching pays when a long prefix is reused across many calls with high
temporal locality. The platform's prompts are assembled per turn from the
identification catalogue, the conversation state, and the candidate evidence — and
the catalogue is **runtime configuration that an operator may change at any
release**. A prefix engineered to be invariant would either exclude the catalogue,
which is most of the useful context, or become a cache that invalidates on every
configuration publish.

So the platform lets providers cache what they can detect and prices the result
honestly, rather than restructuring prompts around a cache boundary that
configuration can move. Revisit if per-turn prompt sizes grow materially.

## Pricing

Cost is computed from the **released pricing catalog**, versioned with the
configuration release, not from a table in code.

`UNKNOWN`/null pricing semantics are **preserved deliberately**. An unpriced model
reports unknown cost, never `$0.00`. Rendering an unpriced call as free understates
spend and is indistinguishable from a genuinely free call — the AI Control Center
displays it as unknown for the same reason.

Every attempt records provider, model, input/cached-input/output tokens, latency,
cost, outcome and correlation metadata.

## Caching and invalidation

| Cache | Invalidation |
|---|---|
| Route pool | Rebuilt at the configuration **activation boundary**, in the same swap that applies the release |
| Task/prompt resolution | Same boundary. `promptVersion`, tier, token ceilings and allowed providers are resolved once per dispatch rather than per attempt |
| Circuit state | Per-key, time-based recovery |
| Provider keys | Read from the environment when creating or refreshing clients, not per request |

The route pool and the configuration snapshot swap **together**. A pool rebuilt at
a different moment than the release it belongs to would route on one release's
providers with another's limits.

## The consistency tradeoff

Between a publish and a process's adoption, that process routes on the previous
release. This is bounded and reported: `GET /api/config/adoption` says `ACTIVATING`
until every required class has adopted. See
[`configuration-caching.md`](configuration-caching.md).

## The fallback

| Failure | Fallback |
|---|---|
| One key rejected | Open that key's circuit, next validated key |
| Model removed or inaccessible | Next validated model/provider route |
| Throttled | Next route, then retry per policy |
| Tier exhausted, escalation enabled | Higher tier |
| Tier exhausted, escalation disabled | Deterministic fallback |
| All providers unavailable | Deterministic task response; **main flow continues** |
| `REJECT` verdict | Deterministic fallback; no external call |
| No real API keys (dev/test) | `ManualFileProvider` — request to `.manual_llm/requests/`, reply from `.manual_llm/responses/`, reported as `MANUAL` |

## The limits

- A route is usable only after live validation produced a receipt bound to
  provider, model, task, secret fingerprint and configuration
  checksum. Publication is refused while an active route lacks one.
- Candidate lists are **bounded**. Failover does not walk an unbounded set.
- Raw keys are accepted only by the backend validation control plane. Never in a
  production application environment.
- No business agent holds a provider object or raw HTTP client — that would be a
  path around interception, redaction, pricing and telemetry.
- A human answer is reported as `MANUAL`, never as the replaced provider.

## Observability

`GET /api/ai/metrics`, `/api/ai/metrics/summary`, `/api/ai/routes`,
`/api/ai/tasks`, and the request log in the
[AI Control Center](../screens/ai-control-center.md).

Per request: attempts, which routes were tried and why each failed, configured vs
selected tier, token split including cache reads, latency, cost, final outcome.

## The failure mode

**Historically:** three loops meant a control on one was absent from the others.
Interception did not exist on the path the Order Agent and Graph Analyzer used;
the simulator's loop never got recursive redaction; and one loop reported `0` cost
for unpriced models. Each was invisible from the others.

**Now:** one loop. A control added to it applies to every caller, and a fourth
caller needs no edit to `final_dispatch.py`. The remaining failure mode is a
misconfigured route, which is visible in route health and circuit state.

## Related

- [`../architecture/ai-dispatch.md`](../architecture/ai-dispatch.md)
- [`../screens/ai-control-center.md`](../screens/ai-control-center.md)
- [`retry-and-backoff.md`](retry-and-backoff.md)
