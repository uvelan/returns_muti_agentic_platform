# Execution Policy

## Status values

```text
NOT_STARTED
READY
ANALYZING
ANALYZED
IMPLEMENTING
IMPLEMENTED
REVIEWING
CHANGES_REQUIRED
REVIEWED
VALIDATING
VALIDATION_FAILED
VALIDATED
INTEGRATING
PUSHING
COMPLETE
BLOCKED
```

## Required normal transition

```text
READY
→ ANALYZING
→ ANALYZED
→ IMPLEMENTING
→ IMPLEMENTED
→ REVIEWING
→ REVIEWED
→ VALIDATING
→ VALIDATED
→ INTEGRATING
→ PUSHING
→ COMPLETE
```

## Failure loops

```text
REVIEWING → CHANGES_REQUIRED → IMPLEMENTING
VALIDATING → VALIDATION_FAILED → IMPLEMENTING
```

## Completion rule

A step cannot be `COMPLETE` until:

- latest remote code was fetched
- starting commit was recorded
- required analysis exists
- implementation is complete
- focused tests pass
- review is approved when required
- security review is approved when required
- independent validation passes
- agent contexts exist
- step completion context exists
- one logical commit exists
- push succeeds
- local and remote heads match
- master execution context is updated

## Parallel execution

Allowed:

- separate read-only analyses
- test design
- security analysis
- review planning
- validation planning

Not allowed:

- two production writers in one working tree
- concurrent changes to the same public contract
- parallel migrations with unresolved ordering
- duplicate repository-wide analysis
- duplicate validation of unchanged code

## Handoff

Every handoff contains only:

```text
Task ID
Current commit
Changed files
Context path
Review or validation status
Blockers
Required next action
```

The receiving agent reads the referenced context instead of requesting a repeated summary.
