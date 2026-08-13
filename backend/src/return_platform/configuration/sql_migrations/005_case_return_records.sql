/*
Forward-only platform migration: the case-scoped RMA model (T-14, contract C3).

This migration extends the platform-owned SQL return store only. External
source databases remain read-only.

WHY THIS EXISTS
---------------
The store this platform already owned could not represent the model the
platform already runs. Every existing return table is keyed on a *session*:

    dbo.return_requests.session_id          PRIMARY KEY, one return_reference
    dbo.return_fulfillment.session_id       PRIMARY KEY
    dbo.return_items.session_id             FOREIGN KEY -> return_requests
    integration.return_support_ticket       UNIQUE (session_id)

So one row is one session is one RMA, and there is no case at all. Support,
however, may answer a single case with several RMAs -- `SupportReturnRecord` is
a tuple precisely so it can, `api/cases.py` nests items inside each
`CaseReturnRecord`, and the case workflow creates one return record per RMA.
That model lived only in MongoDB. There was no shape in SQL to write it into,
which is why `persist_support_result` writes a single flattened return and why
the canonical case path wrote no SQL at all.

These three tables add that shape to the same store, rather than adding a
second store. Nothing here replaces the session tables; they keep serving the
legacy session path until it is retired.

WHAT IS LOAD-BEARING
--------------------
Label, tracking, return location and shipping instruction live on
`dbo.return_record`, never on `dbo.return_case`. That is what makes
"RMA-A's label cannot attach to RMA-B" a property of the schema instead of a
property of whichever code path happens to write it.

`UQ_return_record_item_case_line` is the no-cross-contamination rule, and it is
not invented here: `return_case_activities.record_support_outcome` already
assigns an order line to a record only `if not item.get("returnRecordId")` --
an item belongs to at most one return record. This states that in the store, so
a concurrent or replayed write cannot produce two RMAs claiming one line.

Every write path keys on ids the *workflow* supplies, so replay re-writes the
same rows. The unique constraints are what make a duplicate replay a no-op
rather than a second RMA.
*/

USE [return_platform];
GO

IF OBJECT_ID(N'dbo.return_case', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.return_case (
        case_id VARCHAR(64) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        principal_id VARCHAR(128) NOT NULL,
        order_reference VARCHAR(64) NULL,
        case_status VARCHAR(32) NOT NULL,
        row_version BIGINT NOT NULL CONSTRAINT DF_return_case_row_version DEFAULT (1),
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_case_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_case_updated_at DEFAULT SYSUTCDATETIME()
    );
    CREATE INDEX IX_return_case_order ON dbo.return_case(order_reference, updated_at DESC);
    CREATE INDEX IX_return_case_owner ON dbo.return_case(tenant_id, principal_id, updated_at DESC);
END;
GO

IF OBJECT_ID(N'dbo.return_record', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.return_record (
        return_record_id VARCHAR(64) NOT NULL PRIMARY KEY,
        case_id VARCHAR(64) NOT NULL,
        return_reference VARCHAR(64) NOT NULL,
        -- Per-record fulfilment identity. Never promoted to the case.
        label_reference VARCHAR(128) NULL,
        tracking_reference VARCHAR(128) NULL,
        return_location VARCHAR(128) NULL,
        shipping_instruction_reference VARCHAR(128) NULL,
        record_status VARCHAR(32) NOT NULL,
        source_system VARCHAR(64) NOT NULL,
        row_version BIGINT NOT NULL CONSTRAINT DF_return_record_row_version DEFAULT (1),
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_record_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_record_updated_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_return_record_case FOREIGN KEY (case_id) REFERENCES dbo.return_case(case_id),
        CONSTRAINT UQ_return_record_reference UNIQUE (return_reference),
        CONSTRAINT CK_return_record_status CHECK (record_status IN ('ISSUED','CANCELLED','CLOSED'))
    );
    CREATE INDEX IX_return_record_case ON dbo.return_record(case_id, created_at DESC);
END;
GO

IF OBJECT_ID(N'dbo.return_record_item', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.return_record_item (
        return_item_id VARCHAR(64) NOT NULL PRIMARY KEY,
        return_record_id VARCHAR(64) NOT NULL,
        case_id VARCHAR(64) NOT NULL,
        order_line_id VARCHAR(128) NOT NULL,
        product_id VARCHAR(128) NULL,
        quantity INT NOT NULL,
        reason_code VARCHAR(64) NULL,
        item_status VARCHAR(32) NOT NULL,
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_record_item_created_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_return_record_item_record FOREIGN KEY (return_record_id) REFERENCES dbo.return_record(return_record_id),
        CONSTRAINT FK_return_record_item_case FOREIGN KEY (case_id) REFERENCES dbo.return_case(case_id),
        -- One order line belongs to at most one return record in a case.
        CONSTRAINT UQ_return_record_item_case_line UNIQUE (case_id, order_line_id),
        CONSTRAINT CK_return_record_item_quantity CHECK (quantity > 0)
    );
    CREATE INDEX IX_return_record_item_record ON dbo.return_record_item(return_record_id, created_at DESC);
END;
GO
