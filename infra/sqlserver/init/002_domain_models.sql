USE [return_platform];
GO

IF SCHEMA_ID(N'integration') IS NULL EXEC(N'CREATE SCHEMA integration');
GO
IF SCHEMA_ID(N'platform') IS NULL EXEC(N'CREATE SCHEMA platform');
GO

IF OBJECT_ID(N'dbo.return_items', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.return_items (
        return_item_id VARCHAR(64) NOT NULL PRIMARY KEY,
        session_id VARCHAR(36) NOT NULL,
        return_reference VARCHAR(64) NULL,
        order_line_id VARCHAR(128) NOT NULL,
        product_id VARCHAR(128) NOT NULL,
        quantity INT NOT NULL,
        reason_code VARCHAR(64) NOT NULL,
        item_status VARCHAR(32) NOT NULL,
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_items_created_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_return_items_request FOREIGN KEY (session_id) REFERENCES dbo.return_requests(session_id),
        CONSTRAINT CK_return_items_quantity CHECK (quantity > 0)
    );
    CREATE INDEX IX_return_items_session ON dbo.return_items(session_id, created_at DESC);
    CREATE INDEX IX_return_items_reference ON dbo.return_items(return_reference, created_at DESC);
END;
GO

IF OBJECT_ID(N'dbo.return_tracking', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.return_tracking (
        tracking_id VARCHAR(64) NOT NULL PRIMARY KEY,
        return_reference VARCHAR(64) NOT NULL,
        tracking_type VARCHAR(32) NOT NULL,
        tracking_reference VARCHAR(128) NOT NULL,
        carrier_code VARCHAR(32) NULL,
        tracking_status VARCHAR(32) NOT NULL,
        event_at DATETIME2(3) NOT NULL,
        CONSTRAINT UQ_return_tracking_reference UNIQUE (tracking_reference),
        CONSTRAINT CK_return_tracking_type CHECK (tracking_type IN ('PPL','BOL','CUSTOMER_SHIP','NO_LABEL','DIRECT_VENDOR','FIELD_SCRAP'))
    );
    CREATE INDEX IX_return_tracking_return ON dbo.return_tracking(return_reference, event_at DESC);
END;
GO

IF OBJECT_ID(N'integration.return_support_ticket', N'U') IS NULL
BEGIN
    CREATE TABLE integration.return_support_ticket (
        ticket_id VARCHAR(64) NOT NULL PRIMARY KEY,
        session_id VARCHAR(36) NOT NULL,
        request_digest CHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        external_reference VARCHAR(128) NULL,
        clarification_request NVARCHAR(2000) NULL,
        return_reference VARCHAR(64) NULL,
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_support_ticket_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(3) NOT NULL CONSTRAINT DF_support_ticket_updated_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_support_ticket_request FOREIGN KEY (session_id) REFERENCES dbo.return_requests(session_id),
        CONSTRAINT UQ_support_ticket_session UNIQUE (session_id),
        CONSTRAINT UQ_support_ticket_external UNIQUE (external_reference),
        CONSTRAINT CK_support_ticket_status CHECK (status IN ('DRAFT','SUBMITTED','CLARIFICATION_REQUIRED','RETURN_CREATED','REJECTED','CANCELLED','FAILED'))
    );
    CREATE INDEX IX_support_ticket_status ON integration.return_support_ticket(status, updated_at DESC);
END;
GO

IF OBJECT_ID(N'platform.bay_configuration', N'U') IS NULL
BEGIN
    CREATE TABLE platform.bay_configuration (
        bay_id VARCHAR(64) NOT NULL PRIMARY KEY,
        bay_name NVARCHAR(128) NOT NULL,
        warehouse_id VARCHAR(64) NOT NULL,
        branch_id VARCHAR(64) NOT NULL,
        bay_type VARCHAR(32) NOT NULL,
        active BIT NOT NULL,
        priority INT NOT NULL,
        supported_shipping_paths NVARCHAR(1000) NOT NULL,
        supported_product_types NVARCHAR(1000) NOT NULL,
        max_package_count INT NOT NULL,
        overflow_bay_id VARCHAR(64) NULL,
        CONSTRAINT FK_bay_overflow FOREIGN KEY (overflow_bay_id) REFERENCES platform.bay_configuration(bay_id),
        CONSTRAINT CK_bay_priority CHECK (priority >= 0),
        CONSTRAINT CK_bay_capacity CHECK (max_package_count > 0),
        CONSTRAINT CK_bay_type CHECK (bay_type IN ('PPL','BOL','HOLD','CUSTOMER_SHIP','DIRECT_VENDOR','FIELD_SCRAP'))
    );
    CREATE INDEX IX_bay_configuration_lookup ON platform.bay_configuration(warehouse_id, active, priority);
END;
GO

IF OBJECT_ID(N'platform.bay_assignment', N'U') IS NULL
BEGIN
    CREATE TABLE platform.bay_assignment (
        assignment_id VARCHAR(64) NOT NULL PRIMARY KEY,
        return_reference VARCHAR(64) NOT NULL,
        sales_order_number VARCHAR(64) NOT NULL,
        order_line_id VARCHAR(128) NOT NULL,
        item_number VARCHAR(128) NOT NULL,
        package_count INT NOT NULL,
        warehouse_id VARCHAR(64) NOT NULL,
        bay_id VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        confirmed_by_associate_id VARCHAR(128) NULL,
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_bay_assignment_created_at DEFAULT SYSUTCDATETIME(),
        confirmed_at DATETIME2(3) NULL,
        CONSTRAINT FK_bay_assignment_bay FOREIGN KEY (bay_id) REFERENCES platform.bay_configuration(bay_id),
        CONSTRAINT UQ_bay_assignment_return_line UNIQUE (return_reference, order_line_id),
        CONSTRAINT CK_bay_assignment_packages CHECK (package_count > 0),
        CONSTRAINT CK_bay_assignment_status CHECK (status IN ('CREATED','CONFIRMED','RELEASED','HOLD'))
    );
    CREATE INDEX IX_bay_assignment_bay ON platform.bay_assignment(bay_id, status, created_at DESC);
END;
GO

IF OBJECT_ID(N'platform.feedback_recommendation', N'U') IS NULL
BEGIN
    CREATE TABLE platform.feedback_recommendation (
        recommendation_id VARCHAR(64) NOT NULL PRIMARY KEY,
        session_id VARCHAR(36) NOT NULL,
        area VARCHAR(64) NOT NULL,
        recommendation NVARCHAR(2000) NOT NULL,
        evidence_digest CHAR(64) NOT NULL,
        review_status VARCHAR(32) NOT NULL,
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_feedback_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(3) NOT NULL CONSTRAINT DF_feedback_updated_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_feedback_request FOREIGN KEY (session_id) REFERENCES dbo.return_requests(session_id),
        CONSTRAINT UQ_feedback_session_area_digest UNIQUE (session_id, area, evidence_digest),
        CONSTRAINT CK_feedback_status CHECK (review_status IN ('REVIEW_PENDING','APPROVED','APPLIED','ARCHIVED'))
    );
    CREATE INDEX IX_feedback_review_queue ON platform.feedback_recommendation(review_status, created_at DESC);
END;
GO

-- Deterministic sandbox bay master. Re-running is safe.
MERGE platform.bay_configuration AS target
USING (VALUES
    ('BAY-PPL-01','PPL Staging 01','WH-CHENNAI-01','BR-CHENNAI','PPL',1,10,'["PPL"]','["STANDARD","BULKY"]',50,NULL),
    ('BAY-BOL-01','Freight Staging 01','WH-CHENNAI-01','BR-CHENNAI','BOL',1,20,'["BOL"]','["STANDARD","BULKY"]',20,NULL),
    ('BAY-HOLD-01','Return Hold 01','WH-CHENNAI-01','BR-CHENNAI','HOLD',1,30,'["NO_LABEL"]','["STANDARD","BULKY","HAZARDOUS_REVIEW"]',30,NULL),
    ('BAY-CUSTOMER-SHIP-01','Customer Ship 01','WH-CHENNAI-01','BR-CHENNAI','CUSTOMER_SHIP',1,40,'["CUSTOMER_SHIP"]','["STANDARD","BULKY"]',30,NULL),
    ('BAY-DIRECT-VENDOR-01','Direct Vendor 01','WH-CHENNAI-01','BR-CHENNAI','DIRECT_VENDOR',1,50,'["DIRECT_VENDOR"]','["STANDARD","BULKY"]',20,NULL),
    ('BAY-FIELD-SCRAP-01','Field Scrap 01','WH-CHENNAI-01','BR-CHENNAI','FIELD_SCRAP',1,60,'["FIELD_SCRAP"]','["STANDARD","BULKY","HAZARDOUS_REVIEW"]',20,NULL)
) AS source (bay_id,bay_name,warehouse_id,branch_id,bay_type,active,priority,supported_shipping_paths,supported_product_types,max_package_count,overflow_bay_id)
ON target.bay_id=source.bay_id
WHEN MATCHED THEN UPDATE SET
    bay_name=source.bay_name, warehouse_id=source.warehouse_id, branch_id=source.branch_id,
    bay_type=source.bay_type, active=source.active, priority=source.priority,
    supported_shipping_paths=source.supported_shipping_paths,
    supported_product_types=source.supported_product_types, max_package_count=source.max_package_count,
    overflow_bay_id=source.overflow_bay_id
WHEN NOT MATCHED THEN INSERT (
    bay_id,bay_name,warehouse_id,branch_id,bay_type,active,priority,
    supported_shipping_paths,supported_product_types,max_package_count,overflow_bay_id
) VALUES (
    source.bay_id,source.bay_name,source.warehouse_id,source.branch_id,source.bay_type,
    source.active,source.priority,source.supported_shipping_paths,source.supported_product_types,
    source.max_package_count,source.overflow_bay_id
);
GO
