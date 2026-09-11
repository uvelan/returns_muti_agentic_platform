# RV — CFG-3a @ 52f01260 — VERDICT: PASS

Round 2. Round 1 reviewed `2d4cea7a` and returned CHANGES_REQUIRED (F1, F2 blocking). Fixes are
`29936af9` (substantive) and `52f01260` (drop bookkeeping only). Dispositions are in the ledger under
`CFG-3a step:11`. Read-only review; `return_platform.__file__` =
`…\worktrees\cfg-3a\backend\src\return_platform\__init__.py`.

**Both blocking findings are fixed, and fixed at the cause rather than at the assertion.** One new
advisory (F11) fell out of re-testing F1. PASS.

## Round-1 findings and their disposition

| ID | Round 1 | Disposition | RV round-2 check |
|---|---|---|---|
| F1 | **BLOCKING** — `would_adopt` reported keys the merge discards | **Fixed.** `_would_adopt` takes the merged payload and reports a key only when `merged[key] == packaged[key] and active[key] != packaged[key]` (`packaged_adoption.py:552-583`); `summarize_packaged_drift` threads `result.merged_domains` (through `_units` for the two split-key domains) instead of the `undecided` frozenset. Docstrings in `packaged_adoption.py` and `releases.py:1085-1096` rewritten to what the code now guarantees. | **Verified.** Round 1's exact reproduction rerun below now agrees; a positive control still reports a genuinely-adopted key; the live drift simulation is unchanged. Fixed by asking the merge, not by re-deriving the same insufficient rule. |
| F2 | **BLOCKING** — `/publish` documented a per-step audit trail it did not write | **Fixed.** `publish_configuration` now calls `record_configuration_audit` itself at each step — CREATED, DOMAIN_PATCHED (with `changedPaths`), PROMOTED×2 — inside the rollback `try`, summary last; `audit_ids` returns all five in write order (`releases.py:741-847`). | **Verified.** Five records in order with the right targets, five distinct ids, real `changedPaths`, both promotion statuses — pasted below. The docstring now describes what happens. |
| F3 | ADVISORY — moved log lines lost `release_id=` | **Taken.** New `release_id: str | None = None` on `adopt_packaged_configuration`, `domain=%s` kept alongside. | Threaded at all three call sites: `bootstrap_graph_configuration.py:270`, `releases.py:1013`, `:1113`. Live simulation shows `release_id=…` restored. |
| F4 | ADVISORY — `/packaged-drift` warned on a read path | **Taken.** `log: bool = True`; `summarize_packaged_drift` passes `log=False`. New `test_packaged_drift_emits_no_warning_on_the_read_path` uses `caplog` against a fixture that genuinely has an undecided key. | Verified on the live path: the simulation now emits the warning once (my own explicit `adopt_packaged_configuration` call), not twice. |
| F5 | ADVISORY — rollback narrower than "any refusal" | **Left, with reason.** Ledger: small practical exposure, tightening `except` is a behaviour change to an uncovered path, better done deliberately in CFG-4. | Accepted. This matches my own round-1 wording ("a 500 is not a refusal"). Should be carried as a CFG-4 item, not dropped. |
| F6 | ADVISORY — `get_domain_version` untested | **Taken.** New `tests/configuration/test_get_domain_version_live_infra.py`, `pytest.mark.live_infra`, saving twice and asserting 1 → 2 plus `None` for unknown domain/release, with its own cleanup. | Correctly marked, hence the 6th deselection in the acceptance run. Not executable here (environment rules); its assertions match what I measured directly against the dev graph in round 1. |
| F7 | ADVISORY — `[n]` path mapping untested | **Taken.** `test_dotted_error_path_maps_list_indices_as_brackets`, parametrised over exactly the four cases I ran by hand. | Verified present. |
| F8 | ADVISORY — narrowing drops `return_platform_service` | **Left, with reason.** RV verified no caller exists. | Accepted — I verified it myself in round 1 and re-confirmed no consumer. No code change was warranted. |
| F9 | ADVISORY — unbounded `$regex` alternation | **Taken.** `MAX_ACTIONS = 20`; `Query(max_length=MAX_ACTIONS)` on the router; `list_logs` re-checks for non-HTTP callers; `{"$in": [...]}` when no entry ends in `*`. | **Verified against the running router,** not just read — see the paste below. |
| F10 | ADVISORY — existing-id test didn't pin the survivor | **Taken.** `test_publish_refuses_an_existing_release_id` now asserts `taken` is still `DRAFT` after the 409. | Verified present. |
| **F11** | **new, ADVISORY** | see below | Introduced by F1's fix; display-only, in a state where the panel's own action refuses anyway. Not blocking. |

