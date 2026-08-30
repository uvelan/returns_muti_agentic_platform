# Merge state — orchestrator record

Base: `a50c5500788f99e909f23099a81731b37c736b8c` (`refactor/unified-return-platform`).
Planned order: `T0 → S1 → S2 → V1 → V2 → V3 → ACC`, RV `PASS` (zero unresolved findings) required between every arrow.

**Deviations in force.** (1) User-directed ≤3 parallel agents, so slices are pipelined off the preceding slice's *candidate* head rather than its merge, with rebase instructions if review forces changes; every pipelined base has so far matched the eventually-approved head. (2) Large slices are split into phases when a later phase depends on an unbuilt interface — V1 and V2 are both split, and both S1 and V2 gained a phase 1b after review or integration exposed a gap. (3) ACC phase 1 was cut early because its scope is test-only and independent.

**File-handling note:** edit `.plan/*.md` with an editor, never a shell string-replace. Two rounds of UTF-8 mojibake came from PowerShell `-replace` on these files.

## Slice status

| Slice | Branch | Status | RV rounds | Merged at |
|---|---|---|---|---|
| T0 | (trunk) | DONE | — | 2cafe2a |
| S1 | feat/s1-model-identity | **MERGED** | 1 · PASS (6bdb5bd) | 5d58b90 |
| S1 phase 1b (`actorId`) | feat/s1-actor-id | **MERGED** | 1 · PASS (98a180e) | 132b031 |
| S2 | feat/s2-delivery-spine | **MERGED** | 3 · CR → CR → PASS (b7a78ad) | dfd3036 |
| V1 phase 1 | feat/v1-template-review | **MERGED** | 2 · CR → PASS (18f671f) | b2590ef |
| V1 phase 2 | feat/v1-phase2 | CHANGES_REQUIRED → fixing · was 3437187 | 1 · CR (85db125) | — |
| V2 phase 1 | feat/v2-ingress-relay | **MERGED** | 2 · CR → PASS (02da231) | 97bca1e |
| V2 phase 1b | feat/v2-ingress-relay | UNDER_RV_REVIEW · candidate 04caf5d | 1 open | — |
| V2 phase 2 (frontend) | (same branch, later) | BLOCKED on V1 panel seam | — | — |
| V3 backend | feat/v3-resolver-clarification | awaiting RV round 2 · candidate aa6056c | 1 · CR (3d8715f) | — |
| V3 frontend | (same branch, later) | BLOCKED on V1 panel seam | — | — |
| ACC-1 (harness) | feat/acc-harness | **MERGED** | 2 · CR → PASS (9cb3508) | c1c2b0f |
| ACC-2 (scenarios) | not yet cut | BLOCKED on V3 | — | — |
| RV calibration | rv-calibration/seeded-hardcoding | bait CAUGHT as blocking | 1 (d59e017) | never merges |

**Six merges on trunk:** S1, S1b, ACC-1, V1 phase 1, S2, V2 phase 1.

## Contract amendments (all in `.plan/contracts.md` §1a)

Five so far. **Four of the five are T0 errors — things I froze that did not survive contact with implementation**, and every one was caught by someone trying to build or wire the thing rather than read about it.

| # | What | Raised by |
|---|---|---|
| 1 | `literal:` admitted as a fourth binding source (enumeration amended, implementation not excused) | RV, V1p1-1 F3 |
| 2 | `return_record:` constrained to declared projection attributes (was unconstrained `getattr`) | RV, V1p1-1 F4 |
| 3 | Ingress path moved to `/inbound-messages` — **the frozen path was already served by two live handlers** | integration agent |
| 4 | omc mirror restated as **eventually once, not atomically once** — the transaction it named does not exist | V2 phase 1b |
| 5 | Recovery: retry requires a live execution; the gate moves non-terminal reviews to `HELD_FOR_OPERATIONS` on close — **the operator's own recovery action was building a permanent trap** | V1p2 + RV V1p2-1 F2 |

## Orchestrator errors (the run's discipline applies to me too)

