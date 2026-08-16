# Return method persistence — specification

**Status:** specification only. No workflow code was written for it; `workflows/**` is owned by
another agent and this document is what the next task executes.

**Closes:** the reason `businessComplete` can never become true for any Copilot case.

---

## 1. The defect, stated precisely

`returnMethod` has **no persistence anywhere in the case aggregate.**

* `ReturnRecordView` (`operations/models.py:232`) declares `returnReference`, `status`,
  `returnLocation`, `trackingReference`, `labelReference`, `shippingInstructionReference`,
  `sourceSystem` — and no method.
* `dbo.return_record` (`sql_migrations/005_case_return_records.sql:69`) mirrors exactly those
  columns, and likewise has none.
* No writer records one as a case fact. `record_support_outcome`
  (`workflows/return_case_activities.py:780`) appends `return_reference`, `tracking_reference`,
  `label_reference` and `return_location` — and stops there.
* The value that does exist lives on `ReturnSessionView.approvedReturnMethod`
  (`operations/models.py:295`), which is the **legacy session** shape. A Copilot case has no
  session, so it can never have one.

The consequence chains all the way to the screen:

```text
record.returnMethod is None
  -> resolve_method_requirements(record) is None          (completion.py)
  -> completionProfileResolved is False                   (sect. 6.4)
  -> awaiting always contains RETURN_METHOD
  -> businessComplete can never become true
  -> the case never reaches COMPLETED
```

The read path is already correct and already waiting. `assembly.py` reads a record-level
`returnMethod` first and falls back to the case facts named in `RETURN_METHOD_FACT_NAMES`
(`approved_return_method`, then `return_method`). With either fact present, an approved
`PREPAID_PARCEL` case carrying RMA + label + tracking projects
`stage=AUTHORIZED_RMA, awaiting=(), businessComplete=True`. **Nothing on the read side needs to
change.** The gap is entirely a missing writer.

---

## 2. Which writer, and at exactly which moment

### The writer: `ReturnCaseActivities.record_support_outcome`

`workflows/return_case_activities.py:780`. Verified against the code, not assumed:

* It is the **only** place an RMA comes into existence on a case. It calls
  `create_return_record` once per `SupportReturnRecord`, then `update_return_record` with that
  record's `trackingReference`, `labelReference`, `returnLocation` and
  `shippingInstructionReference`.
* It already writes the per-record fulfilment facts (`tracking_reference-{record_id}` and
  friends) through `_append_fact_once`, so it already owns the pattern the method would follow.
* It already commits the authoritative SQL row for the record first
  (`_persist_records_to_return_store` → `SQLBusinessStateRepository.persist_case_return_records`),
  inside one transaction covering every RMA of the outcome. A method column joins that
  transaction for free.

### The moment: the same activity call that creates the record

Not earlier and not later, for one reason each:

* **Not earlier.** Before Support answers, the platform has a *recommendation*, not a decision.
  `ReturnWorkflowAgent._recommended_method` (`agents/return_workflow.py`) produces one and reports
  `approved_return_method` as *missing evidence* when it comes out `UNKNOWN` — it is explicitly
  the thing an associate must have confirmed, not something the agent settles. Writing the
  recommendation as the method would make the completion profile resolve on a guess, and
  `UNKNOWN` has no row in the requirement table precisely so that cannot happen.
* **Not later.** The method decides the requirement set. A record that exists for even one poll
  without one reports `RETURN_METHOD` outstanding, and the Copilot renders an RMA it claims to
  be waiting on a method for. Same transaction, same activity, one observable state.

### The three upstream carriers the value has to travel through

Support names the method; nothing between the reply and the record can invent it. Each of these
is a field addition on an existing shape — no new module, no new endpoint:

| Layer | Symbol | File |
| --- | --- | --- |
| HTTP request | `ReturnOutcomeRecord` | `api/return_support.py:314` |
| Signal envelope | `support_return_record(...)` | `operations/support_events.py:153` |
| Workflow dataclass | `SupportReturnRecord` | `workflows/return_case_workflow.py:279` |

The envelope helper's docstring already states that its keys are a name-matched contract with the
workflow dataclass, and `test_support_event_delivery.py::test_the_signal_envelope_fits_the_workflow_dataclass`
fails if the two drift — so the addition is guarded on both ends by a test that already exists.