### F11 (new, ADVISORY) — `would_adopt` is now asymmetric across domains when there is no active release

`packaged_adoption.py:566-567` returns `()` when `merged is None`. RETURN_PLATFORM is genuinely absent
from `merged_domains` when there is no active payload, so it reports `[]`. AI_GATEWAY and
DEPENDENCY_SIMULATION are always present — they fall back to the packaged domain itself
(`:492-497`) — so `merged_units == packaged_units`, `active_units == {}`, and every unit is reported.
One response body, two different answers to the same situation:

```
=== no active release at all: is the answer symmetric across domains? ===
  RETURN_PLATFORM:       undecided=[] would_adopt=[]
  AI_GATEWAY:            undecided=[] would_adopt=['other', 'tasks.T1']
  DEPENDENCY_SIMULATION: undecided=[] would_adopt=['dependencies.OMC']
```

Round 1 was consistent here (everything reported, everywhere); the fix made RETURN_PLATFORM `[]` and
left the other two. `test_would_adopt_is_empty_when_there_is_no_active_release` and the corrected
`test_packaged_drift_with_no_active_release_shows_nothing_undecided_or_adopted` both assert only the
RETURN_PLATFORM half, and the former passes `_NO_OTHER_DOMAINS = {AI_GATEWAY: {}, DEPENDENCY_SIMULATION:
{}}` — empty packaged domains, in which the other two cannot show the difference. **Not blocking:**
`would_adopt` is display-only, `POST /adopt-packaged` refuses outright with no active release
(`releases.py:909-916`), and nothing reported is a false claim about an operator's edit being kept or
lost — which is what made F1 blocking. **Fix for CFG-4:** decide one answer for "nothing merged yet"
and give `PackagedAdoptionResult` the same shape for all three domains, then assert all three.

### The corrected pre-existing test — a derivation, not a contract

`test_packaged_drift_with_no_active_release_shows_everything_adoptable` asserted `"discovery" in
would_adopt` on the reasoning that "every packaged key is something a first publish would carry". That
reasoning is about the **CLI's** first publish; this panel serves the **API**, whose `POST
/adopt-packaged` 409s when there is no active release, so nothing is adoptable through it in that
state. The assertion was pinning the old derivation (`not undecided ⇒ adoptable`) rather than an
observable promise: `grep -rn "would_adopt\|wouldAdopt\|packaged-drift" frontend/src scripts` returns
**no consumer** — only backend comments. Correcting it was legitimate, not a test bent to fit the code.
It is, however, only half-corrected, which is F11.

## F1 — round-1 reproduction, rerun on the fixed code

Same shape as round 1: a baseline recorded against an older packaged value, the release edited away
from it, the packaged file independently moved again.

```
=== F1 reproduction, round 1 shape, rerun on the fixed code ===
recorded baseline : {'discovery': 'f0a9eb44aa35…'}
packaged / active : {'threshold': 3} / {'threshold': 2}
undecided         : ()
would_adopt says  : ()            <-- round 1 said ('discovery',)
merge keeps       : {'threshold': 2}
AGREE?            : True          <-- round 1 said False