1. **Four frozen mechanisms failed on contact** (amendments 1–5 above, plus the V1 brief's governance-proposal write path, which that endpoint refuses for a non-agent module).
2. **A stale trunk sha in the S1 phase-1b dispatch** (`24e01b1`, 122 commits behind). The slice verified it was an ancestor and followed the words over the number; RV noted branching there would have failed unmissably rather than subtly.
3. **I described unmerged branch state as trunk state** to V3 — claiming V2 phase 1b had removed a required keyword. It had not; that work is on a branch. V3 checked rather than trusted and was right. **Rule: name the branch an interface claim comes from.**

## Recurring failure shapes (the run's checklist, earned by injection)

Every slice has shipped at least one green-but-blind test. The shapes found so far:

- **A double that accepts anything proves nothing** — `**fact`/`**kwargs` doubles hid five dead fact writes in V1p2 and still hide the fifth (V1p2-1 F1).
- **A consumer tested against a synthetic producer** — V1p2's step-14 tests signalled the workflow by hand, so nothing proved revise/cancel/redraft had a producer; redraft was completely unreachable.
- **Nobody stands at the seam** — S2 tested its payload, V1 tested its notice, and the approval signal failed to decode silently between them.
- **Green because the inputs can't exercise the property** — the entry-id collision test passed on the separator, not the length prefixes, and was believed fixed three times; adjacency was necessary but not sufficient.
- **The instrument flattens into the answer** — V2's drain loop reset every command to one `nextAttemptAt`, so the tie-break fell through to enqueue order.
- **Two things equal by construction** — V2's artifact-gate test compared the invoker's answer with the written artifact.
- **The documented reason is not the operative one** — V3's neutralise-then-bound ordering was safe because of a *space* in the truncation joiner; swapping the documented order changed nothing.
- **A negative assertion** — "does not contain" passes for the wrong reasons; pin the whole composed output as an equality.
- **Skipped on the platform that runs it** — ACC's behavioural stop/kill pin is Windows-skipped, so a structural pin was added beside it.

## Integration debt (orchestrator applies at merge)

- **V2 — item 3 done** (`42b8b60`, `ensure_support_ingress_indexes` wired). Items 1, 2, 4 stopped and returned, correctly, and produced amendments 3 and 4. After V2 phase 1b merges: mount the router (+ mount-count bump), register the dispatcher via the new `build_support_message_classify_dispatcher(...)` one-liner, regenerate OpenAPI — checking that **both** associate endpoints on `.../messages` survive alongside the new `.../inbound-messages` path.
- **V3:** mount `case_clarifications` router; wire `ResolutionInvokerPort`, `GraphSyncPort`, `GraphReadPort`, `CaseFactsPort`, and `ToolExecutor`'s `contracts` allowlist. **Once V1 phase 2 lands, V3's `clarification_answered` workflow signal handler must be written and the reminder-cadence assertion unblocked** — V3 correctly declined to invent V1's contract.
- **`CaseFactProjection` drops `actorId`** (`case_projection/contract.py:154`) so the REST view shows a fact without its principal. RV ruled deferral correct — the projection already omits `turnId`, `correlationId`, and phase 1's own `record_scope`/`identity_version` — but a UI asking "who authorised this" gets "nobody". Two lines plus regeneration of five pinned artifacts.
- **OpenAPI regen is six files, not one:** `npm run contracts:generate` covers `frontend/openapi/…json` + the `.d.ts`; repo-root `scripts/check_openapi_drift.py --write` then covers `openapi/`, `backend/openapi/`, root `openapi.json` and the evidence receipt.
- **`return_configuration.py` conflicts on every slice merge** — each slice appends one config field and import. Resolution is always "keep both", verified by loading the model.
- **`test_main_is_composition_only`** is at 35 vs an expected 33 on trunk from two undocumented pre-existing mounts (`shipment_console_router` `7585b38`, `template_preview_router` `176f1d5`). Not any slice's to adjudicate; fix the expectation when the last router mounts.

## Queued follow-up dispatches

- **V3 adopts `actorId`** — `operations/return_support/clarification.py` (~172): add `actor_id=answer.actor_id`, delete `"answeredBy"` from the value, fix the stale docstring and any test reading it. *Not* `api/canonical_ai.py`'s `answeredBy` — unrelated pre-existing surface.
- **V1 phase 2 adopts `actorId`** — sent with its fix round.
- **RV will check three things on both adoptions:** `actor_id` bound explicitly in the double, an assertion on the **stored camelCase key**, and the old value-level spelling **gone** — otherwise both coexist and the migration never happened.
- **Trap:** `ScopedFactAppendPort` is `(*, record_scope, **fact)`, so `actor_id` type-checks *through* `**fact` — a double captures `fact["actor_id"]` while production stores `actorId`. S1 cannot close this (no signature stops a consumer's `**kwargs`); RV converted it into the acceptance conditions above. Advisory: S1's stated reason for leaving the Protocol alone ("narrowing isn't additive") is wrong — a keyword-only param with a default is accepted by every `**fact` implementer, and would at least put `actor_id` in the contract a consumer reads while writing the double.

## Carry-forward conditions (must be written into the named future brief)

### Into the ACC phase-2 brief
1. **Business-time scenarios must assert, not assume.** The Mon–Fri fixture's id is deliberately not `default`, which prevents *shadowing* — but a scenario that never calls `with_business_calendar` silently inherits the 24/7 dev calendar and stays green while proving nothing. Assert `calendar_applied is True` or `not …is_continuous`.
2. **Run the never-executed safety nets first.** `test_chaos_restart_smoke_real_infra.py` has never run (datastores down), and the behavioural SIGTERM pin is Windows-skipped — RV narrowed its one unproven link to whether `os.killpg(getpgid(pid), SIGTERM)` reaches the child through the session established at launch. The smoke test must be the **first** thing run when the stack comes up.
3. **Acceptance 18's ordered drain rests on V2 populating the causation chain.** Assert the chain, not just the drain.

### Into every brief composing outbound Channel B text
4. **Neutralise associate- and support-authored text.** V1 phase 1 found that binding a raw `associate_notes` fact dropped composition's neutralisation, letting a `BAY ASSIGNMENT:`-shaped line restructure the message. Composition `_safe`s exactly four values (`associate_notes` + three `contact_*`) — match that parity. V3 owns this entirely now (V2 phase 1 composes no outbound text). §9's tool-safety principle applied to message *structure*.
5. **Render support-derived values as data, never markup.** `artifact.value` reaches associate-facing text; V2 phase 1b added structural bounds (256/128), but the rendering side must escape rather than interpret.

### Retirement (post-gate, RV-gated only)
6. `return_details.additional` and `bay_handling_instructions` are `_clean`-only in **both** paths — no regression today (and `additional` has no producer at all), but once composition retires, a release populating it from associate-typed text would carry framing through.
7. Per-route pinning needs a route constraint on `StructuredOutputInvoker` (shared AI-gateway code, outside V2's ownership); `block_exhausted` currently fires after candidate-count attempts rather than genuine provider exhaustion.
