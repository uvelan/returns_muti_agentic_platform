# CI-ENV round 1 — `feat/ci-env-file` @ `2aaecadc`

Base: `refactor/unified-return-platform` (`a683f648`)
Diff: one commit, one file — `.github/workflows/checks.yml`, +47 −1.

## Verdict: CHANGES_REQUIRED

Two findings, both factual claims inside the added comment block. The change
itself is correct, the diagnosis is correct, and I reproduced every measured
figure independently. Nothing in the YAML needs to move.

I am not softening this because the branch is the orchestrator's. I am also not
inflating it: the mechanism is sound and the findings are confined to sentences
that assert things I checked and found not to hold.

---

## What I verified, from scratch

### 1. The diagnosis — CONFIRMED, and it is as bad as claimed

`backend/tests/conftest.py:29-30`:

```python
if not ROOT_ENV_FILE.is_file():
    raise RuntimeError(f"Required repository environment file was not found: {ROOT_ENV_FILE}")
```

`ROOT_ENV_FILE` is `parents[2] / ".env"` — the repository root, which is where
`cp .env.example .env` puts it from the job's default working directory.
Correct target.

`.env` is untracked and ignored:

```
$ git ls-files --error-unmatch .env
error: pathspec '.env' did not match any file(s) known to git   # exit 1
$ git check-ignore -v .env
.gitignore:25:.env	.env
```

I created a `git worktree` of trunk — an exact simulation, since `.env` is
gitignored and therefore genuinely absent in a fresh checkout — confirmed the
worktree had `.env.example` and no `.env`, and ran the suite with `PYTHONPATH`
pointed at **the worktree's own** `backend/src`:

```
INTERNALERROR> ...conftest.py, line 30, in pytest_configure
INTERNALERROR> RuntimeError: Required repository environment file was not found: ...\wt-trunk\.env
EXITCODE=3
```

Exit **3**. The job's own discriminator (`if [ "$status" -gt 1 ]`) then emits
`::error::pytest exited 3 -- the run failed, not the tests` and fails the job.
The premise holds in full: the `backend` job has never completed a run. The red
is observed, and it was observed first.

### 2. The fix — CONFIRMED, byte-for-byte on the figures that matter

Same worktree, `cp .env.example .env`, same `PYTHONPATH`, full suite:

```
FAILED tests/test_cumulative_support_outcomes.py::test_a_rejected_return_still_opens_no_work_item
1 failed, 5232 passed, 11 skipped, 514 deselected, 2 warnings in 260.06s
EXITCODE=1
```

Exactly the claimed `1 failed, 5232 passed, 11 skipped, 514 deselected`, exit 1,
sole failure `test_a_rejected_return_still_opens_no_work_item`. That id is
present in `scripts/ci/known_test_failures.json` under `suites.backend`, so
`assert_known_failures.py` rules it acceptable and the job goes green. The
failure is the pre-existing `workflow.patched` / `'_Runtime' object has no
attribute 'patched'` one recorded in `.plan/merge.md` — unrelated to `.env`.

I took the `PYTHONPATH` trap seriously and set it to the worktree's own
`backend/src`. I saw neither of the two spurious failures the brief warned about,
which is the expected result when the interpreter resolves `return_platform` from
the tree it is measuring.

Note for the record, not a finding against this branch: `.plan/merge.md:171`
records this measurement as `1 failed, 5197 passed, 10 skipped, 512 deselected`.
That table is stale relative to trunk today. The **commit comment's** figure is
the correct one.

### 3. The `contracts` job — CONFIRMED unexposed

In the same worktree with `.env` removed again:

```
$ python backend/scripts/export_openapi.py   # no .env present
EXIT=0
```

And the surrounding chain does not reintroduce the exposure.
`contracts:check` is `contracts:generate && contracts:served && git diff
--exit-code`. `frontend/scripts/export-contracts.js` does nothing but locate a
Python and `spawnSync` `export_openapi.py`; `openapi-typescript` and
`frontend/scripts/check-served-fields.js` are pure JS over the generated
document. No `.env` read anywhere on that path. Omitting the copy step from
`contracts` is correct, and declining to cargo-cult it there is the right call.

