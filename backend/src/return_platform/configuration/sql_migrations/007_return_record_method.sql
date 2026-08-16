/*
Forward-only platform migration: the return method on the RMA (D23, T-14).

This migration extends the platform-owned SQL return store only. External
source databases remain read-only.

WHY THIS EXISTS
---------------
`005_case_return_records.sql` gave `dbo.return_record` the per-record fulfilment
identity -- label, tracking, return location, shipping instruction -- and no
method. The method is what decides the requirement set:

    PREPAID_PARCEL     -> RMA, LABEL, TRACKING
    BRANCH_LTL         -> RMA, BOL, PICKUP
    CUSTOMER_KEEP      -> RMA
    NO_PHYSICAL_RETURN -> RMA

so a record without one has no resolvable completion profile, `awaiting`
permanently contains `RETURN_METHOD`, and `businessComplete` cannot become true
for any return. Phase 4 landed the MongoDB write and the per-record
`return_method` fact but deliberately omitted the SQL column, because sending a
column the schema does not have fails every live write. This is that column.

WHY IT IS ON THE RECORD AND NOT THE CASE
----------------------------------------
The same rule 005 states for label, tracking and return location, and for the
same reason: one case may hold several RMAs with different methods. Completion
is evaluated per record -- `resolve_completion` maps the requirement lookup over
`case.records()` -- so a case-level column would be read as the method of every
RMA on the case. It would silently complete a `CUSTOMER_KEEP` record against a
`PREPAID_PARCEL` requirement set, and hang a `NO_PHYSICAL_RETURN` one forever
waiting for a label it will never have. Putting it beside `label_reference` is
what makes "RMA-A's method cannot attach to RMA-B" a property of the schema.

WHY THERE IS NO CHECK CONSTRAINT
--------------------------------
The vocabulary is `return_policy.normalized_return_methods` in the active
configuration, and it is operator-owned through the Control Centre. A CHECK here
would refuse a method an operator added through a perfectly valid release --
the same defect `return_creation_policy.py` removed from
`shippingPathExpectation`'s pydantic `pattern=` (CFG-03). Validation lives at
the request boundary, resolved from the running snapshot, so an unconfigured
method is a 422 the caller can read rather than a constraint violation
surfacing as a 500 from inside a workflow activity.

NULL is legitimate and stays legitimate: it means Support has not said yet, and
a record in that state is exactly the one reporting `RETURN_METHOD` outstanding.

VARCHAR(64) matches `dbo.return_record`'s other reference columns and
`ReturnOutcomeRecord.returnMethod`'s `max_length`, so a value the API accepts is
a value this column can hold.
*/

USE [return_platform];
GO

IF COL_LENGTH(N'dbo.return_record', N'return_method') IS NULL
    ALTER TABLE dbo.return_record ADD return_method VARCHAR(64) NULL;
GO
