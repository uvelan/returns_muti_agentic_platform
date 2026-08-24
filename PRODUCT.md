# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Three populations of comparable daily volume. **No single primary user** — confirmed
2026-08-24 — so no one surface can serve as the density or layout anchor for the rest.

- **Branch associates** work in the Return Business Copilot (`/returns`), taking a return
  from an utterance through to resolution on one operational screen.
- **Support engineers** work the Channel B conversation (`/support`, RMA tickets, work
  queue): return requests, and the replies to them.
- **Platform operators** work Operations, Configuration, Approvals, Source Sync, the AI
  Control Center and the Graph Schema Analyzer — running the platform rather than
  raising returns.

All three work at desks on displays of 1280px and wider. See Capabilities and
Constraints for what the narrower viewports are actually for.

## Product Purpose

Production return orchestration. An associate describes a return in their own words; the
platform identifies the order against the complete graph corpus, commits exactly one case,
and runs it to resolution under a durable workflow — bay assignment, a support
conversation on business-calendar time, RMA and item persistence, targeted graph sync, and
shipment truth read back through the graph.

Success is that a return reaches durable, queryable truth exactly once: one case, one
workflow, N RMAs, N items in SQL, and a graph the associate can ask "did the RMA come
through" and get a true answer from.

## Positioning

Five mechanisms a neighbouring returns product could not truthfully claim:

- **Discovery reads the whole corpus, not a window.** A misspelled customer name resolves
  against a quarter-million rows and returns the right one ranked first, including when it
  sits 84,000 deep. The bounded probe this replaced could not see it.
- **Identification fields are runtime configuration, not code.** Which fields identify an
  order is a configured decision the platform reads at turn time.
- **One confirmation commits exactly one case and starts exactly one workflow** — under
  retry, under concurrent confirmation, and after a client timeout where the first request
  already committed.
- **SQL is authoritative for return records; Mongo is a derived projection** driven by an
  outbox event emitted only after commit. A queued projection reads as pending, never as
  truth.
- **Every discovery read is pinned to its serving generation.** The graph answers from
  `ActiveRuntimeSnapshot`, so a generation change mid-conversation cannot silently swap
  the corpus underneath a candidate set.

## Operating Context

Nine domains plus a platform landing page, each gated on the cheapest read capability in
that domain:

| Domain | Route | Capability |
|---|---|---|
| Return Business Copilot | `/returns` | `returns.session.read` |
| Returns Support | `/support` | `returns.session.read` |
| Operations | `/operations` | `config.runtime.read` |
| Configuration | `/config` | `config.runtime.read` |
| Approvals | `/approvals` | `governance.proposal.read` |
| Data Sources | `/data-sources` | `config.source.read` |
| Source Sync | `/sync` | `config.source.read` |
| Graph Schema Analyzer | `/graph-schema` | `graph_schema.draft.read` |
| AI Control Center | `/ai` | `ai.request.read` |

Capability gating controls **visibility only**. The backend refuses regardless of what the
navigation shows — a screen appearing is not an authorization decision.

Work is scoped by tenant and branch. Support waits run on a business calendar, not wall
clock: a return raised at 16:30 on a Friday chases Support on Monday morning rather than
spending its reminders into an empty queue overnight.

## Capabilities and Constraints

**Viewport reality.** The shell supports 320–1440px and turns the navigation rail into a
drawer below `lg`. Confirmed 2026-08-24: that range exists to satisfy WCAG 1.4.10 reflow
and 200% zoom — a 1280px display at 200% zoom is a 640px viewport — **not** to serve
tablet or phone use. Real sessions are 1280px and wider. Small-screen work is a
correctness obligation, not a usage scenario.

**Source systems are read-only.** Suggested changes can target only the system graph.

**Tracking is observed, never inferred.** `dbo.return_tracking` is written only from a
real tracking observation carrying the required event facts. Issuance never invents a
tracking type or an event time.

**Unknown status stays unknown.** Legacy or null return status maps to `UNKNOWN`, never to
`ISSUED`. A return cannot present as issued without durable items.

