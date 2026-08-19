# AI Control Center

**Route** `/ai` · **Capability** `ai.request.read` ·
**Component** `frontend/src/domains/ai/AiControlCenterPage.tsx`

## Purpose

See what the models were asked, and answer anything held for review.

## Sections

`/ai/{slug}`, from `AI_SECTIONS`:

| Section | Shows |
|---|---|
| Overview | Current route health, recent volume, held interceptions |
| Requests | The request log: task, route, provider, model, tokens, latency, cost, outcome |
| Interceptions | The queue of requests held for a human decision |
| Metrics | Volume, latency, cost, failover and fallback rates |
| Providers & Models | Configured providers, keys (as references), models, circuit state |
| Routes & Tasks | Task → route bindings and their priorities |
| Safety | Safety and redaction policy |
| Configuration | The `AI_GATEWAY` domain of the active release |
| Audit | Administrative changes to any of the above |

## Which invocation paths can be intercepted (AI-01)

**All of them.** This is the answer that used to be wrong, and it is the most
important line on this page.

Every AI request — ordinary completion, structured reasoning, Graph Analyzer,
Feedback, replay and simulation — terminates at one `FinalDispatcher`, and
`dispatch()` asks the interception policy **before it looks at a route**. A caller
cannot reach a provider without a verdict.

Previously three execution loops called `AIProvider.generate`: the eligibility
decision path, the structured-output path, and the dependency simulator's
narrative path. Interception was real, audited and operator-visible on the first
and **did not exist** on the path the Order Agent and the Graph Analyzer actually
use. So this screen's queue was truthful about a fraction of traffic and silent
about the rest.

The three outcomes, and exactly one is chosen per request:

| Verdict | Effect |
|---|---|
| `ALLOW_PROVIDER` | Dispatch to the provider |
| `HUMAN_RESPONSE` | A human answers instead. Reported as `MANUAL`, **never** as the replaced provider |
| `REJECT` | No external call |

**Redaction runs before the verdict.** A reviewer is never shown raw content the
model itself would not have received, and a request that is going to be rejected
was still redacted before it was recorded.

### `DurableInterceptionProvider` is a different mechanism

It is a **MANUAL provider**, gated to development and test, that *replaces* the
model with a human. It is reached through a route like any other provider.

Gating dispatch and substituting the thing dispatched to are different mechanisms.
Conflating them is how "we have interception" came to be believed about paths that
had none. This screen keeps them apart: the Interceptions section is the gate;
`MANUAL` in the Providers section is the substitution.

## UI regions — Interceptions

**Queue** — held requests with task, age, and requesting context.

**Detail** — the redacted request as the model would have received it, plus the
decision controls.

The request shown is the **redacted** payload. That is the point: a reviewer
deciding whether a model may see something must be looking at what the model would
see.

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| Browse requests | `GET /api/ai/requests`, `/api/ai/metrics` | none | Yes |
| Read a held request | `GET /api/ai/interceptions/{id}/request` | none | Yes |
| **Allow** | `POST /api/ai/interceptions/{id}/allow` | The provider call proceeds. Tokens spent, cost incurred. | No |
| **Answer** | `POST /api/ai/interceptions/{id}/answer` | The human's text becomes the response, reported as `MANUAL` | No |
| **Cancel** | `POST /api/ai/interceptions/{id}/cancel` | `REJECT`. The caller gets its deterministic fallback. | No |
| Replay | `POST /api/ai/requests/{trace_id}/replay` | **A new provider call.** Costs money. | No |
| Compare | `POST /api/ai/requests/{trace_id}/compare` | Diffs a replay against the original | No |

All three interception decisions are irreversible and all three cost something
different: allow spends tokens, answer spends a person, cancel spends the quality
of the caller's fallback.

Replay goes through `FinalDispatcher` like everything else — a replay that
bypassed interception would be a way to launder a rejected request.

`workers/interception_resume.py` carries the decision back to the waiting caller.
If that worker is not running, decisions are recorded and callers keep waiting.

