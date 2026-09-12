# RV — CFG-5b @ 6df39c0f — VERDICT: PASS

Round 2, read-only, over `feat/cfg-5b-agents` head `6df39c0f` ("(CFG) step:09 RV round 1 fixes",
ledger `CFG-5b step:09`) against round 1's `d9a86886`. Round 1's verdict was CHANGES_REQUIRED on one
blocking finding; all five findings taken this round are fixed at the cause, and the one carried
advisory is recorded with a fix location. Environment re-confirmed first:

```
$ PYTHONPATH=<wt>/backend/src backend/.venv/Scripts/python.exe -c "import return_platform; print(return_platform.__file__)"
K:\Projects\Ret\returns_muti_agentic_platform\.claude\worktrees\cfg-5b\backend\src\return_platform\__init__.py
```

## Round-1 findings and their disposition

| ID | Severity | Round-1 summary | Disposition @ 6df39c0f |
|---|---|---|---|
| **F1** | **BLOCKING** | Activation cloned `RETURN_PLATFORM` from this process's runtime snapshot (`released_return_platform_document()` over `main.py`'s `active` closure), then handed it whole to `publish_release_with_domains`, which replaces a domain whole — so a release published by another process between proposal and activation was silently reverted across every key of the domain. | **FIXED at the cause, verified behaviourally.** `governance_agent_configuration.py:108-128` now reads `self._repository.get_active_release()` → `get_domain_config(active.release_id, RETURN_PLATFORM_DOMAIN_KEY)`, with both refusals (`no active release`, `carries no RETURN_PLATFORM domain`) spelled out, and patches `agents[subject_id]` on that. This is `governance_improvement.py:155-167`'s shape exactly, as the fix was asked to be. `released_return_platform_document()` is **gone** — grep confirms zero references (the only remaining match is `main.py:598`'s unrelated local closure of the same name, which feeds the read-only `active=`). My own round-1 probe now reports the opposite result (pasted below). The new regression test genuinely disagrees between the two sources — see the note under the table. |
| A1 | ADVISORY | Save button's `disabled:opacity-40` on `bg-primary`/`text-on-primary` → disabled label at 2.12:1; CFG-5's carried H1/H2 never landed. | **TAKEN.** `AgentsSection.tsx:315-321`: no opacity utility; disabled state is `disabled:bg-surface-container-low disabled:border-outline-variant disabled:text-on-surface-variant disabled:shadow-none disabled:hover:brightness-100` over a now-explicit `border border-transparent`. Re-measured from the shipped tokens: **disabled label 8.43:1** (was 2.12:1), enabled unchanged at 9.63:1 — and H1's actual complaint is answered too, since the two states differ by fill *and* border, not only by cursor. H2's family assertion added at `AgentsSection.test.tsx:164-171`: no `opacity-\d` at all, and the dimming must land on `disabled:bg-*` and `disabled:border-*`. |
| A2 | ADVISORY | `api/agents.py` docstrings still cited `manifest.yaml` and "the loader's own reason". | **TAKEN** (`api/agents.py:9-12`, `:161-165`) — now names the release's `agents.<id>` entry and `AgentConfiguration`'s own validation message. The four OpenAPI copies and the generated `.d.ts` were regenerated for the moved `description` strings; the diff is description text only. |
| A3 | ADVISORY | `backend/config/README.md` header ("manifest-driven") and the `dynamic_knowledge/` bullet contradicted the same file's own "read by nothing" section. | **TAKEN** (`backend/config/README.md:3`, `:107-113`). The replacement claim checks out: `Settings.dynamic_knowledge_schema_path` exists (`settings.py:74`) with default `config/dynamic_knowledge/active-schema.return-order.yaml` (`settings.py:22-24`), and both files named in the bullet are on disk. |
| A4 | ADVISORY | After a successful save the row stayed dirty, so a second click filed a duplicate proposal. | **TAKEN** (`AgentsSection.tsx:226-235`, `:253-255`): a `savedSnapshot` baseline starting at `loaded` and moving to the just-saved draft on success, so `dirty` — and Save — go false the moment the proposal is filed and true again on the next real edit. Covered twice: an inline assertion in the existing save test and a dedicated test (`AgentsSection.test.tsx:205-224`) that clicks again and asserts no second call, then re-edits and asserts Save re-enables. |
| A5 | ADVISORY | An agent-activated release writes no `CONFIGURATION_*` record to the audit stream `GET /api/config/audit` serves. | **CARRIED**, as recommended. Ledger step:09 and `drop.json.rv_round_1.carried` both record it against CFG-7 with a concrete fix location (a `Request`-free audit helper called from both `publish_release_with_domains` callers). Pre-existing, not a regression of this lease. |

