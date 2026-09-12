# RV — CFG-6 @ 064cb96c — VERDICT: PASS

Round 2, read-only, from `.claude/worktrees/cfg-6` (branch `feat/cfg-6-deployment-section`, head
`064cb96c`, content `a175b6ce`, base `59950920`; round 1 reviewed `e967c292`). Ledger entry
`CFG-6 step:12`. No code edited, no launcher or bootstrap run, no write to the live graph or API.

All four blocking findings are fixed at the cause, each with a test I verified independently has
teeth — twice by reproducing the original bug against the fixed tree (F1's rebind, F4's baseline
fallback) and twice by running the new guard's own negative control myself (F2's AST scan on a
reverted copy, A2's shield). A1-A4 were taken as full fixes. Nothing regressed: the acceptance set
goes 1002 → 1013 passed with the same five pre-existing failures by name, and the live simulation is
unchanged.

## Round 1 findings — dispositions

| ID | Sev | Disposition | Verified how |
|---|---|---|---|
| F1 | BLOCKING | **Fixed.** `FeedbackLearningService.__init__` now takes a `SettingsSource` (new structural `Protocol`, `feedback_service.py:43-61`), holds `self._resources`, and `_enabled` reads `self._resources.settings.feedback_learning_enabled` at call time (`:152-154`). The false comment ("reassigns… in place") is replaced with the actual mechanism. The one call site, `orchestrator.py:204-206`, passes `SettingsSnapshot(settings)` — a `SettingsSource` that never changes — and says plainly in situ that this is a snapshot, not a live source, and what a future wiring site must pass instead. | Reran my round-1 rebind reproduction against the fixed tree: the service's next read now follows the rebind. Output below. `tests/operations` 1137 passed (the `ReturnOrchestrator` construction site is in there). |
| F2 | BLOCKING | **Fixed.** `tests/operations/test_feedback_learning_settings_source.py` exists — four tests: the behavioural proof, a negative control, the AST guard over all of `backend/src` refusing a bare `settings` name at a `FeedbackLearningService(...)`/`ReturnOrchestrator(...)` call site, and a positive assertion that the one call site wraps. `families.md` footnote (b) now cites all three by name, and no longer claims the hot-adopt reaches production traffic — it says the opposite, correctly. | Ran the file: 4 passed. Verified the guard's teeth **myself**, not on the implementer's word: ran its own AST predicate against a scratchpad copy of `orchestrator.py` with the wrapper reverted → `['orchestrator.py:204 (FeedbackLearningService)']`, guard would fail. The guard also pins `orchestrator_calls == 0` / `feedback_calls == 1`, so its silence cannot be mistaken for coverage. |
| F3 | BLOCKING | **Fixed.** The tautology is gone; `test_deployment_first_publish_seeds_from_env:302-310` now asserts the sorted `deployment.*` baseline keys equal the four unit names, and the false comment is replaced with the true reason (`recordable_baseline` keeps its initial `_key_digests(packaged_units)` value when there is no active release). | The asserted list matches what my live simulation independently computes for a first publish (four unit digests, below). |
| F4 | BLOCKING | **Fixed.** New `overlay_env_deployment_defaults(configuration, settings)` (`deployment_settings.py:243-276`) — the same `deployment_payload_from_settings` + `merge_deployment_defaults` seam bootstrap uses, applied to the in-memory baseline — called on **both** fallback paths before `build_snapshot` (`runtime_loader.py:102`, `main.py:514-520`). No-op when the env set nothing. Four tests including an end-to-end run through the real `ConfigurationSnapshotBuilder` seam and a negative control. | Reread `snapshot.py:109-195`: `default_configuration` is consumed **only** by `_baseline_snapshot` on the exception path, so the overlay cannot touch the normal graph-driven path — the implementer's safety claim holds. Then reran my own reproduction through the real seam, pre-fix and post-fix. Output below. |
| A1 | ADVISORY | **Fixed.** Two tests for a failure strictly after promote-to-VALIDATED: `/publish` faults the VALIDATED audit write, `/adopt-packaged` faults `promote_configuration_release` on its RELEASED call only. Both assert ARCHIVED **and** head unchanged. | Read `releases.py:872-892` to confirm the injection point really sits between the two promotions — it does. `calls == ["VALIDATED", "RELEASED"]` in the second test pins that the fault lands after VALIDATED succeeded. |
| A2 | ADVISORY | **Fixed.** `_archive_draft_on_refusal_shielded` (`releases.py:1064-1086`) wraps the archive in `asyncio.shield` and swallows only the `CancelledError` the shield delivers back, so the caller's bare `raise` still re-raises the real refusal. All four call sites go through it. Tested at the mechanism, with the HTTP-level route documented as untestable through `BaseHTTPMiddleware`. | Verified the negative control **myself**: cancelling the caller mid-archive leaves the release `DRAFT` unshielded and `ARCHIVED` shielded. Output below. Reasoning also checks out — `_archive_draft_on_refusal`'s `except Exception:` never caught `CancelledError`. |
| A3 | ADVISORY | **Fixed.** The test whose own docstring conceded it guarded nothing is removed (with a comment at its old site pointing at the replacement), and `test_packaged_domain_payloads_seeds_deployment_from_the_bootstrap_snapshot_not_app_state_settings` drives the **real** call site through `POST /api/config/adopt-packaged`, with three distinct provider orders so the assertion can only pass for the right source. | Read the test: `app.state.settings` → `ANTHROPIC`, `packaged_deployment_defaults` → `GOOGLE,NVIDIA`, packaged file → `GOOGLE,NVIDIA,SIMULATOR`; asserting `["GOOGLE","NVIDIA"]` distinguishes all three. It also incidentally proves `deployment.ai` is a valid `--adopt-packaged` unit through the API. |
| A4 | ADVISORY | **Fixed by rewording**, with the reason recorded (an include/exclude affordance is new capability on a primitive three other screens share; raw JSON already does add/remove). The field description now says the control "reorders the providers already listed" and names Advanced (JSON) for adding, removing or restoring one; the module docstring says the same for the SIMULATOR/MANUAL inline badge vs. a native disabled state. | Confirmed "Advanced (JSON)" is the literal button label in `TypedSectionScreen.tsx:187`, so the copy points at a control that exists under that name. |
| A5 | ADVISORY | **Closed** — it was A4's other half; the reword names the escape hatch. | — |
| A9 | ADVISORY | **Fixed in passing** — the misplaced `#:` block moved onto `_governed_ai_providers`'s own docstring, where it belongs. | Read the diff. |
| A6 | ADVISORY | **Carried**, recorded in `drop.json.remaining`. No HTTP/startup-layer test for the 422 or the `RuntimeError`; the paths are correct (I exercised them by hand in round 1) and covered at the `validate_deployment_for_environment` layer. | — |
| A7 | ADVISORY | **Carried**, recorded. `EnumSelect`'s `disabled`/`disabledReason` still covered only through `DeploymentSection.test.tsx`. | — |
| A8 | ADVISORY | **Carried**, recorded. No axe/`canonical-routes.spec.ts` run for `/config/deployment`. | — |
| A10 | ADVISORY | **Carried**, recorded. The live no-restart proof is the orchestrator's to run after PASS — not RV-satisfiable from a read-only worktree, and the lease's own rules forbid running launchers here. | — |
| A11 | ADVISORY | **Carried**, recorded. Production's whole-`deployment` gate on every RETURN_PLATFORM publish is still undocumented. | — |

