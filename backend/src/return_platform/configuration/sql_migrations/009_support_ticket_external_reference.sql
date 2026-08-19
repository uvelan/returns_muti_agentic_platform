/*
Forward-only platform migration: only ONE ticket could exist without an
external reference (RMA-01).

This migration extends the platform-owned SQL return store only. External
source databases remain read-only.

THE DEFECT
----------
`002_domain_models.sql` declared

    CONSTRAINT UQ_support_ticket_external UNIQUE (external_reference)

on a column that is NULLable. SQL Server, unlike the ANSI reading most engines
take, treats NULL as a value for UNIQUE purposes: two rows both holding NULL
are duplicates. So the constraint did not say "no two tickets share an external
reference" -- it said that, *and* "at most one ticket in the entire table may
lack one".

WHY IT NEVER SURFACED
---------------------
The only writer was `persist_support_result`, which is reached from the support
path that has already called the external system, so it always had a reference
to store. The first ticket created before an external reference exists -- which
is the ordinary state of a ticket a support associate has just raised -- takes
the single NULL slot, and the second fails with

    Violation of UNIQUE KEY constraint 'UQ_support_ticket_external'.
    The duplicate key value is (<NULL>).

which reads as a duplicate-ticket error and is nothing of the kind.

THE FIX
-------
A filtered unique index expresses the constraint that was actually intended:
uniqueness among the rows that have a value, and no opinion about the rows that
do not. This is the standard SQL Server idiom for a nullable unique column.

The constraint is dropped and the index created in one migration because a
table cannot be left in the intermediate state: dropping without replacing
would permit genuine duplicates, and creating the index first collides with the
constraint's own index.

Existing rows are unaffected. Any deployment already carrying two tickets with
the same non-null external reference could not have reached this point, because
the constraint being dropped forbade exactly that.
*/

IF EXISTS (
    SELECT 1 FROM sys.key_constraints
    WHERE name = N'UQ_support_ticket_external'
      AND parent_object_id = OBJECT_ID(N'integration.return_support_ticket')
)
BEGIN
    ALTER TABLE integration.return_support_ticket
        DROP CONSTRAINT UQ_support_ticket_external;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'UX_support_ticket_external_present'
      AND object_id = OBJECT_ID(N'integration.return_support_ticket')
)
BEGIN
    CREATE UNIQUE INDEX UX_support_ticket_external_present
        ON integration.return_support_ticket(external_reference)
        WHERE external_reference IS NOT NULL;
END;
GO