=== positive control: a key the release predates ===
would_adopt: ('newkey',)   merged newkey: {'t': 9}
```

The positive control matters: the fix is not "always empty". `test_packaged_adoption.py` carries the
same two cases against the *real* packaged document (its docstring explains why a toy dict fails
`model_validate` before the assertion is reached — the fixture was built honestly).

## F2 — the audit trail, measured

RV-only probe (added, run, deleted; `git status --porcelain` clean afterwards) publishing one domain
through `POST /api/config/publish`:

```
actions in order  : ['CONFIGURATION_RELEASE_CREATED', 'CONFIGURATION_DOMAIN_PATCHED',
                     'CONFIGURATION_RELEASE_PROMOTED', 'CONFIGURATION_RELEASE_PROMOTED',
                     'CONFIGURATION_RELEASE_PUBLISHED']
targets           : ['rv2-publish', 'rv2-publish/RETURN_PLATFORM', 'rv2-publish',
                     'rv2-publish', 'rv2-publish']
audit_ids returned: 5   distinct: 5
DOMAIN_PATCHED changedPaths: ['policy_evaluation.disabled_reason', 'policy_evaluation.enabled']
DOMAIN_PATCHED patchKeys   : ['policy_evaluation']
promotion statuses         : ['VALIDATED', 'RELEASED']
```

`changedPaths` is the real before/after leaf diff (`_changed_paths(current, updated_payload)`), the
same call `patch_domain_config` makes — the loss round 1 flagged is closed, not papered over. The
domain record targets `<release>/<domain>`, matching the four-call path, which is what makes
`?target=<release>` and `?target=<release>/RETURN_PLATFORM` list the four and the one respectively.
Each record is written only after its own step succeeded, inside the rollback `try`; the summary is
written last. Records for steps that *did* happen survive a later refusal — correct for an audit log,
and the same property the four-call path has.

## F9 — verified against the running router

```
20 actions             -> HTTP 200
21 actions             -> HTTP 422        <-- max_length caps the LIST length
1 action of 400 chars  -> HTTP 200        <-- and is not a string cap
MAX_ACTIONS = 20
all-exact    : query -> {'action': {'$in': ['A_B', 'C_D']}}
one wildcard : query -> {'action': {'$regex': '^A_B$|^C_'}}
target only  : query -> {'target': 'rel-1'}
neither      : query -> {}                <-- the default is still find({})
over cap     : ValueError: actions accepts at most 20 values, got 21
```

Both halves of the coordinator's question answered: `Query(max_length=…)` on a `list[str]` constrains
the item count (21 → 422) and not the item length (one 400-character value → 200), and `$in` is chosen
exactly when no entry ends in `*`.

## Live read-only simulation — head 78, unchanged

`summarize_packaged_drift` + `adopt_packaged_configuration` against
`return-platform-d2f7787021d4622d` via `Neo4jConfigurationGraphRepository`. Nothing published.

```
head_revision=78     active_release=return-platform-d2f7787021d4622d status=RELEASED
recorded_baseline_keys=[…20…]
RETURN_PLATFORM: undecided=['agents','clarification_policy','policy_evaluation',
                            'return_eligibility_policy','return_policy','support_ingress']
RETURN_PLATFORM: would_adopt=[]   filled_leaves=[]
AI_GATEWAY: undecided=[] would_adopt=[]     DEPENDENCY_SIMULATION: undecided=[] would_adopt=[]
RETURN_PLATFORM merge validated=True
head_revision_after=78     active_release_after=return-platform-d2f7787021d4622d
```

Identical to round 1: the same six undecided keys, the same 20 baselines, head and active release
unmoved, no adoption, no publish. The fix changed nothing about the live answer — as it should not,
since every baselined key on this graph still matches the packaged file. That is exactly why F1 had to
be caught by construction rather than by observation.

## Runs

```
$ pytest tests/test_configuration_api.py tests/configuration tests/test_graph_configuration_bootstrap.py \
         tests/test_every_console_path_is_mounted.py tests/api -q -p no:cacheprovider
