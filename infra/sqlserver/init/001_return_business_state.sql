USE [return_platform];
GO

IF OBJECT_ID(N'dbo.return_requests', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.return_requests (
        session_id VARCHAR(36) NOT NULL PRIMARY KEY,
        correlation_id VARCHAR(64) NOT NULL,
        customer_reference VARCHAR(64) NOT NULL,
        order_reference VARCHAR(64) NOT NULL,
        reason_code VARCHAR(64) NOT NULL,
        eligibility_decision VARCHAR(32) NOT NULL,
        return_reference VARCHAR(64) NULL,
        return_status VARCHAR(32) NOT NULL,
        row_version BIGINT NOT NULL CONSTRAINT DF_return_requests_row_version DEFAULT (1),
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_requests_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_requests_updated_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_return_requests_return_reference UNIQUE (return_reference)
    );
    CREATE INDEX IX_return_requests_order_reference ON dbo.return_requests(order_reference, updated_at DESC);
    CREATE INDEX IX_return_requests_status ON dbo.return_requests(return_status, updated_at DESC);
END;
GO

IF OBJECT_ID(N'dbo.return_fulfillment', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.return_fulfillment (
        session_id VARCHAR(36) NOT NULL PRIMARY KEY,
        fulfillment_reference VARCHAR(64) NULL,
        tracking_reference VARCHAR(64) NULL,
        warehouse_reference VARCHAR(64) NULL,
        bay_reference VARCHAR(64) NULL,
        fulfillment_status VARCHAR(32) NOT NULL,
        row_version BIGINT NOT NULL CONSTRAINT DF_return_fulfillment_row_version DEFAULT (1),
        created_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_fulfillment_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(3) NOT NULL CONSTRAINT DF_return_fulfillment_updated_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_return_fulfillment_request FOREIGN KEY (session_id) REFERENCES dbo.return_requests(session_id),
        CONSTRAINT UQ_return_fulfillment_tracking_reference UNIQUE (tracking_reference)
    );
END;
GO

IF OBJECT_ID(N'dbo.e2e_seed_scenarios', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.e2e_seed_scenarios (
        scenario_id VARCHAR(96) NOT NULL PRIMARY KEY,
        seed_version VARCHAR(64) NOT NULL,
        seed_digest CHAR(64) NOT NULL,
        order_reference VARCHAR(64) NOT NULL,
        customer_reference VARCHAR(64) NOT NULL,
        reason_code VARCHAR(64) NOT NULL,
        expected_decision VARCHAR(32) NOT NULL,
        applied_at DATETIME2(3) NOT NULL,
        CONSTRAINT CK_e2e_seed_scenarios_expected_decision
            CHECK (expected_decision IN ('APPROVE', 'REJECT', 'REVIEW_REQUIRED'))
    );
    CREATE INDEX IX_e2e_seed_scenarios_seed ON dbo.e2e_seed_scenarios(seed_version, seed_digest);
END;
GO
