# Handoff · saga-agent

**Writer:** this agent only. **Readers:** coordinator and dependent agents.
**Append-only.** Newest entry at the bottom.

Every turn starts with a context receipt:

```
CONTEXT_RECEIPT | task=<id> | baseline=<commit> | context_version=<n> | contracts_hash=<sha256> | decisions_through=<D-n>
```

Every completed task ends with:

```
HANDOFF
task_id:
status: COMPLETE | BLOCKED
baseline_commit:
result_commit:
files_changed:
contracts_implemented:
tests_run:
test_result_summary:
known_baseline_failures:
unresolved_risks:
consumer_actions:
```

A missing fact that affects an interface or a business guarantee is a blocker, not
a judgement call:

```
BLOCKER
task_id:
repository_evidence:
affected_contract:
why_execution_cannot_continue:
minimum_decision_required:
safe_unaffected_work_remaining:
```

---

## Entries

*(none yet)*
