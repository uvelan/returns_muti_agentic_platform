# SEC-01 — history purge runbook

**Status: PREPARED, NOT EXECUTED.**
Nothing in this directory has rewritten history. `purge_history_secrets.sh` is
dry-run by default and refuses to execute without an explicit confirmation
phrase, a fresh mirror clone, and a typed acknowledgement at two separate
points.

The purge runs **once, at integration cutover**, after every parallel track has
merged or been abandoned. Running it earlier invalidates every worktree and
every open branch in flight.

---

## 1. What actually leaked

Established by `scripts/security/scan_secrets.py --mode history` at baseline
`0615921`. Nine distinct credential values, in three files. Values are
identified by `sha256[:16]`; they appear nowhere in this repository.

| # | Provider / service | Variable | Shape | Paths in history |
|---|---|---|---|---|
| 1 | **NVIDIA** (build.nvidia.com / NGC) | `PLATFORM_NVIDIA_API_KEYS[0]` | `nvapi-*`, 70 ch | `backend/.env.vault-backup`, `backend/tests/conftest.py` |
| 2 | **Google** (Generative Language / AI Studio) | `PLATFORM_GOOGLE_API_KEYS[0]` | `AIza*`, 39 ch | `backend/.env.vault-backup`, `backend/tests/conftest.py` |
| 3 | **Google** (second, distinct key) | `PLATFORM_GOOGLE_API_KEYS[1]` | `AIza*`, 39 ch | `backend/.env.vault-backup` |
| 4 | SQL Server | `MSSQL_SA_PASSWORD` | 40 ch | `.env.vault-backup`, `backend/.env.vault-backup` |
| 5 | Neo4j | `GRAPH_PASSWORD` | 40 ch | `.env.vault-backup`, `backend/.env.vault-backup` |
| 6 | MongoDB | `MONGO_ROOT_PASSWORD` | 40 ch | `.env.vault-backup`, `backend/.env.vault-backup` |
| 7 | MongoDB replica set | `MONGO_REPLICA_SET_KEY` | 96 ch | `.env.vault-backup`, `backend/.env.vault-backup` |
| 8 | Temporal | `TEMPORAL_DB_PASSWORD` | 40 ch | `.env.vault-backup`, `backend/.env.vault-backup` |
| 9 | Valkey | `VALKEY_PASSWORD` | 40 ch | `.env.vault-backup`, `backend/.env.vault-backup` |

### Two corrections to the SEC-01 finding as written

The audit records the exposure as *one* provider key, in `.env.vault-backup`,
remaining in `bb9bf2e`. Both halves of that are narrower than the evidence.

1. **The provider keys are not in the root `.env.vault-backup`.** That file's
   `PLATFORM_*_API_KEYS` entries are all `[]`. The provider keys are in
   `backend/.env.vault-backup`, which commit `5b5392a` untracked — *earlier*
   than `0615921`. `0615921` removed the root file, which carried the six
   infrastructure passwords instead. Purging only what `0615921` removed leaves
   the provider keys in place.

