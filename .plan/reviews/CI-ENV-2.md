# CI-ENV round 2 — `feat/ci-env-file` @ `c6b69992`

Round-1 head `2aaecadc`. Base `refactor/unified-return-platform` (`a683f648`).
Full-branch diff: one file, `.github/workflows/checks.yml`, +70 −1.
Round-2 delta: comment-only — `git diff 2aaecadc c6b69992` filtered of comment
lines is **empty**. No YAML key, step, value or ordering moved.

## Verdict: PASS

Both round-1 findings are closed by replacement, and the three claims that are
new to this round hold. Round-1 substance (the red, the green figures,
`contracts` unexposed, the gitignore lines, the scripts, the credential
paraphrase) is not re-derived; it was established from scratch and stands.

---

## F1 — resolved by replacement

The comment no longer credits `ensure_runtime_env_keys.py` with maintaining
`.env.example`. It retracts the claim explicitly ("An earlier draft of this
comment credited ... it does not -- its `update()` flows example -> env, one
way, and never inspects the example") and puts four named protections in its
place. Not a deletion: the rule-13 answer is now longer and stronger than the
one it replaces.

I checked all four independently, at the same depth as round 1, because this is
the branch's second attempt at a rule-13 answer.

| Claimed protection | Verified |
| --- | --- |
| Delete `.env.example` → the `cp` fails here | **True.** The step is `run: cp .env.example .env`; a `run` step is `bash -e`, so the job fails at a step that names itself. |
| Drop a key → `Settings` (`frontend_cors_origin`, `mongo_dsn`) or `conftest`'s `_required_environment_variable` fails **by name** | **True, and reachable in the default run.** `configuration/settings.py:105` `frontend_cors_origin: AnyHttpUrl` and `:107` `mongo_dsn: SecretStr = Field(min_length=10)` are required with no default under `env_prefix="PLATFORM_"`; both keys are present in `.env.example` (`:24`, `:35`). Bare `Settings()` is constructed in normal-suite tests — `tests/api/test_order_line_routes.py:303`, `tests/operations/test_support_resolver_composition.py:241`, `tests/operations/test_support_omc_mirror.py:51` — so a dropped key raises a pydantic error naming the field. Separately, `conftest.py:146` `_required_environment_variable` raises `Required test environment variable is not set: <NAME>`, and it is used by the `test_settings` fixture (`:261, :266, :314, :316, :330`), which is **not** in `_LIVE_INFRA_FIXTURES` (that set is only `reasoning_store`, `temporal_target`, `sqlserver_test_database`) and is requested by 161 test modules. The gate is in the deselected-live default run, not behind the marker. The chain works because `pytest_configure` `load_dotenv`s the copied `.env` into the process environment (`conftest.py:31-37`). |
| Drift content → `tests/test_ai_gateway_routing.py:194` fails, reading the tracked example | **True, line-exact.** `:194` is `text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(...)`, inside `_shipped_models`, consumed by `test_default_model_pools_have_rotation_capacity`, which asserts a rotation-capacity floor over the parsed pools. It reads the tracked file, not `Settings`. |
| `ensure_runtime_env_keys.py` is gated by `tests/test_runtime_env_key_sync.py` | **True, and it genuinely exercises the script.** The test `runpy.run_path`s the real `scripts/linux/ensure_runtime_env_keys.py` and calls the actual `update(env_file, example)` against a tmp fixture, asserting the non-overwrite of `EXISTING`, the append of both missing keys, and the returned `("MISSING_ONE", "MISSING_TWO")`. Not a re-implementation, not a mock. |

Closed.

## F2 — scoped, not softened

The sentence now reads "the **BACKEND** job's greens were replicated locally
... (Only that job: the other 'green on this commit' notes in this file belong
to `frontend-static` and `contracts`, neither of which reads `.env`, and they
are unaffected.)"

Grepped the file: `checks.yml:156` ("All four green on this commit",
`frontend-static`) and `:290` ("Green on this commit", `contracts`) are the only
two such notes, exactly as the parenthetical says. Their non-exposure was
established in round 1. The narrowed statement is the one I said was defensible,
and it keeps the force — the "therefore" is gone and the correction is on the
record rather than quietly dropped. Closed.

## New claim (not reviewed in round 1) — the interchangeability limit

> "The two files are NOT interchangeable in content and this does not claim they
> are: a developer `.env` carries ~11 extra live-infra and host-port keys. What
> was measured is that the normal suite's OUTCOME is the same either way, which
> is all CI needs, since CI only ever sees `.env.example`."

Checked as an admission, so held to exactness.

- **"~11 extra": exact.** Parsed both key sets: `.env.example` 124 keys, the
  working-tree `.env` 135. `.env`-only is exactly 11 — `BACKEND_PORT`,
  `NEO4J_BOLT_PORT`, `NEO4J_HTTP_PORT`, `TEMPORAL_PORT`,
  `PLATFORM_TEST_NEO4J_URI`, `PLATFORM_TEST_TEMPORAL_TARGET`,
  `PLATFORM_CONTAINER_MONGO_DSN`, `PLATFORM_CONTAINER_SOURCE_MONGO_DSN`,
  `DYNAMIC_KNOWLEDGE_SCHEMA_PATH`, `DYNAMIC_ORDER_AGENT_ENABLED`,
  `PLATFORM_AI_MANUAL_HANDOFF`. Example-only is **zero**, i.e. the example's key
  set is a strict subset — which is why the copy can satisfy everything the
  suite reads.
- **"NOT interchangeable in content": correct**, and it is the retraction of the
  "byte-identical" framing I declined to verify in round 1. It now claims less
  than the record, not more.
- **"What was measured is that the normal suite's OUTCOME is the same either
  way": supported.** `.plan/merge.md:184-186` carries the two-row comparison —
  real `.env` and `.env.example` both `1 failed, 5197 passed, 10 skipped, 512
  deselected` — with the row-note that it was taken on an earlier trunk, and the
  current-trunk re-measurement (`.env.example` → `1 failed, 5232 passed, 11
  skipped, 514 deselected`, which I reproduced) recorded separately. The comment
  says outcome-sameness *was measured*; it does not claim it was re-measured on
  this commit, and it does not claim key-set or byte identity. Accurate about
  both what was and was not measured.

Sustained.

## Mechanics

- **YAML parses.** `yaml.safe_load` of the file at `c6b69992` succeeds; jobs are
  `allowlist-self-test, backend, backend-static, frontend-static,
  frontend-tests, contracts`.
- **Step unchanged and correctly placed.** The parsed `backend.steps` prefix is
  `{uses: actions/checkout@v4}`, then `{name: Provide the repository environment
  file, run: cp .env.example .env}`, then `{uses: actions/setup-python@v5}`. The
  copy is immediately after checkout, which is required — before checkout there
  is no `.env.example` to copy.
- **Scope.** `git diff --name-only` against trunk returns exactly
  `.github/workflows/checks.yml`. No test, source, config or `.gitignore` change;
  `scripts/ci/known_test_failures.json` untouched. No blocking rule (1-12)
  implicated; no secret reaches the repo or a log.

## Checked and not raised

- The four-bullet list is summarised as "Every one of those is a backend-suite
  test", but bullet one is the `cp` step itself, not a test. Literally
  imprecise. Not raised: bullet one says "the `cp` fails loudly, **here**" three
  lines above, so no reader can be misled about the mechanism, and the slip
  mislabels the most obviously-present protection rather than inventing one. It
  creates no false reliance and does not argue in the overstating direction —
  the two properties that made F1 and F2 findings.
- "live-infra and host-port keys" fits 8 of the 11 extras; `DYNAMIC_*` and
  `PLATFORM_AI_MANUAL_HANDOFF` are feature flags. Immaterial — the example-only
  set is empty, so none of the 11 can affect the run CI actually performs, and
  the outcome comparison already covers it empirically. Exact list recorded
  above.
- `.plan/merge.md:187` still opens "Byte-identical, down to the single
  allowlisted failure and the exit code" immediately after the precision
  paragraph that disclaims interchangeability. In context it is scoped to the
  outcome, and `merge.md` is outside this diff. Noted for the ledger, not a
  finding against the branch.
- Round-1's non-findings (the "two further scripts" undercount, the missing
  `chmod 600`, the unconditional `cp` against the archived stage-plan rule)
  are unchanged and remain non-findings for the reasons given there.

---

Merge permitted.
