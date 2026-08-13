/*
Forward-only platform migration: RMA-scoped return shipment state (T-15, contract C4).

This migration extends the platform-owned SQL return store only. External
source databases remain read-only.

THE §7 / §10 RECONCILIATION, IN THE SCHEMA
------------------------------------------
`config/returns/production.yaml` declares

    source_resolution:
      shipment_collection: shipmentInfo
      tracking_field: shipmentInfoEventData.trkNum

and that reads as "shipment is an externally-owned read-only source", which
looks like it contradicts the create/update requirement. It does not, and the
block name is the evidence: both keys sit under `source_resolution`, beside
`sales_invoice_collection: salesInv`, `customer_collection:
customerOutboundCDM` and `product_collection: lkpSearchProduct` -- the external
OMC collections the platform resolves orders FROM.

So they describe the **outbound** shipment: how the customer's order travelled
TO them. That shipment is owned by OMC, and nothing here writes it.

A **return** shipment -- how the goods travel BACK, per RMA -- is a different
shipment with a different owner, and the platform owns it. Its authoritative
store already exists and is already RMA-scoped: `dbo.return_tracking`, keyed on
`return_reference`, carrying `tracking_reference`, `carrier_code`,
`tracking_status` and `event_at`. It is written today only by the legacy
`persist_support_result`, and only ever with `'LABEL_CREATED'`.

There is therefore no contradiction to resolve and no new table to invent. The
configuration never claimed ownership of return shipments; it named the source
of outbound ones. This migration adds the fields T-15 requires that
`dbo.return_tracking` lacks, and nothing else.

WHAT IS ADDED, AND WHY EACH
---------------------------
`shipment_details`  T-15 lists it in the update field set; no column carried it.
`row_version`       So a caller can see that a duplicate submit changed nothing,
                    matching `return_requests`/`return_record`'s existing
                    `row_version` convention rather than inventing another.
`updated_at`        `event_at` is when the CARRIER observed the status. When the
                    PLATFORM recorded it is a different fact, and conflating
                    them makes a late-arriving old event indistinguishable from
                    a fresh one. Every other mutable table here has both.

`event_at` stays the ordering authority: it is the status timestamp, so it is
what a stale update is stale with respect to. The existing
`UQ_return_tracking_reference` is the idempotency key -- one row per tracking
number -- and it is why a duplicate update cannot become a second row.
*/

USE [return_platform];
GO

IF COL_LENGTH(N'dbo.return_tracking', N'shipment_details') IS NULL
    ALTER TABLE dbo.return_tracking ADD shipment_details NVARCHAR(1000) NULL;
GO

IF COL_LENGTH(N'dbo.return_tracking', N'row_version') IS NULL
    ALTER TABLE dbo.return_tracking
        ADD row_version BIGINT NOT NULL
        CONSTRAINT DF_return_tracking_row_version DEFAULT (1) WITH VALUES;
GO

IF COL_LENGTH(N'dbo.return_tracking', N'updated_at') IS NULL
    ALTER TABLE dbo.return_tracking
        ADD updated_at DATETIME2(3) NOT NULL
        CONSTRAINT DF_return_tracking_updated_at DEFAULT SYSUTCDATETIME() WITH VALUES;
GO

-- Reads are always "the shipment state of this RMA", so the covering order is
-- by return_reference. `IX_return_tracking_return` already covers
-- (return_reference, event_at DESC); nothing further is needed.