2. **The provider keys entered history long before `bb9bf2e`.** The same NVIDIA
   key and the same first Google key were hardcoded in
   `backend/tests/conftest.py` from `52732a5`, and were removed by `fbfcf05`
   ("security(tests): remove hardcoded live NVIDIA/Google API keys from test
   fixture"). `bb9bf2e` is a re-exposure of an already-exposed key, not the
   first one. The exposure window is therefore `52732a5..HEAD`, not
   `bb9bf2e..HEAD`.

3. **The second Google key has never been mentioned anywhere** — not in the
   audit, not in a commit message. No rotation has been claimed for it.

### What is *not* a finding

Checked and cleared, so nobody re-checks them:

- `linux_kit/returns_platform.tar.gz` (15 MB, in `91b2bf8`) — every `.env`
  inside it holds placeholders identical to the packaged `.env.example`. No
  live credential.
- `PLATFORM_MONGO_DSN`, `PLATFORM_SOURCE_MONGO_DSN`, `PLATFORM_NEO4J_PASSWORD`,
  `PLATFORM_VAULT_TOKEN_FILE` — the historical values are byte-identical to the
  committed `.env.example` / `compose.yaml` defaults. Documented defaults, not
  leaks.
- Two `sk-…`-shaped hits in `backend/tests/` — one is a deliberate fake inside
  `test_checkpoint_contains_no_secrets.py`, the other is English prose in a
  comment. False positives; the scanner's minimum length now excludes both.

---

## 2. EXTERNAL BLOCKER — revocation must be verified before the purge

**The purge does not make the credentials safe. Rotation does.** Removing a key
from history after it has been public for months closes the archive, not the
account. Anyone who fetched the repository already has all nine values.

`fbfcf05` and the `0615921` commit message claim rotation. **A commit message is
not evidence.** Rotation cannot be verified from inside this repository, and the
credentials required to check it are not available to the engineer who prepared
this runbook.

The repository owner must perform these checks and record the result:

| Provider | Exact check |
|---|---|
| **NVIDIA** | Sign in at <https://build.nvidia.com> (or NGC → *Setup* → *API Keys*). Confirm **no active key** whose value ends with the leaked key's last characters, and that key `sha256[:16]=6291454d01707e29` is **revoked, not merely superseded**. Then confirm the usage/billing log shows no calls after the rotation date. |
| **Google** | Google Cloud Console → *APIs & Services* → *Credentials*, in the project that owns the Generative Language API. **Two** keys leaked (`0d7468d555b8e40d`, `2bab9dd32edc0ef6`). Confirm both are **deleted** (not just restricted), and check *Metrics* for API calls after the rotation date. |
| SQL Server / Neo4j / MongoDB / Mongo replica set / Temporal / Valkey | These are compose-stack credentials. Confirm each has been changed in the running stack **and** in Vault, and that the old value no longer authenticates. The replica-set key requires a coordinated keyfile rotation across members. |

Until every row is confirmed, treat the credentials as **live and public**.

---

## 3. Preconditions

- [ ] Every row in section 2 confirmed and recorded.
- [ ] All parallel tracks merged or abandoned; no active `.claude/worktrees/agent-*`.
- [ ] Every open pull request merged or closed.
- [ ] Forks enumerated and deleted, or their owners notified.
- [ ] `python -m pip install --user git-filter-repo` succeeds.
- [ ] A secure, access-controlled location exists for the backup bundle.
- [ ] You can force-push to `https://github.com/uvelan/returns_muti_agentic_platform.git`.

---

## 4. Dry run

```bash
scripts/security/purge_history_secrets.sh --plan
```

Prints the exact command sequence and changes nothing. Read its output against
this runbook; they must agree.

---

## 5. Execute

```bash
SEC01_PURGE_CONFIRM='PURGE SEC-01 HISTORY' \
  scripts/security/purge_history_secrets.sh --execute \
    --mirror     /secure/sec01/returns.git \
    --backup-dir /secure/sec01/backup \
    --baseline   "$PWD/scripts/security/known_exposures.json"
```

What it does, in order:

1. **Fresh mirror clone.** `git filter-repo` refuses to rewrite anything else,
   and that refusal is a feature — it prevents a rewrite of a checkout that has
   uncommitted work.
2. **Protected backup outside the exposed history.** `git bundle create … --all`
   into `--backup-dir`, which must be outside the repository, plus a sha256 and
   mode `0600`. A local, unpushed tag `sec01/pre-purge-<ts>` pins the old tip.
   The tag is deliberately **not** pushed: pushing it would re-publish exactly
   the history being removed.
   **The bundle contains all nine credentials.** It is the rollback path and a
   second copy of the leak. Store it offline; destroy it once the purge is
   accepted.
3. **Recover replacement values** via `build_replacements.py`, into a
   `mktemp -d` file outside the repository, mode `0600`, shredded by an `EXIT`
   trap on every path including failure. The builder cross-checks every
   recovered value against `known_exposures.json` and **aborts** if history
   contains an unreviewed value, or if the baseline names a value history does
   not contain. That interlock is what stops the purge from silently missing one.
4. **Rewrite:**
   ```
   git filter-repo \
     --invert-paths --path .env.vault-backup --path backend/.env.vault-backup \
     --replace-text <replacements>
   ```
   Path removal for the two dead files; `--replace-text` for
   `backend/tests/conftest.py`, which is a live file and must survive with its
   historical values redacted to `***SEC-01-PURGED-CREDENTIAL***`.
5. **Verify** with `scan_secrets.py --mode history --no-allowlist` — the
   baseline is explicitly disabled so it cannot make a failed purge look clean —
   plus a `git log --all -- <path>` check per removed path. Any failure stops
   before the push.
6. **Force-push** `--all` and `--tags`, after a second typed confirmation.
   `--force-with-lease` is not used and cannot be: a lease compares against a
   ref this clone has already rewritten, so it never matches. The guards are the
   human confirmations and the fact that all branches were merged first.

### BFG fallback

Only if `git-filter-repo` cannot be installed. Two passes, since BFG cannot
delete a path and replace text at once:

```bash
java -jar bfg.jar --delete-files '.env.vault-backup' /secure/sec01/returns.git
java -jar bfg.jar --replace-text <replacements> /secure/sec01/returns.git
git -C /secure/sec01/returns.git reflog expire --expire=now --all
git -C /secure/sec01/returns.git gc --prune=now --aggressive
```

`--delete-files` matches by **filename**, so one rule covers both
`.env.vault-backup` and `backend/.env.vault-backup`. BFG never rewrites the
commit at HEAD — acceptable here only because both files were already untracked
before HEAD. Verify with `scan_secrets.py` exactly as in step 5 before pushing.

---

## 6. Rollback

Before the push, there is nothing to roll back: the remote is untouched. Discard
the mirror.

After the push:

```bash
git clone /secure/sec01/backup/pre-purge-<ts>.bundle restored
cd restored && git remote add origin <remote>
git push --force origin --all && git push --force origin --tags
```

This restores the leaked credentials to the remote. Only do it if the purge
broke something worse than the leak.

---

## 7. After the purge — required, not optional

1. **Every clone and worktree is invalid.** They hold commits that no longer
   exist; a `pull` produces an unrelated-histories merge that would reintroduce
   the secrets. Do not merge — re-clone.
   ```bash
   git worktree list
   git worktree remove <path> --force   # for each
   cd .. && rm -rf <old-checkout>
   git clone <remote> <fresh-checkout>
   ```
   This repository carries `.claude/worktrees/agent-*` worktrees; every one is
   pinned to a pre-purge commit.
2. **Ask GitHub Support to purge cached views and stale refs.** Rewritten
   objects stay reachable through the API, through forks, and through pull
   request refs until they do. Deleting forks yourself is faster.
3. **Re-open every pull request** from re-cloned branches.
4. **Confirm push protection is still on:** *Settings → Code security → Secret
   protection → Push protection*. The purge does not change it; confirm rather
   than assume. Do not use a bypass to land the rewritten history — if push
   protection blocks the force-push, the purge did not work.
5. **Empty the baseline.** Set `known_exposures` to `[]` in
   `scripts/security/known_exposures.json` and delete the `[allowlist]` section
   from `.gitleaks.toml`. Both exist only to keep the history gate meaningful
   *while* the nine are pending. Left in place, they re-permit the purged paths.
   The `full-history` CI job then becomes a plain "history is clean" assertion.
6. **Destroy the backup bundle** once the purge is accepted.

---

## 8. The gate that prevents the next one

`.github/workflows/secret-scan.yml` — this repository had no CI at all before
SEC-01; this is the first workflow.

| Job | Scope | Baseline applied? |
|---|---|---|
| `incoming-changes` | only the commits this push/PR introduces | **no** (`--no-allowlist`) |
| `working-tree` | tracked files at the merge result | **no** (`--no-allowlist`) |
| `full-history` | every blob reachable from every ref | yes — the reviewed nine |

The first two jobs are the pre-merge gate and cannot inherit the SEC-01
baseline: nothing arriving now gets to reuse an exemption written for what
already leaked. `full-history` uses the baseline so it fails on a tenth
credential rather than drowning in the nine that are already tracked.

`gitleaks` (pinned 8.28.0, run with `--redact` so a public Actions log never
becomes the second place a credential leaked) is the primary engine in
`full-history`. `scan_secrets.py` runs everywhere, has no dependencies, owns the
baseline and the redaction contract, and is the tool that verifies the purge.

This is layered **under** GitHub's push protection, not instead of it: push
protection rejects known provider-key shapes at `git push`, before any workflow
starts. It would not have caught the six infrastructure passwords, which have no
distinguishing shape — those are what the `platform-infrastructure-password`
rule in `.gitleaks.toml` exists for.

---

## 9. Files

| Path | Role |
|---|---|
| `scripts/security/scan_secrets.py` | Scanner. Worktree / range / history modes. Never prints a value. |
| `scripts/security/known_exposures.json` | Reviewed baseline of the nine, keyed by `sha256[:16]`. Emptied after the purge. |
| `scripts/security/build_replacements.py` | Recovers values at run time; interlocks against the baseline. |
| `scripts/security/purge_history_secrets.sh` | The purge. Dry-run by default. |
| `.gitleaks.toml` | gitleaks rules + the SEC-01 allowlist. |
| `.github/workflows/secret-scan.yml` | The three CI gates. |
