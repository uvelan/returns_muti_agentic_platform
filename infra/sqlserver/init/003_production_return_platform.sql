/*
Stage 4L forward-only platform schema migration.

This migration extends the local/sandbox return-platform database only.
It does not modify production OMC schemas. Production OMC writes must use
an approved command API, stored procedure, integration service, or queue.
*/

SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.return_requests', 'order_source') IS NULL
BEGIN
    ALTER TABLE dbo.return_requests ADD
        order_source VARCHAR(32) NULL,
        source_order_number VARCHAR(64) NULL,
        source_web_order_number VARCHAR(64) NULL,
        trilogie_order_number VARCHAR(64) NULL,
        source_system_code VARCHAR(32) NULL,
        source_erp VARCHAR(32) NULL,
        return_authorization_type VARCHAR(32) NULL,
        product_presence VARCHAR(64) NULL,
        fulfillment_path VARCHAR(64) NULL,
        refund_status VARCHAR(32) NULL,
        physical_return_status VARCHAR(64) NULL,
        approved_at DATETIME2(3) NULL,
        completed_at DATETIME2(3) NULL;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.return_requests')
      AND name = N'IX_return_requests_trilogie_order'
)
    CREATE INDEX IX_return_requests_trilogie_order
        ON dbo.return_requests(trilogie_order_number, created_at DESC);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.return_requests')
      AND name = N'IX_return_requests_web_order'
)
    CREATE INDEX IX_return_requests_web_order
        ON dbo.return_requests(source_web_order_number, created_at DESC);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.return_requests')
      AND name = N'IX_return_requests_physical_status'
)
    CREATE INDEX IX_return_requests_physical_status
        ON dbo.return_requests(physical_return_status, updated_at DESC);
GO

IF COL_LENGTH('dbo.return_items', 'requested_quantity') IS NULL
BEGIN
    ALTER TABLE dbo.return_items ADD
        sku VARCHAR(128) NULL,
        model_number VARCHAR(128) NULL,
        master_product_id VARCHAR(128) NULL,
        product_description NVARCHAR(500) NULL,
        color_finish NVARCHAR(128) NULL,
        requested_quantity DECIMAL(20,6) NULL,
        approved_quantity DECIMAL(20,6) NULL,
        reason_text NVARCHAR(2000) NULL,
        condition_code VARCHAR(64) NULL,
        disposition VARCHAR(64) NULL,
        row_version_v2 BIGINT NOT NULL CONSTRAINT DF_return_items_row_version_v2 DEFAULT 0,
        updated_at_v2 DATETIME2(3) NOT NULL CONSTRAINT DF_return_items_updated_at_v2 DEFAULT SYSUTCDATETIME();

    UPDATE dbo.return_items
       SET requested_quantity = quantity,
           approved_quantity = CASE WHEN item_status IN ('APPROVED','RETURN_CREATED','COMPLETED')
                                    THEN quantity ELSE NULL END;
END;
GO

IF COL_LENGTH('platform.bay_configuration', 'hazardous_allowed') IS NULL
BEGIN
    ALTER TABLE platform.bay_configuration ADD
        hazardous_allowed BIT NOT NULL CONSTRAINT DF_bay_hazardous_allowed DEFAULT 0,
        oversized_allowed BIT NOT NULL CONSTRAINT DF_bay_oversized_allowed DEFAULT 0,
        max_handling_unit_count INT NULL,
        max_pallet_count INT NULL,
        capacity_unit VARCHAR(32) NOT NULL CONSTRAINT DF_bay_capacity_unit DEFAULT 'HANDLING_UNIT',
        row_version_v2 BIGINT NOT NULL CONSTRAINT DF_bay_configuration_row_version_v2 DEFAULT 0,
        updated_at DATETIME2(3) NOT NULL CONSTRAINT DF_bay_configuration_updated_at DEFAULT SYSUTCDATETIME();

    UPDATE platform.bay_configuration
       SET max_handling_unit_count = max_package_count,
           max_pallet_count = max_package_count
     WHERE max_handling_unit_count IS NULL OR max_pallet_count IS NULL;
END;
GO

