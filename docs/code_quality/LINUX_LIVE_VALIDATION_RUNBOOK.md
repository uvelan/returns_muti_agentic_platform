# Linux Live Validation Runbook

## Current status

Windows remediation is in progress. Linux validation has not yet run. A `PASS`
or `PARTIAL` final verdict is prohibited until a returned Linux evidence archive
has passed checksum and receipt validation on Windows.

## Prerequisites

- Linux host with Python 3.13, Node 24, npm 11, Docker with Compose, Git, curl,
  jq, tar, and sha256sum.
- Root `.env` created locally from `.env.example` and populated with the
  environment's credentials. Never transfer or package `.env`.
- Ports configured in `.env` available on the host.
- The verified handoff archive and its adjacent SHA-256 file.

The backend, frontend, and workers run as host processes. Docker is used only
for infrastructure.

## First run

From the verified repository root:

```bash
chmod +x scripts/linux/*.sh scripts/linux/lib/*.sh scripts/generated-fixes/*.sh
./scripts/linux/run_full_linux_validation.sh --from-start
```

The master command executes deterministic transfer, environment, quality,
contract, infrastructure, seed, process, heartbeat, API, and real E2E phases.
Logs, receipts, checkpoints, and PIDs are stored below
`.runtime/linux-validation/`.

## Resume

```bash
./scripts/linux/run_full_linux_validation.sh --resume
```

Checkpoints contain the reviewed tree fingerprint and are accepted only when
their structured phase receipt says `PASS`. Source or configuration changes
invalidate downstream checkpoints automatically because the fingerprint
changes.

To leave a successful stack running for inspection:

```bash
./scripts/linux/run_full_linux_validation.sh --resume --keep-running
```

Failed runs preserve infrastructure and host-process state automatically.

## Failure evidence

```bash
./scripts/linux/15_collect_failure_evidence.sh
./scripts/linux/16_generate_linux_receipt.sh
./scripts/linux/package_validation_results.sh
```

Do not edit source code on Linux. Use a generated repair script only when its
printed precondition matches the observed environment issue. Source defects
must return to Windows with the evidence archive.

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
