# AMEND6 — executing AMENDMENT-6

Append-only. One entry per step. Every command block below is **captured**, not
transcribed: `scripts/dev/ledger_capture.sh` redirects the process's own bytes
into this file.

**Task.** `.plan/contracts.md` §1a AMENDMENT-6 retires `support_digest`,
`clarifications` and `parked_messages` from `CasePanelView`. It was ruled and
never executed. RV sustained it blocking under rule 2 (contract drift) as `E1`
in `.plan/reviews/ACC4-1.md`, escalated to the orchestrator rather than charged
to ACC4.

**Environment.** The only venv is installed editable against the **main**
worktree's `src` via a plain-path `.pth`, so a bare interpreter call from this
worktree imports whatever branch the main tree is on. Every Python command in
this ledger therefore sets
`PYTHONPATH=K:\Projects\Ret\rmap-amend6\backend\src`. Step 1 proves the trap and
the fix rather than asserting them.

---

## Step 1 — base, verified by ref

### `git rev-parse HEAD`

```
c8eac86d642a098943c203597f53c85a5f85c2a9
```

*exit 0*

### `git rev-parse --abbrev-ref HEAD`

```
feat/amendment-6
```

*exit 0*

### `git rev-list --left-right --count refs/heads/refactor/unified-return-platform...refs/remotes/origin/refactor/unified-return-platform`

```
354	0
```

*exit 0*

### `git merge-base --is-ancestor refs/heads/refactor/unified-return-platform HEAD`

```
```

*exit 0*

Local `refactor/unified-return-platform` is **354 ahead / 0 behind** `origin`,
so the local ref is the tip; `feat/amendment-6` is cut from it and the ref is an
ancestor of HEAD (exit 0 above).

---

## Step 2 — the editable-install trap, proved and neutralised

`backend/.venv` exists only in the main worktree, and
`site-packages/return_platform_backend.pth` is a **plain path** line pointing at
the main worktree's `src`. `.pth` paths are appended during `site` processing,
which runs *after* `PYTHONPATH`, so `PYTHONPATH` wins. Proved both directions:

### `cat "K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Lib\site-packages\return_platform_backend.pth"`

```
K:/Projects/Ret/returns_muti_agentic_platform/backend/src

```

*exit 0*

### `"K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" -c "import return_platform; print(return_platform.__file__)"`

```
K:\Projects\Ret\returns_muti_agentic_platform\backend\src\return_platform\__init__.py
```

*exit 0*

### `PYTHONPATH="K:\Projects\Ret\rmap-amend6\backend\src" "K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" -c "import return_platform; print(return_platform.__file__)"`

```
K:\Projects\Ret\rmap-amend6\backend\src\return_platform\__init__.py
```

*exit 0*

The bare call imports the **main worktree**; the `PYTHONPATH` call imports
**this** worktree. Two false failures today came from the first line. Every
Python command below sets it.

`frontend/scripts/export-contracts.js` computes the interpreter as
`<its own worktree>/backend/.venv/...`, which does not exist here, so the
regeneration step in step 6 passes `RETURN_PLATFORM_PYTHON` explicitly as well.

---

## Step 3 — baselines, before any change

`backend/tests/conftest.py::pytest_configure` raises without a repository-root
`.env`, which is gitignored and untracked. `checks.yml:213` copies
`.env.example`; done identically here.

### `cd backend && PYTHONPATH="K:\Projects\Ret\rmap-amend6\backend\src" "K:\Projects\Ret\returns_muti_agentic_platform\backend\.venv\Scripts\python.exe" -m pytest tests --collect-only -q 2>&1 | tail -1`

```
5251/5765 tests collected (514 deselected) in 6.88s
```

*exit 0*

### `cd frontend && npm test -- --maxWorkers=2 --reporter=default 2>&1 | grep -E "^ *(Test Files|Tests) "`

```
 Test Files  1 failed | 61 passed (62)
      Tests  2 failed | 865 passed (867)
```

*exit 1*

### `python -c "import json;print(json.load(open(\"scripts/ci/suite_size_floor.json\"))[\"suites\"])"`

```
{'backend': {'cases': 5251, 'files': 441}, 'frontend': {'cases': 860, 'files': 61}}
```

*exit 0*

**Baseline.** Backend 5251 collected (floor 5251). Frontend 62 files / 867
cases, 865 passed / 2 failed — the two pre-existing allowlisted
`registry.test.ts` failures (FE-DEFECT-2), reproduced identically to
`.plan/reviews/ACC4-1.md` §1. Frontend floor is 61 files / 860 cases; the suite
sits 7 cases above it, well inside `RESTAKE_ALLOWANCE = 0.25`.

The frontend suite exits 1 on those two, which is the tolerated path in
`checks.yml`; the verdict is `assert_known_failures.py`'s.