## Backend APIs consumed

| Method | Path |
|---|---|
| `GET` | `/api/ai/interceptions` |
| `GET` | `/api/ai/interceptions/{id}/request` |
| `POST` | `/api/ai/interceptions/{id}/allow` |
| `POST` | `/api/ai/interceptions/{id}/answer` |
| `POST` | `/api/ai/interceptions/{id}/cancel` |
| `GET` | `/api/ai/metrics`, `/api/ai/metrics/summary` |
| `GET` | `/api/ai/routes`, `/api/ai/tasks` |
| `POST` | `/api/ai/requests/{trace_id}/replay` |
| `POST` | `/api/ai/requests/{trace_id}/compare` |
| `GET` | `/api/config/runtime` |
| `GET` | `/api/principal` |

## Live-state behaviour

Polled, and the interceptions queue is polled hardest — a held request has a
caller blocked behind it, so latency here is a person waiting.

Metrics are aggregated server-side over a window; the screen names the window
rather than implying instantaneous truth.

## Loading, error and empty states

| State | Renders | Distinguished from broken by |
|---|---|---|
| No interceptions | "Nothing is held for review" | The **normal healthy state** when policy is `ALLOW_ALL` — worded so it does not look like a failure |
| No requests logged | "No AI requests in this window" | Names the window |
| Route circuit open | The circuit state on the route row | This is **data**: a rejected key opens its circuit and traffic rotates to the next validated route. Not a screen error |
| All routes exhausted | Stated explicitly | The caller got its deterministic fallback, and the flow continued — the screen says so |
| Unpriced model | Cost shown as unknown, **not zero** | A `0` for an unpriced model was one of the three-loop defects; UNKNOWN/null pricing semantics are preserved deliberately |
| Load failure | Error panel with correlation id | |

Pricing `UNKNOWN`/null is a preserved semantic. Rendering an unpriced call as
`$0.00` understates spend and is indistinguishable from a genuinely free call.

## Persistence and data source

- Request records, attempts, interceptions: **Platform MongoDB**, written by
  `FinalDispatcher` telemetry.
- Route pool and task bindings: the `AI_GATEWAY` domain of the active
  configuration release, in **Neo4j**.
- Keys: **the process environment**. Neo4j stores no key value, and the frontend
  receives neither.

Each request record carries provider, model, tokens, latency, cost, outcome and
correlation metadata — priced from the released catalog, not from a local table.

## Audit effects

Every interception decision is audited with the deciding principal. Every
administrative change to providers, keys, models, routes or safety policy is
audited and readable in the Audit section.

Replay records a new request linked to the original trace.

## Configuration dependencies

| Family | Effect | Restart |
|---|---|---|
| `AI_GATEWAY` — provider allowlists, keys, models | Which routes exist | Hot; the route pool is rebuilt at the activation boundary |
| `AI_GATEWAY` — task prompts and versions | What each task asks | Hot |
| `AI_GATEWAY` — token limits, retry, rate limits, circuit breakers | Route behaviour | Hot |
| `AI_GATEWAY` — deterministic fallback selection | What a caller gets on `REJECT` or exhaustion | Hot |
| Interception policy | Which requests are held. Default `ALLOW_ALL` — an explicit, greppable choice rather than an omission | Hot |

Production and staging **fail closed** when `AI_GATEWAY` is absent from the active
release.

A key/model/task route is usable only after live validation produced a receipt
bound to provider, model, task, secret fingerprint and
configuration checksum. Publication is refused while any active route lacks one.

## Known constraints

- No streaming view of an in-flight request.
- No per-operator interception assignment.
- Real API keys are not required to exercise the AI path: `ManualFileProvider`
  writes to `.manual_llm/requests/` and reads `.manual_llm/responses/`. Its
  directory is not configurable.

## Related

- [`../architecture/ai-dispatch.md`](../architecture/ai-dispatch.md)
- [`../optimization/model-routing.md`](../optimization/model-routing.md)
