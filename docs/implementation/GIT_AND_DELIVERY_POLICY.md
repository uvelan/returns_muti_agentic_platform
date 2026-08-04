# Git and Delivery Policy

## Target branch

```text
feat/v2-order-discovery-integration
```

## Before every step

```powershell
git fetch --all --prune
git status
git branch --show-current
git rev-parse HEAD
git rev-parse origin/feat/v2-order-discovery-integration
git pull --ff-only origin feat/v2-order-discovery-integration
```

Record the starting commit in step context.

## Branch policy

Do not create routine branches or worktrees.

A separate branch or worktree is allowed only when:

- user-owned uncommitted work requires isolation
- local and remote history diverged
- a destructive experiment cannot safely run on the target branch
- the user explicitly requests isolation

Document the reason before creation.

## Commit policy

Use one logical step per commit.

Examples:

```text
feat(internal-store): add generic bootstrap contracts
feat(connectors): implement PostgreSQL source connector
feat(graph): add fenced rebuild lifecycle
feat(order-agent): add structured reasoning loop
fix(guards): reject stale graph evidence
test(sync): cover duplicate strong-anchor requests
```

Context and tests belong in the same commit as the implementation they describe.

## Staging policy

```powershell
git add `
  'backend\src\return_platform\example.py' `
  'backend\tests\test_example.py' `
  'docs\execution-context\phase-XX\PXX-SXX'
```

Avoid `git add .` unless every changed and untracked file has been inspected.

## Push verification

```powershell
git commit -m "feat(scope): implement completed capability"
git push origin feat/v2-order-discovery-integration
git fetch origin

$local = git rev-parse HEAD
$remote = git rev-parse origin/feat/v2-order-discovery-integration

if ($local -ne $remote) {
    throw "Pushed commit is not the remote branch head."
}
```

A step is not complete until local and remote heads match.

## Prohibited operations

Do not silently:

- force-push
- reset user work
- rewrite remote history
- merge divergent history without approval
- rebase shared work without approval
- delete unrelated files
- commit secrets
- generate ZIP delivery artifacts

## Continuous execution

After push verification:

1. Update master execution context.
2. Mark the step complete.
3. Identify newly ready work.
4. Continue automatically.

Stop only for a genuine documented blocker.
