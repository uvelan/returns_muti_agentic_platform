# RV — CFG-5b @ d9a86886 — VERDICT: CHANGES_REQUIRED

Read-only review of `feat/cfg-5b-agents` (head `d9a86886`, content `aa239a95`) against base `52765381`.
Environment confirmed before anything else:

```
$ PYTHONPATH=<wt>/backend/src backend/.venv/Scripts/python.exe -c "import return_platform; print(return_platform.__file__)"
K:\Projects\Ret\returns_muti_agentic_platform\.claude\worktrees\cfg-5b\backend\src\return_platform\__init__.py
```

## Findings

| ID | Severity | file:line | What | Why it matters | Fix |
|---|---|---|---|---|---|
| **F1** | **BLOCKING** | `bootstrap/adapters/governance_agent_configuration.py:98-101`, `configuration/application/agent_configuration.py:146-157`, `main.py:598-607` | Activation clones the whole `RETURN_PLATFORM` document from **this process's runtime snapshot**, not from the active release. `released_return_platform_document()` deep-copies whatever `_released_return_platform_document()` (a closure over `app.state.return_configuration_snapshot.domain_payloads`) returns, patches `agents.<id>`, and hands that to `publish_release_with_domains`, which then **replaces the domain whole** (`release_promotion.py:236`). | The snapshot is refreshed by request middleware behind a 5-second guard (`runtime_activation.py:206`) and, when a refresh raises, `main.py:1219-1230` logs `runtime_configuration_refresh_failed_using_last_good_snapshot` and keeps serving the stale one. So any `RETURN_PLATFORM` release published by another process (or by this one inside the guard window) is **silently reverted** by the next agent activation — `return_policy`, `discovery`, `support`, every CFG-track screen's publish, not just `agents`. The `expected_head_revision` CAS cannot catch it: it is read *after* the clone (`release_promotion.py:262-266`). Demonstrated below, not inferred. The sibling activator for the *same* domain already does it right — `governance_improvement.py:160-168` reads `repository.get_domain_config(active.release_id, RETURN_PLATFORM_DOMAIN_KEY)`. Pre-CFG-5b the blast radius was the `AGENT_MODULES` sibling domain only; this lease widens it to every business key. | In `AgentConfigurationProposalActivator.activate`, read the active release's `RETURN_PLATFORM` document from `self._repository` (mirror `governance_improvement.py:155-167`, including its "no active release / no domain" refusals), patch `agents[subject_id]` on *that*, and publish it. Keep `released_return_platform_document()` for reads only (or delete it). Add the regression the current suite cannot fail: a test whose `service`'s `active` callable disagrees with the seeded release, asserting the other key survives — today's fixture (`tests/configuration/test_agent_configuration_releases.py:77-95`) makes snapshot and release identical, which is why every existing assertion passes either way. |
| A1 | ADVISORY | `frontend/src/domains/config/AgentsSection.tsx:305` | The new per-row Save button carries `disabled:opacity-40` on `bg-primary`/`text-on-primary`. Measured (tokens from `tailwind.config.ts:29,59-60`, composited over the `premium-panel` white ground): enabled label **9.63:1**, disabled label **2.12:1**. | This is the exact utility whose removal was CFG-5's own **blocking** F1 (`.plan/reviews/CFG-5.md:20`, 2.04:1), and CFG-5's H1/H2 advisories were explicitly carried "into CFG-5b's Agents screen work" (`CFG.ledger.md`, "CFG-5 merged at 58b1e409"). Neither is discharged nor recorded as declined anywhere in the CFG-5b ledger. It is not a WCAG failure (inactive controls are exempt) and the mock sweep cannot catch it — `axe-core`'s `colorContrastMatches` returns false for disabled elements (`node_modules/axe-core/axe.js:28194`) — so nothing but this review will. Every row renders with Save disabled in the resting state, so it is the default appearance of the screen. | H1's own prescription: drop the opacity utility, dim through a non-text channel (`disabled:bg-surface-container-low disabled:text-on-surface-variant disabled:border-outline-variant`). H2: assert the family (`no /disabled:(text|opacity)-/` plus the retained foreground token) in `AgentsSection.test.tsx`, not one spelling. |
| A2 | ADVISORY | `configuration/api/agents.py:9-11`, `:161-163` | The module docstring still reads "Agent modules are declared in `manifest.yaml`"; the `PUT` docstring still promises a 422 "carrying the loader's own reason". This lease deleted both the manifest entries and `ConfigurationLoader`. | The file is otherwise the most careful description of this surface on the platform; leaving it naming a system this same lease retired sends the next reader to `manifest.yaml` for an answer that is now in `AgentConfiguration`. The service module, both READMEs and the test module were all updated — this one file was missed. | Two sentences: the document is the release's `agents.<id>` entry; the 422 carries `AgentConfiguration`'s own message. |
| A3 | ADVISORY | `backend/config/README.md:3`, `:105-107` | Header still opens "Canonical, manifest-driven configuration for the unified return platform"; the `dynamic_knowledge/` bullet still says a schema there "is only authoritative if a `GRAPH` module in `manifest.yaml` points at it" — `graph.order_discovery` was deleted by this lease and §7 of the same file now says the manifest is read by nothing. | The rewritten §7/§"Removed as dead" are excellent; these two survivors contradict them inside one file, which is worse than either statement alone. | Reword the header to "release-published configuration"; make the `dynamic_knowledge/` bullet name the real loader (or state that no manifest entry points at it any more). |
| A4 | ADVISORY | `frontend/src/domains/config/AgentsSection.tsx:227-244` | After a successful save the draft still differs from `loaded` (the row is not refetched — `onSuccess` invalidates `["proposals"]` only), so `dirty` stays true and Save stays enabled. A second click files a second, identical proposal. | Two proposals for the same document is noise in the approval queue an operator then has to reject one of. The success panel says the config has not changed, which reads as an invitation to press again. | Disable Save while `proposal !== null` for the current draft, or clear the draft back to `loaded` and let the invalidation refetch. |
| A5 | ADVISORY | `bootstrap/adapters/governance_agent_configuration.py:106-114` | A release published by an agent activation writes **no** `CONFIGURATION_*` record to the audit stream `GET /api/config/audit` serves, while every release published through `configuration/api/releases.py` does (`:1200`). | The lease's recorded deviation is correct on its own terms (`record_configuration_audit` needs a `Request`; `releases.py` is outside Owns; the pre-CFG-5b activator did not call it either, and `PROPOSAL_ACTIVATED` is still in the governance log). But an operator asking `/api/config/audit` who cut release `agent-config-…` still gets nothing. Pre-existing, not a regression. | Orchestrator's call: lift the audit write out of `releases.py` behind a `Request`-free helper in a later lease. Nothing for CFG-5b. |

