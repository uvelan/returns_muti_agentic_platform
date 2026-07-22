"""Canonical domain contracts for the Return Platform."""

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    IdentityQuality,
    NonBlankText,
    Sha256Digest,
    SourceProvenance,
    UtcDateTime,
    VersionReference,
)
from return_platform.canonical.bay import AssignmentEvidence, Bay, BayAssignment
from return_platform.canonical.customer import (
    Address,
    ContactPoint,
    Customer,
    CustomerAccount,
)
from return_platform.canonical.operations import (
    AgentDecision,
    AuditEvent,
    ConfigurationVersionBinding,
    ContextSnapshot,
    GraphProjectionEvidence,
    GraphProjectionStatus,
    GraphSyncRun,
    GraphSyncSafeError,
    GraphValidationResult,
    ReturnSession,
    WorkflowStage,
)
from return_platform.canonical.order import OrderLine, SalesOrder
from return_platform.canonical.product import Product
from return_platform.canonical.return_models import (
    FreightShipment,
    Return,
    ReturnItem,
    ReturnVersion,
)
from return_platform.canonical.shipment import (
    CarrierTrackingQuality,
    CarrierTrackingReference,
    Shipment,
    ShipmentItem,
    TrackingEvent,
)
from return_platform.canonical.warehouse import Warehouse, WarehouseProduct

__all__ = [
    "Address",
    "AgentDecision",
    "AssignmentEvidence",
    "AuditEvent",
    "Bay",
    "BayAssignment",
    "CanonicalBaseModel",
    "CanonicalIdentifier",
    "CarrierTrackingQuality",
    "CarrierTrackingReference",
    "ConfigurationVersionBinding",
    "ContactPoint",
    "ContextSnapshot",
    "Customer",
    "CustomerAccount",
    "FreightShipment",
    "GraphProjectionEvidence",
    "GraphProjectionStatus",
    "GraphSyncRun",
    "GraphSyncSafeError",
    "GraphValidationResult",
    "IdentityQuality",
    "NonBlankText",
    "OrderLine",
    "Product",
    "Return",
    "ReturnItem",
    "ReturnSession",
    "ReturnVersion",
    "SalesOrder",
    "Sha256Digest",
    "Shipment",
    "ShipmentItem",
    "SourceProvenance",
    "TrackingEvent",
    "UtcDateTime",
    "VersionReference",
    "Warehouse",
    "WarehouseProduct",
    "WorkflowStage",
]
