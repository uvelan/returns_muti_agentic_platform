# Windows Multi-Agent Execution Workflow

## One-time setup

Use Antigravity IDE as the only graphical IDE and Codex CLI from its integrated PowerShell terminal.

```powershell
Set-Location 'K:\Projects\FEG\Ret\full\returns_platform'

git fetch --all --prune
git switch feat/v2-order-discovery-integration
git pull --ff-only origin feat/v2-order-discovery-integration

git status
git branch --show-current
git rev-parse HEAD
git rev-parse origin/feat/v2-order-discovery-integration
```

Adjust the repository path when required.

Install Codex CLI:

```powershell
npm install --global @openai/codex@latest
codex --version
codex
```

## Recommended sessions

```text
Antigravity conversation 1:
Gemini 3.1 Pro — critical architecture and orchestration

Antigravity conversation 2:
Gemini 3.6 Flash — focused analysis and validation

Antigravity conversation 3:
Claude Sonnet 4.5 — independent review

Integrated PowerShell terminal:
Codex CLI — implementation, repair, commit and push
```

Gemini 3.5 Flash is optional for mechanical context maintenance.

## Per-task workflows

### SMALL

```text
Codex implementation
→ Gemini 3.6 Flash validation
→ Codex context update, commit and push
```

### NORMAL

```text
Gemini 3.6 Flash focused analysis
→ Codex implementation and focused tests
→ Sonnet 4.5 independent review
→ Codex repairs when needed
→ Gemini 3.6 Flash independent validation
→ Codex context update, commit and push
```

### CRITICAL

```text
Gemini 3.1 Pro architecture analysis
→ Codex implementation
→ Sonnet 4.5 code and security review
→ Codex correction loop
→ Gemini 3.6 Flash independent validation
→ Codex context update, commit and push
```

## Concurrency rule

Only one agent may write production code.

```text
Analysis completes
→ Codex writes
→ Codex stops editing
→ Sonnet reviews read-only
→ Codex repairs
→ Gemini validates read-only
→ Codex commits and pushes
```

Do not run Codex and a write-capable Antigravity agent against the same working tree simultaneously.

## PowerShell equivalents

| Purpose | PowerShell |
|---|---|
| Current directory | `Get-Location` |
| List files | `Get-ChildItem` |
| Read file | `Get-Content <path>` |
| Search text | `Select-String -Path <path> -Pattern <text>` |
| Find command | `Get-Command <name>` |
| Set environment variable | `$env:NAME = 'value'` |
| Remove file | `Remove-Item <path>` |
| Remove directory | `Remove-Item -Recurse -Force <path>` |
| Copy | `Copy-Item` |
| Move | `Move-Item` |

## Proportionate validation

During implementation, run focused tests and relevant type/lint checks.

At phase gates, run broader integration checks.

At final completion, run full backend, frontend, infrastructure and adversarial validation.

Typical backend commands:

```powershell
Set-Location backend
poetry run ruff check <changed-paths>
poetry run mypy <changed-paths>
poetry run pytest <focused-test-paths> -q
```

Typical frontend commands:

```powershell
Set-Location frontend
npm run lint
npm run typecheck
npm test -- --run <focused-test>
npm run build
```

Use repository-defined commands when they differ.

## Commit and push

```powershell
git status
git diff --check
git diff --stat

git add `
  '<explicit production file>' `
  '<explicit test file>' `
  '<explicit context directory>'

git commit -m "feat(scope): implement completed capability"
git push origin feat/v2-order-discovery-integration
git fetch origin

$local = git rev-parse HEAD
$remote = git rev-parse origin/feat/v2-order-discovery-integration

if ($local -ne $remote) {
    throw "Local and remote heads do not match."
}

git status
```

## Linux-specific final gates

Use Windows for implementation, review, unit tests, lint, type checking, frontend build, commits and pushes.

Run Linux-specific gates only where the repository explicitly requires Linux, such as host shell scripts, Docker infrastructure, Temporal workers or authoritative real end-to-end execution.
