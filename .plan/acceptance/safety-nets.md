# Safety nets — the two never-executed guarantees, executed

ACC phase 2, dispatch condition 1. Both of the harness's "arguments rather than
observations" are now observations. Recorded before any scenario was written,
because a scenario built on an unproven kill primitive is built on sand.

Stack: brought up from `compose.yaml` (`sqlserver`, `mongodb`, `valkey`, `neo4j`,
`temporal` + `temporal-postgresql`). All five host ports reachable — but see the
defect at the bottom: the live-suite entry point preflights the wrong SQL Server
port and would have refused to run against a stack that is up.

---

## (a) `test_chaos_restart_smoke_real_infra.py` — first execution ever

Never run before: the datastores were down when it was written (ACC phase 1),
and RV carried it forward as "the first thing run when the stack comes up".

```
tests/harness/test_chaos_restart_smoke_real_infra.py -m live_infra
1 passed in 40.57s
```

The real `run_return_workflow_worker.py` starts from the harness, survives the
20s settle window, is killed, and comes back with a different pid. The three
assumptions this file exists to test — script paths, working directory,
inherited environment — all hold against the deployment's own entry point.

### Fault injection

| # | injected fault | result | verified how |
| --- | --- | --- | --- |
| INJ-4a | `WorkerSpec.env={"PLATFORM_MONGODB_URI": <unreachable>}` | **1 passed — MISS** | see below |
| INJ-4b | `WorkerSpec.env={"PLATFORM_MONGO_DSN": <unreachable>}` | **1 failed** | injection proved to be a real fault first |
| — | reverted | 1 passed (40.36s) | `git status` clean |

**INJ-4a is the finding, and it is about the injection, not the test.**
`PLATFORM_MONGODB_URI` is not a variable this repo defines — `.env` names
`PLATFORM_MONGO_DSN`. So the "green" was an **ineffective injection**: nothing
was actually broken, and recording it as "the test is blind" would have been the
mirror image of `merge.md`'s newest shape (*an injection red for the wrong
reason*) — a **green** for the wrong reason, nearly written down as evidence
about the test.

What separated them was refusing to conclude from the green alone. The fault was
run **directly** against the worker first:

```
PLATFORM_MONGO_DSN=mongodb://127.0.0.1:59999/... python scripts/run_return_workflow_worker.py
→ pymongo.errors.ServerSelectionTimeoutError ... exits
```