**Ordering note for the rollout.** Temporal's converter ignores keys a dataclass has no field
for. Adding `return_method` to `support_return_record` **before** adding it to
`SupportReturnRecord` is therefore safe in exactly the way `support_event_id` already is: the
in-flight signals deserialize, the field is simply not read yet. Adding it to the dataclass first
is also safe. Neither ordering needs a coordinated deploy.

**Validation belongs at the edge.** `ReturnOutcomeRecord` should accept the method as a plain
constrained string and check it against `return_policy.normalized_return_methods` from the active
configuration — never against a closed enum literal. The catalogue is operator-owned through the
Control Centre, and `return_creation_policy.py:77` already validates `shippingPathExpectation`
that way. `ReturnRecordProjection.returnMethod` is deliberately a plain string for the same
reason (see its docstring): a method an operator added must still *render*, and an unmapped one
leaves the profile unresolved rather than making the case unreadable.

---

## 3. Column, fact, or both — and why

**Recommendation: a column on `ReturnRecordView`, plus a per-record fact. Never a single
case-level fact.**

### The column is the home

`returnMethod: str | None = None` on `ReturnRecordView` (`operations/models.py:232`), and
`return_method VARCHAR(64) NULL` on `dbo.return_record` in a new forward-only migration, carried
on `ReturnRecordWrite` (`operations/sql_business_state.py:81`).

The decisive argument is the one the task names: **one case may hold several RMAs with different
methods.** That is not hypothetical in this codebase — it is the documented reason the record
exists at all:

> `ReturnRecordView`: "a multi-item return can produce several RMAs with different labels and
> different return locations, and putting these on the case (one each) or on the item (one per
> item) can express neither."

> `SupportReturnRecord`: "one reply can create several RMAs with different labels going to
> different places, and flattening them would be the very thing the case model was changed to
> stop."

Completion is already evaluated **per record** — `resolve_completion` maps
`resolve_method_requirements` over `case.records()` and a case is complete only when every record
is. A single case-level value would be read as the method of every RMA on the case, which is the
same cross-attribution `returnLocation` was made per-record to prevent, and it would silently
complete a `CUSTOMER_KEEP` record against a `PREPAID_PARCEL` requirement set (or hang a
`NO_PHYSICAL_RETURN` one forever waiting for a label).

The column also puts the method beside `labelReference` and `returnLocation`, which are the other
two per-record fulfilment facts, in the same document and the same authoritative SQL row.
`assembly.py` reads `record.get("returnMethod")` **first**, before any fallback, for exactly this
reason: "a case-level value applied over a record that disagreed would be the cross-attribution
the multi-RMA shape exists to prevent."

### The fact is the audit trail, and it is per record

Also append `return_method` through `_append_fact_once` with `fact_id=f"return_method-{record_id}"`
— the id shape the sibling facts already use, which is what makes the write idempotent under
replay. The fact carries the provenance the column cannot: `agentId`, `channel=CHANNEL_B`,
`acquisitionMethod=OBSERVED`, `sourceSystem=RETURN_SUPPORT`, `observedAt`. Support deciding the
method is an *observation* on the case, and the fact log is where the case records who said what
and when. It is also what reaches Channel A: the agent's turn context is built from the fact
projection, which is how the method becomes visible in the associate's original conversation
without a new chat or a client-side join.

### Why "both" is not two sources of truth

The column is authoritative and the fact is provenance; the projection reads the column first and
never merges them. That is the arrangement `label_reference` and `tracking_reference` already
have and it has not produced a conflict, because one writer writes both in one activity.

### What `RETURN_METHOD_FACT_NAMES` becomes

Keep it, and note what it is for. `latest_case_facts` is a **latest-per-name** projection, so a
case with two RMAs writing two `return_method` facts keeps only the newer one — the fallback is
therefore correct **only for a single-record case**, and it exists to read cases written before
the column landed. Once the column is populated it is never consulted for a record that has one.
Do not extend the fallback to try to disambiguate per record; that is what the column is.

Do **not** rename the fact per record (`return_method-RR-1` as a *name*). `latest_case_facts`
keys on `factName`, and a per-record name would make the projection grow one entry per RMA and
break `assembly._return_method_fact`'s lookup. Use the shared name and the per-record `factId`,
exactly as `tracking_reference` does today.