No new findings this round.

## Pasted outputs

**F1 — the rebind now reaches the service** (round 1 read `True` on the third line):

```
before activation, svc._enabled      : True
activated is a NEW object            : True
after  activation, svc._enabled      : False  <-- round 1 read True here
SettingsSnapshot (orchestrator site) : True (static by construction, as documented)
```

```
$ pytest tests/operations/test_feedback_learning_settings_source.py -q
4 passed in 2.23s
$ pytest tests/operations -q          # the ReturnOrchestrator construction site lives here
1137 passed, 126 deselected in 55.87s
```

**F2 — the AST guard's teeth, checked by me against a reverted scratchpad copy:**

```
AST guard on a reverted copy -> violations: ['orchestrator.py:204 (FeedbackLearningService)']
guard would FAIL: True
```

**F4 — my round-1 reproduction, rerun through the real `build_snapshot` seam, both ways:**

```
env  ai_provider_order            = GOOGLE,NVIDIA
PRE-FIX  (packaged, un-overlaid): source= VERSION_CONTROLLED_BASELINE | after apply -> GOOGLE,NVIDIA,SIMULATOR
POST-FIX (runtime_loader/main.py): source= VERSION_CONTROLLED_BASELINE | after apply -> GOOGLE,NVIDIA
```

**A2 — the shield's negative control, run by me:**

```
UNSHIELDED (pre-A2) -> DRAFT
SHIELDED   (post-A2) -> ARCHIVED
```