and the spec was read back out of the module the test actually imports
(`.../agent-ad292c7edae9893d2/backend/tests/harness/chaos_restart.py`, not the
main checkout's copy) to confirm the overlay was present at the point of use.
Only then did the red mean the test was sighted. **The tell:** the worker dies at
~10s and `is_running` flips at 10s, inside the 20s settle window — so a real
startup death is caught with 10s of margin, and `_SETTLE_SECONDS` is doing work
rather than decorating.

---

## (b) The SIGTERM link — proved on a platform that has one

The behavioural pin
`test_chaos_restart.py::test_stop_lets_the_worker_handle_its_signal_and_kill_does_not`
is `skipif(os.name == "nt")`. This run's dev platform is Windows, so it has never
executed. RV narrowed the single unproven link to whether
`os.killpg(os.getpgid(pid), SIGTERM)` reaches the child through the session
`start()` establishes with `start_new_session=True`.

`tests/harness/posix_signal_proof.py` executes exactly that, under Linux:

```
docker run --rm -v <worktree>/backend:/w -w /w python:3.13-slim \
    python tests/harness/posix_signal_proof.py

python 3.13.14 on linux, pid 1
PASS  start_new_session puts the worker in its own process group
PASS  killpg(getpgid(pid), SIGTERM) reaches the grandchild through the session
PASS  stop() gives the worker its SIGTERM handler (the drain path)
PASS  kill() does not run the worker's SIGTERM handler (the crash path)
all four links proved
```

It is a script, not a test, for two reasons: `tests/conftest.py` imports
`return_platform`, so collecting one harness test in a bare container would need
the whole dependency tree installed there, while `chaos_restart.py` imports only
the standard library; and a `test_`-named file would be **collected and skipped**
on the dev platform, which is the shape ("skipped on the platform that runs it")
this proof exists to close.

### Fault injection — three, each verified to have landed

| # | injected fault | anchor verified | result |
| --- | --- | --- | --- |
| INJ-1 | `start_new_session=True` → `{}` in `start()` | `git diff` + read-back of lines 178-192 | checks **1 and 2** fail |
| INJ-2 | `_signal_tree` POSIX branch always `SIGKILL` | `git diff -U0` → one line, 220, inside `_signal_tree` | check **3** fails |
| INJ-3 | `kill()` calls `_signal_tree(force=False)` | read-back of lines 228-240 confirms it is in `kill()`, not `stop()` | check **4** fails |

Every injection was applied by an anchored Python replace asserting
`count(old) == 1` — never a blind `str.replace` — then **read back from the file
at its line numbers** before the run. That is the discipline `merge.md`'s newest
shape demands: V1 phase 2's ordering injections matched a different endpoint and
silently deleted a block, producing plausible red. Here INJ-3's anchor
(`self._signal_tree(process.pid, force=True)`) appears in **both** `stop()` and
`kill()`; the `count(old) == 1` assertion passed only because the anchor included
the two following lines, and the read-back is what proves it landed in `kill()`.

### INJ-1 found a blind spot in this proof, and it was fixed before it shipped

On its first form, check 2 called `killpg` and watched a heartbeat file stop.
Under INJ-1 it reported **PASS**:

```
FAIL  start_new_session puts the worker in its own process group -- getpgid(7) == 1
PASS  killpg(getpgid(pid), SIGTERM) reaches the grandchild through the session   ← wrong
```

With no session the worker inherits the runner's group, so `killpg` signalled
**group 1 — everything** — and the grandchild stopped anyway. The check could not
distinguish *the signal traversed the session* from *the signal hit the whole
world*: `merge.md`'s "green because the inputs can't exercise the property",
found in my own instrument by my own injection.

Fixed by asserting the **scoping** half first — the target group must not be the
runner's own group — before the heartbeat is allowed to mean anything. Re-run
under INJ-1 with the fix in place:

```
FAIL  start_new_session puts the worker in its own process group
FAIL  killpg(...) reaches the grandchild -- the worker shares the runner's
      process group (1) -- killpg here signals the suite itself, so any observed
      death is collateral rather than the session doing its job
2 FAILED
```

and clean, with `chaos_restart.py` reverted and `git status` showing no
modification to it: **all four PASS, exit 0**.

---

## Production defect found (reported, not fixed)

**`scripts/dev/run_real_infra_suite.sh:56` preflights the wrong SQL Server port.**
It requires `SQL Server:14330`. `compose.yaml` publishes `127.0.0.1:11433->1433`
with a comment explaining exactly why it moved (WinNAT reserves 14267-14366 on
this host, so the 14330 bind fails **silently** while the container still reports
healthy), and `.env:94` agrees: `PLATFORM_SQLSERVER_PORT=11433`.

So the live-infra entry point — the only sanctioned way to run the 496 deselected
tests — **refuses to run against a stack that is fully up**, and does it with the
message "start the stack", which is the one message guaranteed not to lead anyone
to the port number. The script's own header names this failure by name
("ENV-ACTION-01 ... Temporal running, healthy, and publishing no host port at
all") and then reproduces it against a different service.

It is one line. It is **not fixed here**: `scripts/` is outside ACC's
`backend/tests/`-only scope. Every live-infra run recorded in this directory was
therefore invoked with `pytest -m live_infra` directly, with the five ports
verified by hand first.

Re-verified after the session break: `run_real_infra_suite.sh:56` still reads
`"SQL Server:14330"`; `compose.yaml:192` publishes
`127.0.0.1:${PLATFORM_SQLSERVER_PORT:-11433}:1433` and `.env:94` sets
`PLATFORM_SQLSERVER_PORT=11433`. The defect stands, unchanged.

---

## Re-verification after the session break (step:01)

The run above was interrupted by an API session limit before its commit landed,
so none of it was in the repo. Rather than trust the record, both nets were run
again from a cold start, and each was re-injected with a **different** fault
from the one recorded — a claim re-proved by repeating its own evidence is a
claim compared with itself.

**Stack state at re-verification:** all six containers `Up (healthy)` —
`sqlserver 11433`, `temporal 17233`, `neo4j 17474/17687`, `mongodb 27017`,
`valkey 6379`, `temporal-postgresql` (internal).

| net | clean re-run | independent injection | injection verified real first | result under injection |
| --- | --- | --- | --- | --- |
| (a) live-infra smoke | **1 passed, 40.66s** | `RETURN_WORKFLOW_WORKER.env = {"PLATFORM_TEMPORAL_TARGET": "localhost:59998"}` (recorded run used a Mongo DSN) | ran `scripts/run_return_workflow_worker.py` with that env directly → `RuntimeError: Failed client connect … ConnectionRefused`, exit 1 | **1 failed in 7.93s** |
| (b) POSIX SIGTERM proof | **all four links proved**, exit 0 | `_signal_tree`: `SIGKILL if force else SIGTERM` → `SIGKILL` (recorded runs used INJ-1/2/3 by other anchors) | `git diff -U0` shows exactly one changed line, and it is the only occurrence in the file | **check 3 FAIL** — "stop() gives the worker its SIGTERM handler (the drain path) … every POSIX teardown is a SIGKILL"; checks 1, 2, 4 still PASS |

Note the shape of (b)'s red: only the drain check fails, and the crash check
(4) stays green — which is what a `force`-ignoring `_signal_tree` should do and
not what a broken file or a collapsed import would do. That asymmetry is the
verification that the injection did what it claims, per `merge.md`'s newest
failure shape.

Both injections were reverted with `git checkout`; `git status` showed
`tests/harness/chaos_restart.py` unmodified after each.

**Verdict: both safety nets hold, and the interrupted run's record is accurate.**


---

## Correction (RV ACC2-1, F1) — this record was wrong in both directions

**It was a guard with no gate.** `posix_signal_proof.py` was written
deliberately uncollectable, so pytest could not silently skip it on Windows, and
then **run once, by hand**. That is RV rule 13 exactly, on the branch that made
rule 13 its subject. It is now invoked by
`backend/tests/acceptance/test_the_posix_signal_proof_is_gated.py`, which runs
the script as a subprocess and asserts exit 0, **four** `PASS` lines, and the
closing "all four links proved" — the last two because a script that stopped
running its checks would also exit 0. The script stays a script: it imports only
the standard library plus `chaos_restart`, which is what lets it run in a bare
`python:3.13-slim`, and porting its checks into pytest would drag
`conftest.py`'s `return_platform` import in and lose that.

**And the residual risk above was overstated.** `.github/workflows/checks.yml`
runs every job on **`ubuntu-latest`**. So
`test_chaos_restart.py::test_stop_lets_the_worker_handle_its_signal_and_kill_does_not`
— the behavioural pin this proof was written to substitute for — **executes on
every push**. "It has never run" was true of this Windows workstation and false
of the pipeline, and the distinction was never drawn. The substitution is a
convenience for dev machines, not a stand-in for absent coverage.

Verified rather than argued: on Windows the new module reports `1 passed,
1 skipped` with the reason spelled out; under `python:3.13-slim` every assertion
it makes holds (`returncode 0`, `PASS lines 4`, closing line present); and with
`start_new_session=True` removed from `WorkerProcess.start()` the same
assertions fail, so the gate is not decoration. Injection reverted; `git status`
clean.
