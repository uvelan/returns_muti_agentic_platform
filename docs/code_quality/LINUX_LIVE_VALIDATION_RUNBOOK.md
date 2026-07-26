# Linux Live Validation Runbook

## Current status

Stage 4O source remediation is committed at the required `b278776` prefix.
Linux runtime validation has not yet run. A `SANDBOX_VALIDATED`,
`LIVE_STACK_VALIDATED`, or `PRODUCTION_READY` claim is prohibited until the
commit-bound Linux evidence archive passes every automated phase and the
23-screen manual attestation gate.

## Prerequisites

- Linux host with Python 3.13, Node 24, npm 11, Docker with Compose, Git, curl,
  jq, tar, and sha256sum.
- Root `.env` created locally from `.env.example` and populated with the
  environment's credentials. Never transfer or package `.env`.
- Ports configured in `.env` available on the host.
- A clean `master` checkout whose HEAD descends from the Stage 4O commit with
  prefix `b278776`.

The backend, frontend, and workers run as host processes. Docker is used only
for infrastructure.

## First run

From the verified repository root:

```bash
chmod +x scripts/*.sh scripts/linux/*.sh scripts/linux/lib/*.sh
./scripts/linux/run_full_linux_validation.sh --from-start
```

The master command executes prerequisite, commit/tree, environment, quality,
contract, infrastructure, seed, process, heartbeat, API, all-six-scenario,
AI live-stack, real browser, accessibility, restart/replay, and repository-state
phases. Logs, receipts, checkpoints, and PIDs are stored below
`.runtime/linux-validation/`.

The first pass intentionally stops at the manual screen gate and creates:

```text
.runtime/linux-validation/evidence/manual-screen-validation.json
```

Inspect every listed route against the real stack. Record the concrete
`resolvedUrl` used for dynamic routes, set each route status and the top-level
status to `PASS`, record the operator and a timezone-aware ISO 8601 `checkedAt`,
then resume:

```bash
./scripts/linux/run_full_linux_validation.sh --resume
```

The attestation is bound to the current full commit and tree fingerprint. A
source/configuration change invalidates it.

## Resume

```bash
./scripts/linux/run_full_linux_validation.sh --resume
```

Checkpoints contain the reviewed tree fingerprint and are accepted only when
the repository is clean and their structured phase receipt says `PASS`. Source,
configuration, staged, or untracked non-ignored changes invalidate downstream
checkpoints. The final receipt fails closed if any of the 21 canonical phase
receipts is missing or bound to a different commit/tree.

To leave a successful stack running for inspection:

```bash
./scripts/linux/run_full_linux_validation.sh --resume --keep-running
```

Failed runs preserve infrastructure and host-process state automatically.
The manual screen phase is expected to fail once to create its attestation
template; this is not a product failure.

## Failure evidence

```bash
./scripts/linux/15_collect_failure_evidence.sh
./scripts/linux/16_generate_linux_receipt.sh
./scripts/linux/package_validation_results.sh
```

Fix confirmed source defects with `apply_patch`, rerun the affected gates, and
commit them separately before restarting `--from-start`. Never alter or package
the root `.env`.

## Safe shutdown

```bash
./scripts/linux/17_stop_host_processes.sh
./scripts/linux/18_stop_infrastructure.sh --stop
```

The infrastructure stop command does not remove volumes. Destructive reset is
not part of the normal validation path.

## Return evidence to Windows

The packaging command prints:

```text
artifacts/linux-validation-<UTC_TIMESTAMP>.tar.gz
artifacts/linux-validation-<UTC_TIMESTAMP>.tar.gz.sha256
```

Import those files on Windows:

```powershell
.\scripts\windows\import_linux_results.ps1 `
  -ArchivePath "<path-to-linux-validation-archive>" `
  -ChecksumPath "<path-to-sha256-file>"
```

If import succeeds, rerun the final Windows gates and
`.\scripts\windows\finalize_review.ps1`. A Linux failure must be classified from
the receipt and logs; manually typed summaries are not evidence.
