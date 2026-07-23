# Agent Walkthrough, Review, and Status Reports

Every implementation agent must write its walkthrough, self-review, handoff, or
progress report in this directory.

## Required convention

- Use one Markdown file per stage or bounded work package.
- Prefer names such as `stage3b_status.md`, `stage3c_walkthrough.md`, or
  `inventory_review.md`.
- Update the same file when continuing the same stage instead of creating
  timestamped duplicates.
- Report the actual working-tree state and distinguish implemented, verified,
  deferred, blocked, and not started work.
- Include exact Docker commands and results. Do not report host npm/backend commands
  as Docker verification.
- Link to supporting evidence; do not copy large generated logs into the status file.
- Record known failures and follow-up work. Never convert fixture behavior into a
  live-validation claim.
- Keep screenshot evidence marked `DEFERRED` until the hardening stage.
- State whether a Git commit was created. The current user instruction is **no
  commits**.

## Minimum report template

```markdown
# <Stage or work package> status

Status: IN PROGRESS | BLOCKED | READY FOR REVIEW | COMPLETE

## Implemented

## Verification

| Docker command | Result | Details |
|---|---|---|

## Live versus fixture boundary

## Known issues and deferred work

## Files and evidence

## Handoff / next action

Screenshots: DEFERRED
Git commit created: NO
```

Reviewer-authored approval or rejection documents belong in
`docs/review/verdict/`, not in this status directory.