728 passed, 6 deselected, 2 warnings in 104.22s     [exit 0]      (the brief's exact command)

$ ruff check  src/return_platform/{configuration,security} tests/configuration tests/test_configuration_api.py
All checks passed!
$ ruff format --check  src/return_platform/configuration tests/configuration tests/test_configuration_api.py
76 files already formatted
$ mypy  api/{audit,releases,router}.py application/packaged_adoption.py cli/bootstrap_graph_configuration.py
Success: no issues found in 5 source files

$ python scripts/check_openapi_drift.py
{ … "openapi_sha256": "f20d4e540356b43e79100616d901e6dedeab8b9159889410b29369e479820920",
  "snapshots": [4], "diffs": [], "status": "PASS", "exit_code": 0 }
```

728 = round 1's 714 + 14. The 6th deselection is the new `live_infra` test. All four OpenAPI copies
byte-identical (`md5sum` → `013cceae8838c5c1cc010d01015cdc60` ×4). The whole OpenAPI change this round
is one line, `"maxItems": 20` on the `actions` query parameter — a validation keyword with no
TypeScript representation, which is why `frontend/src/api/generated/return-platform.d.ts` is untouched
by `29936af9` and `git diff --stat 5dc5a825..HEAD -- frontend/src` still shows that one generated file
and nothing else. No frontend component touched.

```
$ pytest <the nine known-failing modules> tests/configuration tests/api -q -p no:cacheprovider -rf
42 failed, 774 passed, 6 deselected, 2 warnings in 101.95s (0:01:41)
$ grep ^FAILED … | sed 's/::.*//' | sort | uniq -c | sort -rn
  13 test_ai_a_rejected_parse_is_repaired_on_its_own_route   9 test_confirmation_starts_the_case_workflow
   6 test_order_discovery_smoke_net   4 test_reasoning_stage_prompts   3 test_ai_route_balancing_design
   2 test_enforced_contracts_are_disclosed   2 test_ai_single_dispatch_boundary
   2 test_turn_temporal_grounding     1 test_keyless_reasoning_is_held_for_a_human
$ grep ^FAILED … | grep -cE "tests/configuration|tests/api"        →  0
$ diff <round-1 full-suite failing ids @2d4cea7a> <round-2 failing ids @52f01260>
  (no output)   IDENTICAL — the same 42 node ids
```

Not a count match: the 42 **node ids** are byte-identical to the set round 1 extracted from the full
suite at `2d4cea7a`, which itself was identical to CFG-1's RV-verified base set. Zero failures in
`tests/configuration` or `tests/api`. **No new failure — nothing blocking.**

Files changed by the fix round: `configuration/api/{audit,releases,router}.py`,
`configuration/application/packaged_adoption.py`, `configuration/cli/bootstrap_graph_configuration.py`,
four OpenAPI copies, the drift receipt, three test files (two new), the ledger and `drop.json`. All
inside the brief's Owns list. **No scope creep.** `drop.json`'s `head_sha` now names `29936af9`,
recorded by the follow-up `52f01260` — the same one-commit lag every prior step used.

## Judgement

The two blocking findings came back fixed at the cause, which is the part that mattered. F1 was not
patched by adding the missing case to the "not undecided" rule — the rule was replaced by asking the
merge what it actually did, so the class of mistake is gone rather than one instance of it, and the
new unit tests carry both the failing shape and a positive control against the real packaged document
rather than a toy dict that would not have survived validation. F2 now writes the five records its
docstring describes, with `changedPaths` on the patch record, so the collapsed publish is genuinely
equivalent to the four-call path on the dimension the audit log exists for; I measured the trail rather
than reading the assertion. F9 I checked against the running router in both directions, because
"`max_length` on a list" is exactly the kind of thing that silently means something else. The two
advisories left behind (F5, F8) are left for reasons I agree with — F8 because I verified the
non-issue myself, F5 because it is a deliberate behaviour change to an uncovered path and a fix round
is the wrong place for it, though it should be carried into CFG-4 rather than forgotten. Against all
that, F11: the F1 fix left "nothing merged yet" answered two different ways in one response body, and
the two tests covering that state assert only the half that is now empty, with empty packaged domains
that cannot show the other half. It is display-only, in a state where the route it advertises refuses
outright, and it misreports nothing about an operator's edit — so it is an advisory, not a second
round of changes. PASS.