**An unconfigured platform refuses.** With no active return configuration the case routes
answer 503 rather than projecting from a constant, because an unconfigured platform that
quietly answers from a default is indistinguishable from a configured one until a return
completes without its paperwork.

**Explicitly undecided:** the accessibility standard (see below).

## Brand Commitments

The product carries two names in the shipped UI, and they disagree: the shell breadcrumb
says **Returns Intelligence Platform** while the document title says **Return Platform
Console**. Recorded as an observed fact for a naming decision to resolve, not as an
approved pair.

**Voice, as evidenced in shipped UI copy.** Terse, declarative, and names the mechanism
rather than praising it — "Discovery through resolution, one operational screen"; "Every
change waiting on a human decision, in one queue"; "Sync runs, what each one read, and
what it wrote to the graph." Refusals say what is true and what to do: "Not an address a
label could be sent to. Correct it, or leave it empty."

No palette, typography or visual direction is recorded here; that is not product truth.

## Evidence on Hand

- **Per-screen functional documentation** — `docs/screens/`, one document per screen
  covering purpose, regions, actions, APIs consumed, live-state behaviour,
  loading/error/empty states, persistence, audit effects and known constraints.
- **Canonical runtime flow** — `docs/architecture/canonical-runtime-flow.md`.
- **Security boundaries** — `docs/architecture/security-boundaries.md`.
- **Configuration families** — `docs/configuration/families.md`.
- **A deep UI and functional audit returning NO-GO on 30 findings**, with a remediation
  ledger recording per-finding closure and evidence at
  `docs/execution-context/remediation/LEDGER.md`.
- **Live-infrastructure test evidence** — 504 selected tests; 448 passed / 2 failed /
  0 errors measured 2026-08-23 against real Mongo, SQL Server, Neo4j and Temporal.
- **Real seeded non-customer order data** used for live validation (orders `CA273603`,
  `CW273354`), with captured case and workflow identifiers.

**Absences future work must not fabricate:** there are no testimonials, named customers,
case studies, press, pricing, licensing terms, benchmarks, uptime figures or deployment
claims. None have been established, and none may be invented to fill a layout.

## Product Principles

1. **Every fact has one home.** SQL owns return records; `ActiveRuntimeSnapshot` owns
   graph serving. A projection is never permitted to look authoritative, and a surface
   that shows one says so.
2. **Refuse rather than guess.** An unconfigured platform, an unresolvable candidate and
   an unobserved tracking event all produce a refusal that says what is missing — never a
   plausible default.
3. **Exactly once is a product promise.** Retry, concurrency and client timeout are
   ordinary conditions, not edge cases, and the interface must not imply a second
   submission is needed.
4. **Authorization is the server's.** Navigation hides what a principal cannot read as a
   convenience; nothing in the interface may read as the decision itself.
5. **Behaviour is configured, not coded.** Identification fields, business calendars,
   return-method requirements and policy all resolve from the active release at runtime,
   so the interface must show which release it is answering from.

## Accessibility & Inclusion

**No standard has been established.** Confirmed 2026-08-24 as an open decision, deliberately
recorded rather than assumed.

What exists today is engineering practice, not a commitment: a skip link to `#main-content`
on both shell frames, landmark structure, a global `:focus-visible` ring, `prefers-reduced-motion`
handling that keeps the spinner turning slowly because a frozen spinner reads as a hung
screen, implicit label association enforced by test, and colour tokens whose text pairs were
verified against WCAG 1.4.3 (all 18 pass; `outline` was moved to `#5b6664` at 5.66:1 to fix
119 failing nodes across 39 routes).

Known open gaps as of 2026-08-24, from `/impeccable audit`: a single static `<title>` serves
roughly 40 routes (WCAG 2.4.2, Level A), and 17 of 49 form controls carry a boundary below
3:1 (WCAG 1.4.11) despite an `outline-control` token existing for exactly that purpose.

Deciding the standard changes their severity, which is why it is recorded as a decision
rather than inferred from the work already done.
