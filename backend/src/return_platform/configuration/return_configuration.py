"""Validated configuration for the production Ferguson return flow."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentConfiguration(StrictConfigModel):
    name: NonBlank
    version: NonBlank
    enabled: bool
    ai_assisted: bool
    human_confirmation_required: bool
    capabilities: tuple[NonBlank, ...] = Field(min_length=1)


class DiscoveryConfiguration(StrictConfigModel):
    web_order_pattern: NonBlank
    ambiguity_gap_millionths: int = Field(ge=0, le=1_000_000)
    auto_confirmation_allowed: bool
    anchor_weights: dict[NonBlank, int]
    conflict_penalty_millionths: int = Field(ge=0, le=1_000_000)
    strong_anchors: tuple[NonBlank, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_weights(self) -> DiscoveryConfiguration:
        if any(not 0 <= weight <= 1_000_000 for weight in self.anchor_weights.values()):
            raise ValueError("discovery anchor weights must be 0..1000000")
        return self


class SourceResolutionConfiguration(StrictConfigModel):
    sales_invoice_collection: NonBlank
    customer_collection: NonBlank
    shipment_collection: NonBlank
    product_collection: NonBlank
    order_number_paths: tuple[NonBlank, ...] = Field(min_length=1)
    web_order_paths: tuple[NonBlank, ...] = Field(min_length=1)
    trilogie_order_paths: tuple[NonBlank, ...] = Field(min_length=1)
    customer_id_paths: tuple[NonBlank, ...] = Field(min_length=1)
    customer_name_paths: tuple[NonBlank, ...] = Field(min_length=1)
    line_id_paths: tuple[NonBlank, ...] = Field(min_length=1)
    product_id_paths: tuple[NonBlank, ...] = Field(min_length=1)
    sku_paths: tuple[NonBlank, ...] = Field(min_length=1)
    product_description_paths: tuple[NonBlank, ...] = Field(min_length=1)
    shipped_quantity_paths: tuple[NonBlank, ...] = Field(min_length=1)
    phone_field: NonBlank
    email_field: NonBlank
    customer_master_id_field: NonBlank
    tracking_field: NonBlank
    tracking_order_field: NonBlank


class SmartQuestion(StrictConfigModel):
    field: NonBlank
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    priority: int = Field(ge=0, le=10_000)
    customer_answerable: bool


class SmartQuestionConfiguration(StrictConfigModel):
    max_questions_per_turn: int = Field(ge=1, le=5)
    fields: tuple[SmartQuestion, ...] = Field(min_length=1)


class BranchStagingConfiguration(StrictConfigModel):
    require_return_number_tag: bool
    allow_manufacturer_box_marking: bool
    allow_branch_inventory_addition: bool


class ReturnPolicyConfiguration(StrictConfigModel):
    photo_required_reason_codes: tuple[NonBlank, ...]
    supported_product_presence: tuple[NonBlank, ...] = Field(min_length=1)
    normalized_return_methods: tuple[NonBlank, ...] = Field(min_length=1)
    rga_required_product_resolutions: tuple[NonBlank, ...]
    heavy_pickup_required_fields: tuple[NonBlank, ...] = Field(min_length=1)
    branch_staging: BranchStagingConfiguration


class WorkflowConfiguration(StrictConfigModel):
    version: NonBlank
    stages: tuple[NonBlank, ...] = Field(min_length=2)
    sla_minutes: dict[NonBlank, int]
    completion_dimensions: tuple[NonBlank, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_workflow(self) -> WorkflowConfiguration:
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("workflow stages must be unique")
        if any(value <= 0 for value in self.sla_minutes.values()):
            raise ValueError("workflow SLAs must be positive")
        return self


class SupportConfiguration(StrictConfigModel):
    authority_mode: NonBlank
    external_mirror_enabled: bool
    default_priority: NonBlank
    queues: tuple[NonBlank, ...] = Field(min_length=1)
    external_ticket_outbox_topic: NonBlank


class OmcConfiguration(StrictConfigModel):
    v2_customer_return_table: NonBlank
    v1_customer_return_table: NonBlank
    customer_return_display: dict[NonBlank, NonBlank]
    normalized_statuses: dict[NonBlank, NonBlank]
    tendered_is_pickup: bool
    license_plate_implies_receipt: bool
    rga_is_customer_return: bool


class BayConfiguration(StrictConfigModel):
    authority_mode: NonBlank
    require_physical_receipt: bool
    allow_prearrival_reservation: bool
    eligible_statuses: tuple[NonBlank, ...] = Field(min_length=1)


class IntegrationTopicConfiguration(StrictConfigModel):
    enabled: bool
    topic: NonBlank
    authority: NonBlank
    ai_may_fabricate_success: bool = False


class IntegrationConfiguration(StrictConfigModel):
    omc_return_create: IntegrationTopicConfiguration
    external_support_mirror: IntegrationTopicConfiguration
    carrier_booking: IntegrationTopicConfiguration
    customer_notification: IntegrationTopicConfiguration


class ExtensionConfiguration(StrictConfigModel):
    document_artifact_metadata: bool = True
    ocr_processing: bool = False
    image_processing: bool = False
    ncr_workflow: bool = False
    vendor_recovery_workflow: bool = True

    @model_validator(mode="after")
    def validate_processing_dependencies(self) -> ExtensionConfiguration:
        if (self.ocr_processing or self.image_processing) and not self.document_artifact_metadata:
            raise ValueError("OCR and image processing require document artifact metadata")
        return self


class ReturnPlatformConfiguration(StrictConfigModel):
    schema_version: NonBlank
    assumption_set_version: NonBlank
    agents: dict[NonBlank, AgentConfiguration]
    discovery: DiscoveryConfiguration
    source_resolution: SourceResolutionConfiguration
    smart_questions: SmartQuestionConfiguration
    return_policy: ReturnPolicyConfiguration
    workflow: WorkflowConfiguration
    support: SupportConfiguration
    omc: OmcConfiguration
    bay: BayConfiguration
    integrations: IntegrationConfiguration
    extensions: ExtensionConfiguration

    @model_validator(mode="after")
    def validate_required_agents(self) -> ReturnPlatformConfiguration:
        required = {
            "order_discovery",
            "return_workflow",
            "return_fulfillment",
            "bay_assignment",
            "feedback_learning",
        }
        missing = sorted(required - set(self.agents))
        if missing:
            raise ValueError(f"missing required agent configurations: {', '.join(missing)}")
        if self.discovery.auto_confirmation_allowed:
            raise ValueError("production discovery cannot allow automatic confirmation")
        if self.omc.tendered_is_pickup:
            raise ValueError("OMC tendered state cannot be treated as physical pickup")
        if self.omc.rga_is_customer_return:
            raise ValueError("RGA cannot be configured as the customer return identity")
        if self.return_policy.branch_staging.allow_manufacturer_box_marking:
            raise ValueError("manufacturer box marking must remain disabled")
        if self.return_policy.branch_staging.allow_branch_inventory_addition:
            raise ValueError("branch inventory addition must remain disabled")
        configured_integrations = (
            self.integrations.omc_return_create,
            self.integrations.external_support_mirror,
            self.integrations.carrier_booking,
            self.integrations.customer_notification,
        )
        if any(item.ai_may_fabricate_success for item in configured_integrations):
            raise ValueError("AI cannot fabricate success for authoritative integrations")
        return self


class LoadedReturnConfiguration(StrictConfigModel):
    configuration: ReturnPlatformConfiguration
    path: Path
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def load_return_configuration(path: Path) -> LoadedReturnConfiguration:
    """Load, size-bound, validate, and fingerprint one return configuration file."""
    resolved = path.expanduser().resolve(strict=True)
    if resolved.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("return configuration must be YAML")
    raw = resolved.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("return configuration exceeds 1 MB")
    parsed: Any = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        raise ValueError("return configuration root must be an object")
    return LoadedReturnConfiguration(
        configuration=ReturnPlatformConfiguration.model_validate(parsed),
        path=resolved,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