## Answers to the six questions

**1. Repoint.** `GET /api/agents` and `/{id}` serve `active()["agents"]` (`agent_configuration.py:113-144`), `source` is always `"RELEASE"` with the reason documented; `PUT` validates through `AgentConfiguration(**document)` and answers 202 + proposal view (`api/agents.py:142-213`). Activation re-validates at activation time, uses the **canonical** `model_dump(mode="json")` (not the submitted document) and receipts its sha256 (`agent_configuration.py:161-185`) — good, and better than the brief asked. Baseline metadata **is** carried (`release_promotion.py:250-251`, inside the shared publish). The clone, however, is **not** taken from the active release — see **F1**. Audit: see **A5**; the change is in the governance audit log (`test_the_edit_reaches_the_audit_trail`, `"PROPOSAL_ACTIVATED" in audit.actions()`), not in the configuration audit stream, and the recorded deviation is honest about why.

**2. Retirement.** `backend/config/agents/` (8 files) and the six non-agent entries and their files are gone; `ls backend/config` shows no `agents/ workflows/ sync/ sources/ mappings/ graph/`. `application/loader.py` deleted, and nothing imports it:

```
$ grep -rn "ConfigurationLoader|application\.loader|LoadedManifestModule" backend/src backend/tests frontend/src
(only prose: agent_configuration.py:6, domain/release.py:11, configuration/README.md:42,72-73 — no import, no call)
```

`manifest.yaml` now holds `platform.system_store` alone, and **it is dead**: `platform/system_store/manifest_loader.py::load_system_store_config` reads `Settings.system_store_manifest_path` → `backend/config/platform/system_store.yaml` directly and never opens `manifest.yaml`. The file's own header comment and both READMEs say exactly this; keeping it as a historical index rather than deleting it (a decision about a file outside Owns) is the right call for this lease, but the next lease that owns `platform/system_store/` should delete it. READMEs: `configuration/README.md` accurate; `backend/config/README.md` accurate except **A3**.
The deleted test (`test_a_credentials_block_in_an_agent_document_is_refused`) — judged **correct to delete**. It depended on `AgentConfigNode.retry_policy: Mapping[str, Any] | None` to build a document that passed schema validation while carrying `retry_policy.credentials.token`. `AgentConfiguration` (`return_configuration.py:51-95`) is `extra="forbid"` with every field concretely typed and no free mapping, so no such document exists; `test_a_refused_document_names_the_field_and_proposes_nothing` covers the refusal that replaces it, and the governance forbidden-key policy itself is still exercised in `tests/platform/test_proposal_kernel.py` and `tests/operations/test_feedback_improvement_proposals.py`. The property did not lose its test; it lost its hole.

**3. Bootstrap interplay.** `test_a_proposal_published_agent_edit_survives_the_packaged_file_that_disagrees_with_it` drives the real `bootstrap_graph_configuration.main()` through `_install_bootstrap_doubles` with a release whose `agents` differs from the packaged file and a baseline recorded by `_baseline_of(packaged_payload)` — so the digest genuinely moves away and the carry-forward keeps the edit, asserted key by key against `packaged_payload`. It mirrors the already-accepted `support` test one for one. It does not drive the activator itself (it constructs the post-activation release state), which is the right seam: the activator has its own test.