`allowlist-self-test`, `frontend-static` and `frontend-tests` were checked for
the same exposure and have none. `.github/workflows/` contains only `checks.yml`
and `secret-scan.yml`; no other workflow runs pytest.

### 4. Rule 13 — the fix is self-gating, and the branch under-sells it

Asked properly: if `.env.example` drifts so that `Settings` no longer accepts it,
or if it is deleted, what fails and how loudly?

- **Deleted** → `cp .env.example .env` fails, `bash -e` aborts the step, the job
  fails at a step that names itself. Loud.
- **A key `Settings` requires is dropped** → `Settings` has genuinely required
  fields with no default (`frontend_cors_origin: AnyHttpUrl`, `mongo_dsn:
  SecretStr = Field(min_length=10)`), and `conftest.py`'s settings fixture pulls
  a further set through `_required_environment_variable`, which raises by name.
  The suite fails. Loud.
- **`.env.example` content drifts** → `backend/tests/test_ai_gateway_routing.py`
  reads the tracked `.env.example` directly (`_shipped_models`, line 194) and
  asserts a rotation-capacity floor over it.

All three of those gates are the `backend` job. Which is to say: **this branch is
the gate for its own fix**, and it is also the first thing that has ever made
those `.env.example` guards executable outside a workstation. That is the right
rule-13 answer and it is a strong one.

`ensure_runtime_env_keys.py` is itself gated —
`backend/tests/test_runtime_env_key_sync.py` exercises its `update()` against a
synthetic fixture — and that test, too, has never run in CI until now. So the
"same defect one layer down" the brief anticipated is **not** present. But see
Finding 1: the comment credits the wrong mechanism.

### 5. The two rejected alternatives — the credential claim is accurate

`.gitignore:31` is `.env.*`, `:32` is `!.env.example`. The comment above the
rule at lines 26-30 says `backend/.env.vault-backup` was tracked and carried a
live provider key into history, that GitHub's push protection caught it, and
that the W0.1 credential migration did not — a backup taken before the migration
kept the plaintext. The commit's paraphrase ("a backup file once carried a live
provider key into git history and was caught by push protection") is faithful and
does not overstate. Sustained.

The conftest-degradation rejection is also sound: the raise is a deliberate
guard, and copying satisfies it where degrading would weaken it. No rule-10
concern.

### 6. Nothing else moved — CONFIRMED

One commit on the branch. `git diff --name-only` against trunk returns exactly
`.github/workflows/checks.yml`. No test, source, config, or `.gitignore` change;
`scripts/ci/known_test_failures.json` untouched.

No blocking rule (1-12) is implicated. No secret reaches the repo or a log — the
copied file holds placeholders only, and the job's sole uploaded artifact is
`backend/junit-backend.xml`.

---

## Findings

### F1 — `checks.yml`, added comment: `ensure_runtime_env_keys.py` does not do what the comment says it does

> "`ensure_runtime_env_keys.py` maintains it as the authoritative key set so it
> cannot silently rot."

`scripts/linux/ensure_runtime_env_keys.py` reads `.env.example` and writes into
`.env` — `update(path, example_path)` copies the example over a missing `.env`,
then appends any assignment present in the example and absent from `.env`. The
data flows example → env, one way. The script never inspects, validates or
maintains `.env.example`, and nothing in it would notice or prevent `.env.example`
rotting. The clause "so it cannot silently rot" names a protection that does not
exist.

Why it matters: this sentence is the branch's answer to the rule-13 question, and
it is the one load-bearing assertion in the comment that does not survive
checking. A later reader deciding whether `.env.example` is safe to rely on in CI
would be relying on the wrong mechanism. It is also gratuitous, because the real
answer is better and the branch earned it: after this change the **`backend` job
itself** is what makes `.env.example` rot loud —
`test_ai_gateway_routing.py::test_default_model_pools_have_rotation_capacity`
asserts over the tracked file, and `Settings` plus `conftest`'s
`_required_environment_variable` fail by name on a missing key. Say that instead.