**Acceptance set (the brief's, plus the new file):**

```
$ pytest tests/configuration tests/api tests/test_configuration_api.py tests/test_graph_configuration_bootstrap.py \
    tests/test_ai_gateway_routing.py tests/test_ai_gateway_policy.py tests/platform \
    tests/test_outbox_dependency_dispatchers.py tests/test_worker_runtime_activation.py \
    tests/test_ai_route_balancing_design.py tests/test_ai_entry_points_share_one_path.py \
    tests/test_ai_single_dispatch_boundary.py tests/test_openapi_contract_drift.py \
    tests/operations/test_feedback_learning_settings_source.py -q
5 failed, 1013 passed, 35 deselected, 2 warnings in 155.02s (0:02:35)
FAILED tests/test_ai_route_balancing_design.py::test_candidates_are_provider_balanced_and_rotate_within_provider
FAILED tests/test_ai_route_balancing_design.py::test_order_agent_fails_over_to_next_provider_and_logs_attempts
FAILED tests/test_ai_route_balancing_design.py::test_order_agent_escalates_to_lightweight_tier_when_standard_exhausted
FAILED tests/test_ai_single_dispatch_boundary.py::test_the_cacheable_prefix_is_byte_identical_across_turns_and_modes
FAILED tests/test_ai_single_dispatch_boundary.py::test_no_per_turn_value_is_copied_into_the_system_prompt
```

The same five, by name, as round 1 — which I confirmed then against the base `59950920` in a
throwaway worktree (`5 failed, 27 passed`, same names). 1002 → 1013 passed is exactly the eleven
tests this round adds net (4 feedback-source + 4 F4 overlay + 4 API, less the 1 A3 removal).

**Lint / types / drift / OpenAPI copies:**

```
$ ruff check <27 lease-touched backend files>   -> All checks passed!
$ ruff format --check <same>                    -> 27 files already formatted
$ mypy <17 touched backend source files>        -> Success: no issues found in 17 source files
$ python scripts/check_openapi_drift.py         -> "diffs": [], "status": "PASS", "exit_code": 0
$ sha256sum of the four copies                  -> 87c50bc4…26ada, identical, all four (unchanged)
```

**Frontend:**

```
$ npx vitest run
Test Files  89 passed (89)      Tests  1069 passed (1069)
$ npm run typecheck -> exit 0        $ npm run lint -> exit 0
```

## Live simulation, redone read-only (nothing written)

Live head 135, `RELEASED publish-cbb472ad451d468d`, `deployment` still absent, 20 baselines none
naming it. With this round's code:

```
undecided RETURN_PLATFORM = ['agents', 'clarification_policy', 'policy_evaluation',
                             'return_eligibility_policy', 'return_policy', 'support_ingress']
recordable baseline entries for deployment.* = {
  "deployment.ai": "e9f97bc1b547a5627cba317159270be19ecccbc3a7c21b81addda9ff2e3bf255",
  "deployment.dependencies": "dfa4d2c74b98313483a41907a1425cc977924d4053acec24cd975a2d3eb80cd0",
  "deployment.feedback_learning": "26b3426b2593763c96d0890b4a77a0bbf66d13fc512b0c6b138a23c290f30a2a",
  "deployment.support_ticket": "d06ca618f45e09790871d12359eac6636e82a2372b6946d3b02ea976bc623413" }
model_validate PASSES; resulting deployment.ai.provider_order = ('GOOGLE', 'NVIDIA')
resulting deployment.dependencies = omc='SIMULATED' parcel='SIMULATED' freight='SIMULATED' lsi='SIMULATED'
resulting deployment.support_ticket = mode='INTERNAL' base_url=None
resulting deployment.feedback_learning = enabled=True
--- packaged-drift, RETURN_PLATFORM ---
would_adopt = ['deployment.ai', 'deployment.dependencies', 'deployment.feedback_learning',
               'deployment.support_ticket']
```

Identical to round 1 and to the expectation: `deployment` adopted wholesale, the six undecided keys
untouched, four unit baselines recorded, provider order `GOOGLE,NVIDIA` (this host's
`PLATFORM_AI_PROVIDER_ORDER`), `model_validate` passes. The F4 fix changes nothing here, as it
should not — this is the graph-driven path, not the fallback.

## Judgement

Every blocking finding was fixed at its cause rather than at its symptom, and — the part that
matters most given what round 1 actually found — each fix arrives with a test whose failure mode I
could reproduce myself. That is the difference between this round and the last one: round 1's
problem was not missing work, it was four confident statements that were not true, and the answer to
that is not a better sentence but a check that fails when the sentence stops being true. `families.md`
footnote (b) now says the hot-adopt does *not* yet reach production traffic and names the three tests
that pin the requirement; the AST guard fails on a bare `settings` name at any present or future call
site; the first-publish baseline assertion asserts the four digests the live graph confirms; and the
baseline-fallback overlay is proven against the real `ConfigurationSnapshotBuilder` seam with a
negative control that reproduces the original bug. The four advisories taken were taken properly —
A1 faults strictly after promote-to-VALIDATED and checks the head, A2's shield survives a
cancellation that provably defeated the unshielded call, A3 moved to the real call site with three
distinguishable values, and A4 chose an honest reword over a primitive rewrite and named a control
that exists. The five carried advisories (A6-A8, A10, A11) are all recorded in `drop.json.remaining`
rather than quietly dropped, which is the right disposition for gaps at the HTTP, axe and
documentation layers. PASS. The live no-restart proof (A10) is still the lease's headline claim and
is still design rather than evidence — it belongs to the orchestrator, before merge, and should not
be waived.
