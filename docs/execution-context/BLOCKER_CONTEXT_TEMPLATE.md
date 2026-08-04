# Blocker Context

## Repository state

- Target branch:
- Current local branch:
- Local commit:
- Remote commit:
- Working tree:

## Active execution

- Phase:
- Step:
- Task ID:
- Agent role:
- Status: `BLOCKED`

## Exact blocker

Describe the exact condition preventing safe progress.

## Evidence

### Commands executed

```text
<exact command>
```

### Results

| Command | Exit code | Relevant output |
|---|---:|---|

## Files modified but not committed

| Path | State | Safe to preserve |
|---|---|---|

Use `NONE` when no files were modified.

## Attempts made

| Attempt | Result | New diagnostic signal |
|---|---|---|

## Why continuing is unsafe

Explain the concrete risk.

## Required resolution

State the exact credential, infrastructure, repository action, decision or information needed.

## Safe resume command

```powershell
<exact safe resume command>
```

## Hallucination verification

- Blocker reproduced: YES/NO
- Commands executed: YES/NO
- Evidence recorded: YES/NO
- Unsupported assumptions present: YES/NO