The same sentence appears at `.plan/merge.md:177` and should be corrected there
too, though `merge.md` is outside this diff.

### F2 — `checks.yml`, added comment: "every green on this commit" overstates the blast radius

> "Every 'green on this commit' this file claimed was therefore replicated
> locally on a workstation that happened to have a `.env`, never observed in the
> pipeline."

The two "green on this commit" claims that actually appear in this file are the
`frontend-static` header ("All four green on this commit") and the `contracts`
header ("Green on this commit"). Neither job reads `.env` — finding 3 above
establishes that `contracts` is unexposed, and the frontend jobs are Node-only.
The `.env` defect therefore does not imply anything about those two claims, so
the "therefore" does not follow.

The defensible statement is narrower and still damning: the **`backend`** job
could never complete, so any belief that the backend suite was green in the
pipeline was replicated locally and never observed there. Scope the sentence to
the backend job.

This one is raised because it argues in the overstating direction. The
undercounts elsewhere in the comment are not findings and I am not raising them —
for the record, `linux_kit/run.sh:31` runs `cp .env.example .env`
unconditionally, `scripts/bootstrap_host.ps1:25` runs the Windows twin,
`scripts/prepare_runtime_configuration.sh:123-125` drives
`ensure_runtime_env_keys.py` with `--example-file`, and
`scripts/linux/environment_report.sh:710` and
`scripts/start_stage4m_simulation.sh:5` instruct. "Two further scripts instruct
developers to" undercounts and mislabels, but only makes the "not an invented
command line" argument weaker than the evidence supports, which harms nobody.

---

## Claims checked and sustained

| Claim | Result |
| --- | --- |
| `.gitignore:25` is `.env` | Sustained — `git check-ignore -v` names that exact line |
| `.gitignore:31` is `.env.*` | Sustained |
| `scripts/bootstrap_host.sh:17` runs the copy | Sustained (`cp "$ROOT/.env.example" "$ROOT/.env"`) |
| `scripts/linux/reset_docker_environment.sh:85` runs the copy | Sustained |
| Absent `.env` → INTERNALERROR, exit 3 | Sustained — reproduced |
| `.env.example` → `1 failed, 5232 passed, 11 skipped, 514 deselected` | Sustained — reproduced exactly |
| Sole failure is the allowlisted one | Sustained — present in `known_test_failures.json` |
| Credential incident is what the `.gitignore` comment records | Sustained — faithful paraphrase |
| `export_openapi.py` exits 0 with no `.env` | Sustained — reproduced |
| `.env.example` results byte-identical to a real `.env` | **Not verified.** I declined to copy a populated `.env` into a scratch tree. The key sets are not identical either — the local `.env` carries 11 keys `.env.example` does not (`PLATFORM_TEST_NEO4J_URI`, `TEMPORAL_PORT`, and similar, all live-infra or host-port keys that the default run deselects). This does not affect the fix: CI only ever sees `.env.example`, and that measurement I did reproduce. Flagged as scope-limited, not raised as a finding. |

## Considered and dismissed

- `docs/archive/stage-plans/STAGE_4_HLD_ALIGNMENT_NEXT_STEPS_EXECUTION_PLAN.md:120`
  says "Never run unconditional `cp .env.example .env`", and this step is
  unconditional. Dismissed: that rule guards a developer's populated `.env`
  against being clobbered. After `actions/checkout@v4` on `ubuntu-latest` there
  is no `.env` to clobber, and the file is gitignored so it cannot arrive with
  the checkout. Adding `test -f .env ||` would be harmless but is taste, not a
  finding.
- The existing scripts `chmod 600` after copying and the CI step does not.
  Immaterial on an ephemeral runner holding placeholders. Not a finding.
- Step placement (after `checkout`, before `setup-python`). Correct — `cp` needs
  no interpreter, and the default working directory is the repository root,
  which is where `conftest` looks.

---

## To resolve

Reword the two sentences in F1 and F2. No YAML change, no code change. Resubmit
and I will re-review the complete updated diff.