**The new regression test does make the two sources differ.**
`test_a_concurrent_release_survives_agent_activation` (`tests/configuration/test_agent_configuration_releases.py:371-468`)
seeds `baseline`, captures its `RETURN_PLATFORM` document as the *stale* one, then publishes a
second release `concurrent-release` with `support.external_mirror_enabled` flipped and asserts it is
the active release. The service under test is constructed as
`AgentConfigurationService(active=lambda: stale_snapshot)` — pinned to the pre-concurrent document,
never re-read — while the repository has moved on; the assertions are that the agent edit landed
**and** `published["support"]["external_mirror_enabled"] is concurrent_value`. On round 1's code this
test fails on that second assertion; it is not a restatement of the existing fixture, which could
never disagree with itself. The suite's own docstring says so in the same words.

## Pasted output

F1, my round-1 probe re-run unchanged against `6df39c0f` (stale snapshot vs. newer active release):

```
active release before activation: concurrent
active         support.external_mirror_enabled = True
stale snapshot support.external_mirror_enabled = False
published release: agent-config-proposal-6d03260d-dab0-471f-b78c-563cd2d2e5cb
published      support.external_mirror_enabled = True
agent edit applied: False
VERDICT: concurrent release preserved            # round 1: "REVERTED by the agent activation"
```

A1, recomputed from `tailwind.config.js` (`primary #004e47`, `on-primary #ffffff`,
`surface-container-low #f1f4f2`, `on-surface-variant #3e4947`, `outline-variant #bec9c6`, panel `#ffffff`):

```
enabled Save  (on-primary on primary):          9.630
disabled Save (on-surface-variant on s-c-low):  8.432      # round 1: 2.122
disabled Save fill vs panel (non-text cue):     1.107      # the state is now visible without the label
disabled Save border vs panel (non-text cue):   1.699
read-only fieldset opacity-75, on-surface:      7.558      # untouched, and above the floor
```

Acceptance, all re-run at `6df39c0f`:

```
$ pytest tests/configuration tests/api tests/test_configuration_api.py tests/test_graph_configuration_bootstrap.py tests/platform -q
955 passed, 35 deselected, 2 warnings in 179.79s (0:02:59)

$ pytest <the nine known-failing modules> -q -p no:cacheprovider
42 failed, 112 passed in 41.85s        # the same pair as .plan/reviews/CFG-1.md's base measurement, and as round 1

$ ruff check <7 touched files>           All checks passed!
$ ruff format --check <7 touched files>  7 files already formatted
$ mypy <5 touched source files>          Success: no issues found in 5 source files

$ python scripts/check_openapi_drift.py
"diffs": [], "status": "PASS", "exit_code": 0
$ sha256 (first 16) of the four snapshots
b4c8e433a611d0ac  openapi.json
b4c8e433a611d0ac  openapi/return-platform.openapi.json
b4c8e433a611d0ac  backend/openapi/return-platform.openapi.json
b4c8e433a611d0ac  frontend/openapi/return-platform.openapi.json

$ npx vitest run
 Test Files  90 passed (90)    Tests  1081 passed (1081)      # +2, A1's and A4's
$ npm run typecheck   exit 0
$ npm run lint        exit 0
$ git status --short  (clean)
```

## Scope and record

Round 2 touches only round 1's own surface plus the review and ledger: the activator, the service,
`api/agents.py`, `backend/config/README.md`, the agent-release tests, `AgentsSection.tsx` and its
test, the regenerated OpenAPI/`.d.ts`, the ledger and `drop.json`. No new file, and still nothing
near the bootstrap CLI or `packaged_adoption.py`. One cosmetic lag, not a finding and consistent
with every prior lease in this track: `drop.json.head_sha` names `d9a86886`, the commit before the
one carrying the fixes (`merge_status` is still `PENDING`, and the round-1 block inside it is
accurate).

The one item still genuinely outstanding is unchanged from round 1 and structural: the live
`propose → activate → runtime reflects → revert` e2e cannot run until this branch merges and the
shared `:8000` API restarts onto it. The spec is written, typechecks, lints, and drives the right
calls; that run is the orchestrator's post-merge step, per CFG-6's precedent.

## Judgement

The blocking finding came back fixed where it was caused, not papered over: the activator now asks
the graph what the active release holds instead of asking its own process, the method that made the
wrong answer available is deleted rather than left to tempt the next caller, and both module
docstrings now say which source is legitimate for which job — a read path may answer from this
process's snapshot, a publish may not. The regression test is the one the round-1 suite structurally
could not contain, and it fails on the old code for the right reason. The four advisories were taken
in the same spirit: A1 dims through a channel that is not the label's own contrast and asserts the
family rather than the spelling that caused it, discharging CFG-5's H1/H2 at last; A4's
`savedSnapshot` fixes duplicate proposals at the state rather than by disabling a button on a timer;
A2 and A3 leave no sentence in the tree still pointing at the manifest system this lease deleted.
Every acceptance number reproduces at the new head, the nine known failures are byte-identical to
CFG-1's base set, and the four OpenAPI copies hash identically. Nothing blocking remains.
