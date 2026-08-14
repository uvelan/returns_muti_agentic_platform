"""Validated configuration for the production Ferguson return flow."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

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
    implementation_id: NonBlank
    task_queue: NonBlank
    state_namespace: NonBlank
    prompt_ref: NonBlank | None = None
    policy_ref: NonBlank | None = None
    ai_route_ref: NonBlank | None = None
    # Whether the case parks when this agent fails, or carries on without it.
    # Declared per agent rather than inferred from the stage, because the same
    # stage can be required for one deployment and advisory for another --
    # a branch with no warehouse has no bay to assign.
    failure_policy: Literal["blocking", "best_effort"] = "blocking"
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    retry_max_attempts: int = Field(default=3, ge=1)
    max_concurrency: int = Field(default=10, ge=1)
    requests_per_minute: int = Field(default=60, ge=1)
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)


class AnchorExtractorConfiguration(StrictConfigModel):
    anchor_type: NonBlank
    patterns: tuple[NonBlank, ...] = Field(min_length=1)


class ConversationPromptsConfiguration(StrictConfigModel):
    greeting_patterns: tuple[NonBlank, ...] = Field(min_length=1)
    greeting_response: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]
    greeting_status: NonBlank
    greeting_title: NonBlank
    initial_match_template: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]
    initial_no_match_template: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]
    continue_match_template: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]
    continue_no_match_template: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]
    confirmation_associate_template: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]
    confirmation_assistant_template: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]
    submission_associate_template: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]
    submission_assistant_template: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)
    ]


class DisambiguationAttributeConfiguration(StrictConfigModel):
    """One deterministic candidate attribute that may be requested from an associate."""

    slot: NonBlank
    candidate_field: NonBlank
    label: NonBlank
    priority: int = Field(ge=0, le=10_000)


class ProgressiveDialogueStateConfiguration(StrictConfigModel):
    """Domain state names consumed by the reusable conversation runtime."""

    no_candidates: NonBlank = "ENTITY_IDENTIFICATION"
    single_candidate: NonBlank = "LINE_SELECTION"
    slot_disambiguation: NonBlank = "CUSTOMER_DISAMBIGUATION"
    generic_disambiguation: NonBlank = "ORDER_DISAMBIGUATION"


class ProgressiveDiscoveryConfiguration(StrictConfigModel):
    """Bounded fuzzy retrieval and progressive disambiguation policy."""

    enabled: bool = True
    customer_fulltext_index: NonBlank = "customer_name_search_v2"
    product_fulltext_index: NonBlank = "product_description_search_v2"
    candidate_limit: int = Field(default=10, ge=1, le=20)
    max_edit_distance: int = Field(default=2, ge=0, le=2)
    one_edit_min_token_length: int = Field(default=4, ge=3, le=64)
    two_edit_min_token_length: int = Field(default=8, ge=4, le=128)
    candidate_ttl_seconds: int = Field(default=900, ge=60, le=3_600)
    max_clarification_options: int = Field(default=6, ge=2, le=12)
    dialogue_states: ProgressiveDialogueStateConfiguration = Field(
        default_factory=ProgressiveDialogueStateConfiguration
    )
    weak_anchor_source_fallback_enabled: bool = True
    weak_anchor_targeted_graph_upsert_enabled: bool = False
    fuzzy_search_anchors: tuple[NonBlank, ...] = (
        "CUSTOMER_NAME",
        "PRODUCT_DESCRIPTION",
    )
    disambiguation_attributes: tuple[DisambiguationAttributeConfiguration, ...] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def validate_policy(self) -> ProgressiveDiscoveryConfiguration:
        slots = [item.slot for item in self.disambiguation_attributes]
        if len(slots) != len(set(slots)):
            raise ValueError("progressive discovery disambiguation slots must be unique")
        if (
            self.weak_anchor_targeted_graph_upsert_enabled
            and not self.weak_anchor_source_fallback_enabled
        ):
            raise ValueError(
                "weak-anchor graph upsert requires weak-anchor source fallback to be enabled"
            )
        return self


class IdentificationSearchConfiguration(StrictConfigModel):
    """One graph read that can answer one identification signal.

    A field usually has more than one: an associate saying "Dallas" could mean
    where the customer is registered or where this order was sent, and a phone
    number has to be asked for both as typed and as bare digits. Each of those
    is an entry here rather than a branch in Python.

    `applies_when_pattern` is what lets one value take different operators
    without a code path per case -- a complete email address is an identifier
    and matches EXACT, a fragment is all the associate could read off a screen
    and only CONTAINS finds it. Both are entries; the pattern decides which is
    issued.

    `narrow_with` names another intent key whose value is added as a second
    filter on the same entity when it is present. A quantity alone matches
    thousands of lines; a quantity and a product description together are a
    real narrowing, and skipping the pass entirely when the companion is absent
    would lose the quantity-only search that is still worth running.
    """

    entity: NonBlank
    field: NonBlank
    #: Any operator the active schema enables for the field, plus FULLTEXT for a
    #: ranked index read. Validated against the schema at runtime, not here:
    #: this file is loaded by processes that hold no graph schema.
    strategy: NonBlank = "EXACT"
    limit: int = Field(default=5, ge=1, le=100)
    #: Result columns. Empty means "every displayable field on the entity",
    #: which is what the compiler already defaults to.
    result_fields: tuple[NonBlank, ...] = ()
    #: How the associate's value is reshaped before it is sent. `AS_TYPED`,
    #: `DIGITS` (punctuation stripped) or `LOWERCASE`.
    value_form: NonBlank = "AS_TYPED"
    applies_when_pattern: str | None = None
    narrow_with: NonBlank | None = None
    #: A last resort rather than an ordinary pass: issued only when every other
    #: search in the turn came back empty. This is how the misspelling recovery
    #: for customer names is expressed -- an indexed approximate search is
    #: expensive and imprecise next to an exact one, so it earns its turn only
    #: once the cheap searches have failed.
    only_when_nothing_found: bool = False
    #: The most a candidate found by a deferred search may score. An approximate
    #: match standing in for an exact one must not present as strongly as the
    #: thing it stood in for -- the associate still has to confirm it.
    deferred_score_ceiling_millionths: int = Field(default=600_000, ge=0, le=1_000_000)
    #: What a match from this search is called in a candidate's `matches` list.
    #: Defaults to `<field>_<strategy>`. Named explicitly where a label is part
    #: of an existing contract -- `customer_name_fuzzy` is one the reasoning
    #: prompt reads to decide whether to hedge about spelling.
    match_label: NonBlank | None = None
    #: Only for `strategy: FULLTEXT`. Defaults to the progressive customer index
    #: when omitted, since that is the only index the platform creates today.
    fulltext_index: NonBlank | None = None


class IdentificationFieldConfiguration(StrictConfigModel):
    """One thing an associate can say that helps identify an order.

    This is the catalogue the audit's contract C6 requires: identity, the key
    the model populates, what the value means, how it is normalized and
    validated, which graph reads answer it, how much it is worth when ranking,
    how sensitive it is, and whether it is on. Adding an identification field is
    adding an entry here.

    A field with no `searches` -- or whose searches name entities and fields the
    active schema does not have -- is not an error and is not silently dropped.
    It is reported to the associate's turn as an unusable signal, which is the
    honest state for "the associate told us the colour and nothing in this graph
    records colour".
    """

    field_id: NonBlank
    #: The key the reasoning model populates on OrderSearchIntent. Distinct from
    #: `field_id` because one is the operator's vocabulary and the other is the
    #: model's, and they are not always the same word.
    intent_key: NonBlank
    enabled: bool = True
    #: `STRING`, `INTEGER`, `DATE_LOWER_BOUND`, `DATE_UPPER_BOUND` or
    #: `DATE_POINT`. The date kinds are what let one configured date field carry
    #: an open bound and another carry a same-day window without either one
    #: knowing which field it is.
    value_type: NonBlank = "STRING"
    #: Whether the model may supply several values. A date bound may not; a list
    #: of names may.
    multiple: bool = True
    label: NonBlank
    description: str = ""
    #: Other words an associate or the model may use for this. Shown to the
    #: model so a newly configured field is recognizable without a prompt edit.
    aliases: tuple[NonBlank, ...] = ()
    #: `NONE`, `LOWER_ALPHANUMERIC`, `DIGITS` or `TRIM`. Applied before the
    #: value is compared during ranking, never before it is sent to the graph --
    #: the graph gets the form each search asks for.
    normalization: NonBlank = "NONE"
    #: A value failing this is reported as invalid rather than searched, so a
    #: mistyped ZIP does not silently return nothing.
    validation_pattern: str | None = None
    #: `NONE`, `CONTACT` or `PERSONAL`. Carried for the clarification and
    #: redaction policies to read; nothing here decides disclosure.
    sensitivity: NonBlank = "NONE"
    #: What a match on this field is worth when candidates are ranked, and the
    #: extra it is worth when the match is exact rather than partial. Millionths
    #: for the same reason every other weight in this file is: integers compare
    #: and serialize identically everywhere, floats do not.
    ranking_weight_millionths: int = Field(default=100_000, ge=0, le=1_000_000)
    exact_match_bonus_millionths: int = Field(default=0, ge=0, le=1_000_000)
    #: How badly this field is wanted when the agent has to ask for something.
    #: Mirrors `clarification_policy.fields[].priority`.
    clarification_priority: int = Field(default=0, ge=0, le=10_000)
    searches: tuple[IdentificationSearchConfiguration, ...] = ()

    @model_validator(mode="after")
    def validate_field(self) -> IdentificationFieldConfiguration:
        if self.value_type in {"DATE_LOWER_BOUND", "DATE_UPPER_BOUND", "DATE_POINT"}:
            if self.multiple:
                raise ValueError(
                    f"identification field {self.field_id!r} is a date bound and cannot be multiple"
                )
        for search in self.searches:
            if search.strategy == "FULLTEXT" and search.narrow_with is not None:
                raise ValueError(
                    f"identification field {self.field_id!r} cannot narrow a FULLTEXT search: "
                    "the index is the predicate"
                )
        return self


class DiscoveryConfiguration(StrictConfigModel):
    web_order_pattern: NonBlank
    ambiguity_gap_millionths: int = Field(ge=0, le=1_000_000)
    auto_confirmation_allowed: bool
    anchor_weights: dict[NonBlank, int]
    conflict_penalty_millionths: int = Field(ge=0, le=1_000_000)
    strong_anchors: tuple[NonBlank, ...] = Field(min_length=1)
    anchor_extractors: tuple[AnchorExtractorConfiguration, ...] = Field(min_length=1)
    free_text_fallback_anchor: NonBlank
    conversation: ConversationPromptsConfiguration
    progressive: ProgressiveDiscoveryConfiguration
    #: Every signal the canonical Order Discovery agent can search on. Empty is
    #: allowed and means "this deployment has configured no identification
    #: fields", which the agent reports rather than papering over with a
    #: built-in list -- a hardcoded fallback here is exactly the defect this
    #: catalogue exists to remove.
    identification_fields: tuple[IdentificationFieldConfiguration, ...] = ()

    @model_validator(mode="after")
    def validate_weights(self) -> DiscoveryConfiguration:
        if any(not 0 <= weight <= 1_000_000 for weight in self.anchor_weights.values()):
            raise ValueError("discovery anchor weights must be 0..1000000")
        field_ids = [item.field_id for item in self.identification_fields]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("discovery identification field ids must be unique")
        intent_keys = [item.intent_key for item in self.identification_fields]
        if len(intent_keys) != len(set(intent_keys)):
            raise ValueError("discovery identification intent keys must be unique")
        known_keys = set(intent_keys)
        for item in self.identification_fields:
            for search in item.searches:
                if search.narrow_with is not None and search.narrow_with not in known_keys:
                    raise ValueError(
                        f"identification field {item.field_id!r} narrows with unknown intent key "
                        f"{search.narrow_with!r}"
                    )
        extractor_types = [item.anchor_type for item in self.anchor_extractors]
        if len(extractor_types) != len(set(extractor_types)):
            raise ValueError("discovery anchor extractor types must be unique")
        missing_strong = set(self.strong_anchors) - set(extractor_types)
        if missing_strong:
            raise ValueError(
                f"strong anchors require extraction patterns: {', '.join(sorted(missing_strong))}"
            )
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
    customer_city_paths: tuple[NonBlank, ...] = ()
    customer_postal_code_paths: tuple[NonBlank, ...] = ()
    customer_account_type_paths: tuple[NonBlank, ...] = ()
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
    label: NonBlank
    priority: int = Field(ge=0, le=10_000)
    customer_answerable: bool
    field_group: NonBlank
    anchor_type: NonBlank | None = None
    candidate_field: NonBlank | None = None
    #: How long an answer to this question stays good for. `None` -- the
    #: default -- means it does not go stale: a return reason stated on Monday
    #: is still the return reason on Tuesday, and re-asking it would be the
    #: defect, not the safeguard. Set it only where the answer is genuinely
    #: perishable.
    answer_ttl_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    #: Whether an answer must be read back and confirmed before it is acted on,
    #: however clearly it was stated. The one legitimate reason to raise a
    #: question the associate has already answered.
    confirmation_required: bool = False
    #: A stated value failing this is captured but marked invalid, so the agent
    #: asks again for a reason it can name rather than silently ignoring it.
    validation_pattern: str | None = None


class SmartQuestionGoal(StrictConfigModel):
    preferred_field_groups: tuple[NonBlank, ...] = Field(min_length=1)
    preferred_fields: tuple[NonBlank, ...] = Field(min_length=1)


class SmartQuestionConfiguration(StrictConfigModel):
    version: NonBlank
    max_prompts_per_turn: int = Field(ge=1, le=5)
    max_fields_per_turn: int = Field(ge=1, le=5)
    max_distinct_values_for_ai: int = Field(default=5, ge=1, le=20)
    phrasing_owner: Literal["LLM", "CONFIG"]
    field_selection_owner: Literal["LLM", "CONFIG"]
    fields: tuple[SmartQuestion, ...] = Field(min_length=1)
    goals: dict[NonBlank, SmartQuestionGoal] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_policy(self) -> SmartQuestionConfiguration:
        field_names = [item.field for item in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("smart-question fields must be unique")
        known_fields = set(field_names)
        for goal_name, goal in self.goals.items():
            unknown = set(goal.preferred_fields) - known_fields
            if unknown:
                raise ValueError(
                    f"smart-question goal {goal_name} references unknown fields: "
                    f"{', '.join(sorted(unknown))}"
                )
        return self


class BranchStagingConfiguration(StrictConfigModel):
    require_return_number_tag: bool
    allow_manufacturer_box_marking: bool
    allow_branch_inventory_addition: bool


class ReturnPolicyConfiguration(StrictConfigModel):
    photo_required_reason_codes: tuple[NonBlank, ...]
    supported_product_presence: tuple[NonBlank, ...] = Field(min_length=1)
    normalized_return_methods: tuple[NonBlank, ...] = Field(min_length=1)
    #: Which shipping instruction types tender a bill of lading, and therefore
    #: make `RECORD_SHIPPING_INSTRUCTIONS` emit `BOL_TENDERED` as well as
    #: `SHIPPING_INSTRUCTIONS_ISSUED`.
    #:
    #: Configuration because it varies with the method catalogue above: an
    #: operator who adds a freight return method through the Control Centre has
    #: decided something about whether it tenders a BOL, and a hardcoded set
    #: would answer "no" for every method it had never heard of -- silently
    #: dropping a production event rather than failing.
    #:
    #: No Python default. A fallback here would be the same hardcoded vocabulary
    #: in a second place, reachable exactly when the operator's answer is
    #: missing, which is when guessing is least defensible.
    bol_tendering_instruction_types: tuple[NonBlank, ...]
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


class BusinessWorkingPeriodConfiguration(StrictConfigModel):
    """One span of working time on one weekday, in the calendar's own zone.

    `weekday` is Monday-0, matching `date.weekday()`. `end_minute` is exclusive
    and may be 1440 -- midnight at the end of the day -- so two consecutive
    whole days join into one continuous span rather than leaving a zero-length
    seam between them.
    """

    weekday: int = Field(ge=0, le=6)
    start_minute: int = Field(ge=0, le=1439)
    end_minute: int = Field(ge=1, le=1440)

    @model_validator(mode="after")
    def validate_span(self) -> BusinessWorkingPeriodConfiguration:
        if self.start_minute >= self.end_minute:
            raise ValueError("a working period must start before it ends")
        return self


class BusinessCalendarConfiguration(StrictConfigModel):
    """When a calendar is open, declared rather than assumed (C8).

    There is no Mon-Fri anywhere in the code that reads this. A deployment
    whose warehouse works Saturdays declares a Saturday period and gets one; a
    24/7 operation declares every weekday whole and gets wall-clock behaviour
    back exactly. `holidays` are whole non-working days in this calendar's own
    zone, so a holiday falls on the same day for every reader regardless of
    where the worker computing it happens to run.

    A calendar with no working periods at all is rejected here rather than at
    use: a case whose deadline can never arrive is a case nobody is ever told
    about, and the configuration release is the last place that can still be
    refused cheaply.
    """

    calendar_id: NonBlank
    timezone: NonBlank = "UTC"
    working_periods: tuple[BusinessWorkingPeriodConfiguration, ...] = Field(min_length=1)
    holidays: tuple[date, ...] = ()


class ReturnCaseTimingConfiguration(StrictConfigModel):
    """How long the case waits, and how often it chases.

    Defaults, not constants: every field is editable through a configuration
    release. A workflow reads them once at start and keeps them for its own
    lifetime -- an in-flight return must not have its deadline moved underneath
    it -- so a change applies to new cases.

    `support_response_wait_seconds` and `reminder_interval_seconds` are
    **business-calendar durations**. Eight hours means eight *working* hours,
    which over a weekend is a different wall-clock instant entirely;
    `business_calendar_id` names the calendar in `business_calendars` that
    decides, and `timezone` is the fallback zone used when that calendar does
    not declare one.

    Both were documented this way long before anything read them: the workflow
    computed `workflow.now() + timedelta(seconds=...)`, so a return raised at
    16:30 on a Friday chased Support at 18:30, 20:30 and 22:30 into an empty
    queue and parked itself at 00:30 on Saturday. The arithmetic now runs in
    `resolve_business_deadline` against the configured calendar. A calendar
    that declares every day whole restores the old behaviour exactly, which is
    what a 24/7 operation should configure.

    `bay_wait_seconds` is deliberately NOT a business duration. It bounds dead
    time on the critical path while an associate waits, and stretching it
    across a weekend would leave a live conversation hanging.
    """

    # Bay is advisory and sits in front of every return, so this is dead time
    # on the critical path. Short on purpose; measure before raising it.
    bay_wait_seconds: int = Field(default=120, ge=0, le=86_400)
    support_response_wait_seconds: int = Field(default=28_800, ge=60)
    reminder_interval_seconds: int = Field(default=7_200, ge=60)
    max_reminders: int = Field(default=3, ge=0, le=50)
    # What happens when the reminders run out. Without this the case sits
    # forever with nobody told, which is the failure mode a reminder cap
    # creates if it has no terminal branch.
    on_reminders_exhausted: Literal["PARK_FOR_OPERATIONS", "ESCALATE"] = "PARK_FOR_OPERATIONS"
    business_calendar_id: NonBlank = "default"
    timezone: NonBlank = "UTC"


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


class FeatureFlagsConfiguration(StrictConfigModel):
    reusable_conversation_engine: bool = False
    order_discovery_copilot: bool = False
    copilot_operations_console: bool = False
    graph_first_runtime_configuration: bool = False


class CredentialBindingConfiguration(StrictConfigModel):
    """One Vault reference whose value never enters graph configuration."""

    profile_key: NonBlank
    vault_reference: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=16,
            max_length=768,
            pattern=r"^vault://secret/production/[A-Za-z0-9_./-]+#[A-Za-z0-9_-]+(?:\?version=\d+)?$",
        ),
    ]
    validation_receipt_id: NonBlank | None = None
    validation_configuration_checksum: (
        Annotated[
            str,
            StringConstraints(pattern=r"^[a-f0-9]{64}$"),
        ]
        | None
    ) = None
    bootstrap_managed: bool = False

    @model_validator(mode="after")
    def require_validation_receipt(self) -> CredentialBindingConfiguration:
        if not self.bootstrap_managed and (
            self.validation_receipt_id is None or self.validation_configuration_checksum is None
        ):
            raise ValueError(
                "control-plane credentials require a validation receipt and configuration checksum"
            )
        return self


class AIModelBindingConfiguration(StrictConfigModel):
    model_id: NonBlank
    model_class: Literal["LIGHTWEIGHT", "STANDARD"]
    task_keys: tuple[NonBlank, ...] = Field(min_length=1)
    priority: int = Field(default=1, ge=1, le=100)


class AIValidatedRouteConfiguration(StrictConfigModel):
    """One validated credential/model/task combination used by runtime routing."""

    credential_profile_key: NonBlank
    model_id: NonBlank
    task_key: NonBlank
    validation_receipt_id: NonBlank
    validation_configuration_checksum: Annotated[
        str,
        StringConstraints(pattern=r"^[a-f0-9]{64}$"),
    ]


class AIProviderRuntimeConfiguration(StrictConfigModel):
    provider_key: Literal["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA"]
    enabled: bool = False
    base_url: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=8, max_length=1024),
    ]
    credentials: tuple[CredentialBindingConfiguration, ...] = ()
    models: tuple[AIModelBindingConfiguration, ...] = ()
    validated_routes: tuple[AIValidatedRouteConfiguration, ...] = ()
    priority: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def validate_enabled_provider(self) -> AIProviderRuntimeConfiguration:
        if self.enabled and self.provider_key != "OLLAMA" and not self.credentials:
            raise ValueError("enabled hosted AI providers require at least one credential")
        if self.enabled and not self.models:
            raise ValueError("enabled AI providers require at least one configured model")

        model_ids = [item.model_id for item in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("AI provider model IDs must be unique")

        profile_keys = [item.profile_key for item in self.credentials]
        if len(profile_keys) != len(set(profile_keys)):
            raise ValueError("AI credential profile keys must be unique")

        route_keys = [
            (item.credential_profile_key, item.model_id, item.task_key)
            for item in self.validated_routes
        ]
        if len(route_keys) != len(set(route_keys)):
            raise ValueError("AI validated credential/model/task routes must be unique")

        if self.enabled and self.provider_key != "OLLAMA":
            known_credentials = {credential.profile_key for credential in self.credentials}
            known_model_tasks = {
                (model.model_id, task_key) for model in self.models for task_key in model.task_keys
            }
            unexpected = sorted(
                route
                for route in route_keys
                if route[0] not in known_credentials
                or (route[1], route[2]) not in known_model_tasks
            )
            if unexpected:
                raise ValueError(
                    "AI validated routes must reference configured credentials "
                    f"and model tasks: {unexpected}"
                )

            route_receipts_by_profile: dict[str, set[str]] = {}
            for route in self.validated_routes:
                route_receipts_by_profile.setdefault(
                    route.credential_profile_key,
                    set(),
                ).add(route.validation_receipt_id)

            invalid_credentials = sorted(
                credential.profile_key
                for credential in self.credentials
                if credential.validation_receipt_id is not None
                and credential.validation_receipt_id
                not in route_receipts_by_profile.get(
                    credential.profile_key,
                    set(),
                )
            )
            if invalid_credentials:
                raise ValueError(
                    "AI credential validation receipt must belong to one of "
                    "its validated routes: " + ", ".join(invalid_credentials)
                )

        return self


class DataSourceRuntimeConfiguration(StrictConfigModel):
    source_key: NonBlank
    source_type: Literal["MONGODB", "NEO4J", "SQLSERVER", "VALKEY", "TEMPORAL"]
    enabled: bool = True
    access_mode: Literal["READ_ONLY", "READ_WRITE"]
    host: NonBlank | None = None
    port: int | None = Field(default=None, ge=1, le=65_535)
    uri: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=1024)]
        | None
    ) = None
    username: NonBlank | None = None
    database: NonBlank | None = None
    credential: CredentialBindingConfiguration | None = None
    required_datasets: tuple[NonBlank, ...] = ()
    validation_receipt_id: NonBlank | None = None
    validation_configuration_checksum: (
        Annotated[
            str,
            StringConstraints(pattern=r"^[a-f0-9]{64}$"),
        ]
        | None
    ) = None
    bootstrap_managed: bool = False
    priority: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def validate_source(self) -> DataSourceRuntimeConfiguration:
        if self.source_type in {"MONGODB", "NEO4J"} and self.uri is None:
            raise ValueError(f"{self.source_type} data sources require a URI")
        if self.source_type in {"SQLSERVER", "VALKEY", "TEMPORAL"} and (
            self.host is None or self.port is None
        ):
            raise ValueError(f"{self.source_type} data sources require host and port")
        if (
            self.enabled
            and not self.bootstrap_managed
            and (
                self.validation_receipt_id is None or self.validation_configuration_checksum is None
            )
        ):
            raise ValueError(
                "control-plane data sources require a validation receipt and configuration checksum"
            )
        if self.enabled and not self.bootstrap_managed and self.credential is None:
            raise ValueError("control-plane data sources require a Vault credential binding")
        if self.credential is not None and (
            self.credential.bootstrap_managed != self.bootstrap_managed
        ):
            raise ValueError("data-source and credential bootstrap modes must match")
        if self.enabled and not self.bootstrap_managed and self.credential is not None:
            if self.credential.validation_receipt_id != self.validation_receipt_id:
                raise ValueError("data-source and credential validation receipt IDs must match")
            if (
                self.credential.validation_configuration_checksum
                != self.validation_configuration_checksum
            ):
                raise ValueError("data-source and credential validation checksums must match")
        return self


class RuntimeIntegrationsConfiguration(StrictConfigModel):
    ai_providers: tuple[AIProviderRuntimeConfiguration, ...] = ()
    data_sources: tuple[DataSourceRuntimeConfiguration, ...] = ()

    @model_validator(mode="after")
    def validate_unique_keys(self) -> RuntimeIntegrationsConfiguration:
        providers = [item.provider_key for item in self.ai_providers]
        if len(providers) != len(set(providers)):
            raise ValueError("runtime AI provider keys must be unique")
        sources = [item.source_key for item in self.data_sources]
        if len(sources) != len(set(sources)):
            raise ValueError("runtime data-source keys must be unique")
        source_map = {item.source_key: item for item in self.data_sources}
        read_only_sources = {"source-mongodb", "omc-sqlserver"}
        violations = sorted(
            key
            for key in read_only_sources
            if key in source_map and source_map[key].access_mode != "READ_ONLY"
        )
        if violations:
            raise ValueError(
                "authoritative external sources must remain read-only: " + ", ".join(violations)
            )
        return self


class ReturnPlatformConfiguration(StrictConfigModel):
    schema_version: NonBlank
    assumption_set_version: NonBlank
    agents: dict[NonBlank, AgentConfiguration]
    discovery: DiscoveryConfiguration
    source_resolution: SourceResolutionConfiguration
    clarification_policy: SmartQuestionConfiguration
    return_policy: ReturnPolicyConfiguration
    workflow: WorkflowConfiguration
    support: SupportConfiguration
    omc: OmcConfiguration
    bay: BayConfiguration
    # Defaulted so an existing release without the block still loads: the
    # values are the documented defaults, and a deployment that wants others
    # states them.
    return_case: ReturnCaseTimingConfiguration = Field(
        default_factory=ReturnCaseTimingConfiguration
    )
    # Defaulted empty so an existing release without the block still loads. An
    # empty set is not a silent Mon-Fri: `resolve_business_deadline` falls back
    # to wall clock and says so on the case, which is the behaviour that was
    # there before and is now visible rather than assumed.
    business_calendars: tuple[BusinessCalendarConfiguration, ...] = ()
    integrations: IntegrationConfiguration
    extensions: ExtensionConfiguration
    runtime_integrations: RuntimeIntegrationsConfiguration = Field(
        default_factory=RuntimeIntegrationsConfiguration
    )
    feature_flags: FeatureFlagsConfiguration = Field(default_factory=FeatureFlagsConfiguration)

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