**4. Frontend.** Typed table on `TypedSectionScreen`'s chrome (not the component — deviation recorded and correct: that component publishes straight to the release, which is the wrong write path here); columns name/version/`Toggle`/`Toggle`/`EnumSelect`; the enum options come from `runtime.ai_gateway_configuration.tasks`, which is a real field of the served snapshot (`snapshot.py:50`, `AIGatewayConfiguration.tasks: dict[str, TaskConfiguration]`, `router.py:123-148`) with the same id vocabulary as `ai_route_ref` (`backend/config/returns/agents.yaml:15` → `ORDER_CANDIDATE_ANALYSIS_V1`, a packaged task). Save files the proposal, renders its id, states "The active configuration has not changed" and links `/approvals` (asserted at `AgentsSection.test.tsx:156-181`). Advanced JSON kept. Read-only path asserted twice (`:148`, `:275`). A typed save round-trips the whole document including dead knobs (`:175` asserts `retry_max_attempts` travels) — the property most likely to have been got wrong, and it is tested. MSW handlers and the contract test match the new shape. The e2e spec drives `PUT` → `POST /api/proposals/{id}/approve` → `/activate` → poll `GET /api/config/runtime`, then reverts; payload shapes match `DecisionRequest`/`ActivationRequest` (`api/proposals.py:129-143`) and both routes answer 200, so it is written to pass once the trunk serves this branch. Styling: **A1**.

**5. Runs.** All reproduced in this worktree at head (see below).

**6. Scope.** No file outside the brief's Owns except `main.py` and `configuration/snapshot.py` — both named by the CFG-5 step:05 plan the brief tells this lease to follow (items 3 and 1) — and `configuration/README.md`, which is the sibling of an owned README. `backend/config/{workflows,sync,sources,mappings,graph}/` are deleted under the brief's own conditional grant, with the grep recorded. **Nothing touches the bootstrap CLI or `packaged_adoption.py`**: `git diff --name-only 52765381..HEAD | grep -E "packaged_adoption|bootstrap_graph|scripts/"` returns nothing. No blocking scope finding.

## Pasted output

F1, reproduced with a standalone probe (worktree code, in-memory repository; the service's `active`
callable is the stale snapshot, the repository's active release is newer):

```
active release before activation: concurrent
active     support.external_mirror_enabled = True     <- what the graph holds
stale snapshot support.external_mirror_enabled = False
published release: agent-config-proposal-c16f8f3f-9a9d-4bcb-9d5d-145fae264394
published  support.external_mirror_enabled = False    <- the concurrent release, reverted
agent edit applied: False
VERDICT: concurrent release REVERTED by the agent activation
```

A1, computed from the shipped tokens (`primary #004e47`, `on-primary #ffffff`, panel `#ffffff`):

```
enabled Save: text on primary = 9.63
disabled Save (opacity-40 over white panel): fg (255,255,255) bg (153,184,181) contrast 2.122
```

Acceptance:

```
$ pytest tests/configuration tests/api tests/test_configuration_api.py tests/test_graph_configuration_bootstrap.py tests/platform -q
955 passed, 35 deselected, 2 warnings in 183.38s (0:03:03)

$ pytest <the nine known-failing modules> -q -p no:cacheprovider
42 failed, 112 passed in 43.41s          # byte-identical pair to .plan/reviews/CFG-1.md's base measurement

$ ruff check <7 touched files>          All checks passed!
$ ruff format --check <7 touched files> 7 files already formatted
$ mypy <5 touched source files>         Success: no issues found in 5 source files

$ python scripts/check_openapi_drift.py
"diffs": [], "status": "PASS", "exit_code": 0
$ sha256 of the four snapshots
848c19da...000b0  openapi.json
848c19da...000b0  openapi/return-platform.openapi.json
848c19da...000b0  backend/openapi/return-platform.openapi.json
848c19da...000b0  frontend/openapi/return-platform.openapi.json

$ npx vitest run
 Test Files  90 passed (90)    Tests  1079 passed (1079)
$ npm run typecheck   exit 0
$ npm run lint        exit 0
$ git status --short  (clean)
```

## Judgement

Everything this lease set out to delete is properly dead and properly documented — the loader, the
eight agent files, the six orphan entries, the `AGENT_MODULES` domain — and the grep that licensed
each deletion is in the ledger rather than asserted. The read path, the canonical-dump receipt, the
re-validation at activation time and the bootstrap interplay test are all better than the brief
asked for, the three recorded deviations are each argued from the code rather than from convenience
(the deleted credentials test in particular is the right call: the model closed the hole the test
was guarding), and the frontend earns its typed table with the one test that matters — a typed save
round-trips the dead knobs. What stops this merging is one line of sourcing: the activator patches a
copy of the runtime snapshot instead of the active release, and because `publish_release_with_domains`
replaces a domain whole, an agent activation now overwrites every other `RETURN_PLATFORM` key with
whatever this process last saw. That was survivable when the target was a sibling `AGENT_MODULES`
domain nothing else wrote; pointing the same pattern at the domain every Configuration screen
publishes into turns a 5-second staleness window into a silent revert of someone else's governed
release, as the probe above shows. The fix is the pattern the sibling activator twenty lines away
already uses, plus the test whose fixture currently cannot distinguish the two sources.
