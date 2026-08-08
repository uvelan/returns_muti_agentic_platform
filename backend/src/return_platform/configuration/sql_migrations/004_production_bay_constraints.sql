/*
Stage 4L forward-only platform bay and recommendation governance migration.

This migration applies only to the platform-owned SQL database. It does not
modify Ferguson production OMC schemas.
*/

SET XACT_ABORT ON;
GO

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'platform.bay_configuration')
      AND name = N'CK_bay_type'
)
    ALTER TABLE platform.bay_configuration DROP CONSTRAINT CK_bay_type;
GO

ALTER TABLE platform.bay_configuration WITH CHECK
ADD CONSTRAINT CK_bay_type CHECK (
    bay_type IN (
        'PPL','BOL','UPS','LTL','HOLD','QUARANTINE','HAZARDOUS',
        'OVERSIZED','CUSTOMER_SHIP','DIRECT_VENDOR','FIELD_SCRAP'
    )
);
GO

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'platform.bay_assignment')
      AND name = N'CK_bay_assignment_status'
)
    ALTER TABLE platform.bay_assignment DROP CONSTRAINT CK_bay_assignment_status;
GO

ALTER TABLE platform.bay_assignment WITH CHECK
ADD CONSTRAINT CK_bay_assignment_status CHECK (
    status IN (
        'CREATED','CONFIRMED','RESERVED','ASSIGNED','STAGED',
        'RELEASED','EXPIRED','HOLD','CANCELLED'
    )
);
GO

IF COL_LENGTH('platform.feedback_recommendation', 'feedback_id') IS NULL
BEGIN
    ALTER TABLE platform.feedback_recommendation ADD
        feedback_id VARCHAR(64) NULL,
        recommendation_type VARCHAR(64) NULL,
        severity VARCHAR(32) NULL,
        reviewed_by VARCHAR(128) NULL,
        reviewed_at DATETIME2(3) NULL,
        applied_at DATETIME2(3) NULL,
        row_version BIGINT NOT NULL
            CONSTRAINT DF_feedback_recommendation_row_version DEFAULT 0;
END;
GO