IF OBJECT_ID(N'platform.bay_reservation', N'U') IS NULL
BEGIN
    CREATE TABLE platform.bay_reservation (
        reservation_id VARCHAR(64) NOT NULL PRIMARY KEY,
        return_reference VARCHAR(64) NOT NULL,
        session_id VARCHAR(36) NOT NULL,
        handling_unit_id VARCHAR(128) NOT NULL,
        warehouse_id VARCHAR(64) NOT NULL,
        bay_id VARCHAR(64) NOT NULL,
        reserved_capacity INT NOT NULL,
        status VARCHAR(32) NOT NULL,
        reserved_at DATETIME2(3) NOT NULL CONSTRAINT DF_bay_reservation_reserved_at DEFAULT SYSUTCDATETIME(),
        expires_at DATETIME2(3) NOT NULL,
        released_at DATETIME2(3) NULL,
        row_version BIGINT NOT NULL CONSTRAINT DF_bay_reservation_row_version DEFAULT 0,
        CONSTRAINT FK_bay_reservation_bay
            FOREIGN KEY (bay_id) REFERENCES platform.bay_configuration(bay_id),
        CONSTRAINT UQ_bay_reservation_return_unit
            UNIQUE (return_reference, handling_unit_id),
        CONSTRAINT CK_bay_reservation_capacity CHECK (reserved_capacity > 0),
        CONSTRAINT CK_bay_reservation_status CHECK (
            status IN ('RESERVED','ASSIGNED','RELEASED','EXPIRED','CANCELLED')
        )
    );
    CREATE INDEX IX_bay_reservation_expiry
        ON platform.bay_reservation(warehouse_id, status, expires_at);
    CREATE INDEX IX_bay_reservation_bay
        ON platform.bay_reservation(bay_id, status);
END;
GO

IF COL_LENGTH('platform.bay_assignment', 'handling_unit_id') IS NULL
BEGIN
    ALTER TABLE platform.bay_assignment ADD
        reservation_id VARCHAR(64) NULL,
        handling_unit_id VARCHAR(128) NULL,
        physical_receipt_confirmed BIT NOT NULL
            CONSTRAINT DF_bay_assignment_receipt DEFAULT 0,
        assignment_reason VARCHAR(64) NULL,
        staged_at DATETIME2(3) NULL,
        released_at DATETIME2(3) NULL,
        row_version_v2 BIGINT NOT NULL
            CONSTRAINT DF_bay_assignment_row_version_v2 DEFAULT 0,
        updated_at DATETIME2(3) NOT NULL
            CONSTRAINT DF_bay_assignment_updated_at DEFAULT SYSUTCDATETIME();

    UPDATE platform.bay_assignment
       SET handling_unit_id = CONCAT(return_reference, ':', order_line_id)
     WHERE handling_unit_id IS NULL;

    ALTER TABLE platform.bay_assignment
        ALTER COLUMN handling_unit_id VARCHAR(128) NOT NULL;

    IF EXISTS (
        SELECT 1 FROM sys.key_constraints
        WHERE parent_object_id = OBJECT_ID(N'platform.bay_assignment')
          AND name = N'UQ_bay_assignment_return_line'
    )
        ALTER TABLE platform.bay_assignment
            DROP CONSTRAINT UQ_bay_assignment_return_line;

    ALTER TABLE platform.bay_assignment
        ADD CONSTRAINT UQ_bay_assignment_return_handling_unit
            UNIQUE (return_reference, handling_unit_id);

    ALTER TABLE platform.bay_assignment
        ADD CONSTRAINT FK_bay_assignment_reservation
            FOREIGN KEY (reservation_id)
            REFERENCES platform.bay_reservation(reservation_id);
END;
GO

IF OBJECT_ID(N'platform.return_policy_version', N'U') IS NULL
BEGIN
    CREATE TABLE platform.return_policy_version (
        policy_version VARCHAR(128) NOT NULL PRIMARY KEY,
        configuration_digest CHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        activated_at DATETIME2(3) NOT NULL,
        retired_at DATETIME2(3) NULL,
        created_by VARCHAR(128) NOT NULL,
        CONSTRAINT CK_return_policy_version_status
            CHECK (status IN ('DRAFT','ACTIVE','RETIRED'))
    );
END;
GO
