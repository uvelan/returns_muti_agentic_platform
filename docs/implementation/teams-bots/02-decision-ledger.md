# 02 · Decision ledger

**Writer:** coordinator only. **Append-only — never rewrite an old decision.**
Agents read from their last recorded decision id onward.

---

## D-1 · Teams delivery states use the existing outbox literals

**Raised by:** coordinator, Wave 0 verification.
**Affects:** plan §5.5, contract C5. **Agent:** B.

**Conflict.** The plan freezes
`PENDING | RETRYING | DELIVERED | BLOCKED | DEAD_LETTERED`. The repository uses
different literals:

| Plan | Actual | Evidence |
|---|---|---|
| `RETRYING` | `RETRY` | `operations/integrations/outbox.py:97` |
| `BLOCKED` | `BLOCKED_EXTERNAL_DEPENDENCY` | `outbox.py:422` |
| `DEAD_LETTERED` | `DEAD_LETTER` | `outbox.py:100` |
| *(absent)* | `DISPATCHING` | `outbox.py:368` |

**Why it matters.** `CLAIMABLE_STATUSES = ("PENDING", "RETRY")` is used verbatim
in the claim query (`outbox.py:357`) **and in a partial index filter**
(`outbox.py:206`). A row written `RETRYING` would never be claimed and would sit
outside that index — delivery would stop silently, with no error anywhere.

**Decision.** The plan's names are descriptive prose, not a migration instruction.
Agent B **reuses the existing literals**. No new status value is introduced and no
migration is written. C5 records the real set.

---

## D-2 · `origin_channel` and `delivery_transport` are not case-fact fields

**Raised by:** coordinator, Wave 0 verification.
**Affects:** plan §5.8, contract C8. **Agents:** B, C.

**Conflict.** The plan freezes the authoritative fact as
`source_system=RETURN_SUPPORT_SERVICE, origin_channel=CHANNEL_B, delivery_transport=MICROSOFT_TEAMS`.

`append_case_fact` (`operations/case_repository.py:328-342`) accepts
`fact_id, case_id, fact_name, value, agent_id, channel: FactChannel,
acquisition_method: FactAcquisition, turn_id, source_system, source_path,
observed_at, supersedes_fact_id`.

**Neither `origin_channel` nor `delivery_transport` exists anywhere in the
codebase** (verified by repository-wide search).

**Decision.** Map to the real fields rather than adding parameters:

```
channel       = FactChannel.CHANNEL_B      # what origin_channel meant
source_system = "RETURN_SUPPORT_SERVICE"   # unchanged
```

`delivery_transport` is **not** written to the case fact. It belongs on the
outbox/delivery record, which is consistent with the plan's own rule two lines
earlier — *"Teams delivery status is integration metadata, not business workflow
state."* Putting it on the fact would make transport metadata part of business
provenance.

---

## D-3 · Test baseline is measured, not quoted

**Raised by:** coordinator, Wave 0. **Affects:** plan §10, acceptance criteria.

Plan §10 requires recording the exact repository baseline rather than relying on a
historical pass count. The baseline for commit
`47f5abd7fad4e9f0e2c890ef7e762b37e45296e6` is measured on a clean tree and
recorded in `05-verification-ledger.md`. **No agent may quote a pass total from
chat history or from an earlier draft plan as an acceptance criterion.**

---

## D-4 · `workers/integration_outbox.py` has one writer: Agent B

**Raised by:** coordinator, Gate W0 ownership check. **Affects:** plan §6.
**Agents:** B, C.

**Conflict.** Plan §6 gives Agent B `workers/integration_outbox.py` (dispatcher
registration, lines 69-133) and gives Agent C "reconciliation worker logic" — which
lives in `_reconciliation_sweep` **in that same file** (line 166). Gate W0 requires
that no two agents share a writable file.

**Decision.** **Agent B owns the file outright.** Agent C ships reconciliation as a
callable in its own module under the saga package and exposes it as a single entry
point; Agent B wires that call into `_reconciliation_sweep`. Agent C never edits
`workers/integration_outbox.py`.

Rationale: the file is the dispatcher registration site, which B edits for both
topics, and the sweep is a host rather than the logic. One writer, one merge
target, and the reconciliation logic stays unit-testable without the worker.

---

## D-5 · The support-outcome enqueue belongs to Agent C, not Agent B

**Raised by:** coordinator, Gate W0 ownership check. **Affects:** plan §6.
**Agents:** B, C.

**Conflict.** Plan §6 makes Agent B responsible for "workflow-opened and
committed-support-outcome producers", while Agent C owns the Mongo transaction in
`operations/support_events.py`. The support-outcome outbox row **must be written
inside that transaction** (C6) — so the producer and the transaction are the same
edit, in a file only one of them can own.

**Decision.** Split the two producers by where they must live:

| Producer | Owner | File |
|---|---|---|
| Channel B opened → workflow card | **B** | `workflows/return_case_activities.py` |
| Support outcome committed → support card | **C** | `operations/support_events.py`, inside the existing `session.with_transaction(...)` |
| Both dispatchers (delivery) | **B** | `operations/integrations/` + registration |

Agent B freezes the topic strings and idempotency-key formats in C5; Agent C uses
them verbatim. `DurableSupportEventStore.outbox_idempotency_key` already exists
(`support_events.py:266`) and is the pattern to follow.

Rationale: enqueueing outside C's transaction would reopen the dual-write window
the saga exists to close. Delivery stays entirely with B.
