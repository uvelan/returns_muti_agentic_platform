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