### One name, not two, on the write side

`assembly.RETURN_METHOD_FACT_NAMES` accepts `approved_return_method` then `return_method`. The
writer should emit **`return_method`**, because `operations/warehouse/case_placement.py:77`
already reads a case fact of that name (`FACT_RETURN_METHOD`) to choose a bay. Writing the other
spelling would give the Copilot a resolved method while bay placement still normalized `None`.
`approved_return_method` stays in the tuple as the higher-authority read for anything that
migrates the legacy session value across.

---

## 4. Satisfying the sect. 6.5 revision invariant

> **Invariant.** Any write that can change the `CaseDetail` projection must, in the same
> transaction, bump `case.revision` and set `case.updatedAt`.

Writing the return method changes `awaiting` and can flip `businessComplete` from false to true.
It is squarely inside the invariant, and it is **not satisfied today** — this is a pre-existing
gap the new writer inherits rather than one it creates. `case_repository.py:425` records it
plainly:

> "Nothing bumps `cases.version` when a child collection is written today."

`create_return_record` and `update_return_record` write `operational_return_records` and bump only
that document's own `version`. `append_case_fact` inserts into `case_facts` and bumps nothing.
So a client polling `GET /api/cases/{caseId}` sees an unchanged `revision` over a projection whose
content has changed, and the mitigation in place is only a read *ordering* one — the case document
is read before its children, so the reported revision can be stale-old but never stale-new.

The writer must therefore, in the same act that records the method:

1. **Bump `cases.version` and set `cases.updatedAt`.** Through `update_case`, which already does
   both in one `find_one_and_update` with an `expected_version` filter, so a lost update is a
   `ConcurrencyConflictError` rather than silence. `backfill_case` already demonstrates the
   pattern for a projection-changing write.
2. **Make the bump atomic with the record write.** Two Mongo writes are not one transaction. The
   plan permits exactly two shapes and forbids a third:
   * one transaction (a Mongo session spanning `operational_return_records` and `cases`), or
   * a projection/outbox writer with deterministic ordering.
   * **Never a best-effort second write.** A bump that can fail after the record commits produces
     precisely the case the invariant exists to prevent: complete on the server, incomplete on
     every client that trusts the revision.
3. **Stay idempotent under replay.** The activity is retried and the workflow may
   `continue_as_new`. The record write is already idempotent (workflow-supplied ids plus the
   unique index) and the fact write is already idempotent (`_append_fact_once` on a derived
   `fact_id`). The revision bump must not be: a replay that finds nothing to change must **not**
   bump, or every retry manufactures a revision change on an unchanged projection and the client
   re-fetches forever. Bump only when the record write actually modified a document.
4. **Order it after the authoritative SQL commit.** `record_support_outcome` already commits SQL
   first and only then updates the platform case (T-14). The method is part of the same outcome
   and must not reverse that order — a Mongo case that reported a method the SQL return store
   never received is the divergence the ordering exists to close.
5. **Cover it with the concurrency test sect. 6.5 requires.** Two writers on different child
   collections must produce two distinct, monotonically increasing revisions with no lost update.
   A method writer and a tracking writer racing on one case is the concrete instance of that test.

---

## 5. Definition of done for the executing task

* `ReturnOutcomeRecord`, `support_return_record`, `SupportReturnRecord`, `ReturnRecordWrite`,
  `ReturnRecordView` and `dbo.return_record` each carry the method.
* `record_support_outcome` writes it to the record and appends one `return_method` fact per
  record, and bumps `cases.version` / `cases.updatedAt` atomically with the record write.
* The method is validated against `return_policy.normalized_return_methods` at the API edge, not
  against a closed enum.
* A test asserts an approved `PREPAID_PARCEL` case with RMA + label + tracking reaches
  `stage=AUTHORIZED_RMA, awaiting=(), businessComplete=True` **through the writer**, not by
  hand-placing a fact.
* A test asserts one case with two RMAs of different methods evaluates each against its own
  requirement row — e.g. a `CUSTOMER_KEEP` record complete while a `PREPAID_PARCEL` record still
  awaits `TRACKING`.
* A test asserts a replayed activity produces no second revision bump.
