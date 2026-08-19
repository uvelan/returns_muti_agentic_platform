"""Validated configuration for the production Ferguson return flow."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Collection
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from return_platform.configuration.settings import PRODUCTION_ENVIRONMENT
from return_platform.policy.eligibility_policy import ReturnEligibilityPolicy
from return_platform.policy.vocabulary import ReturnReason

if TYPE_CHECKING:  # pragma: no cover - see `validate_return_method_requirements`
    from return_platform.operations.case_projection.completion import (
        ReturnMethodRequirementTable,
    )

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
    # There is deliberately no `failure_policy` here. What happens when a step
    # fails is not a per-agent setting: it is decided by the workflow phase that
    # calls it, in code, and it has to be, because the two directions are
    # different control flow rather than different values. `ReturnCaseWorkflow`
    # absorbs a failed bay request into a `REQUEST_FAILED` result and continues
    # (`_gather_bay`), absorbs an unavailable support drafter into the
    # deterministic template (`_open_support`, and again inside the activity
    # itself), and parks the case on a graph-sync failure
    # (`_park_for_graph_sync_failure`). A configured value could not have
    # produced any of those; it could only have contradicted them.
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
    #: Where the order's own date lives on the sales document, in preference
    #: order. This is the basis of the standard return window, so it is declared
    #: beside the other source bindings rather than written into the policy
    #: package -- a re-bind is an operator edit, not a code change.
    #:
    #: Defaulted empty so a release cut before this field still parses. Empty
    #: means the deployment has bound no order date, and a case whose window
    #: cannot be dated reviews rather than guessing one.
    order_date_paths: tuple[NonBlank, ...] = ()
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


class ReturnMethodRequirementConfiguration(StrictConfigModel):
    """One return method and the artifacts it needs before the return is complete.

    The operator-owned half of `operations/case_projection/completion.py`. The
    *structure* of a legal row is not stated here twice -- every row is handed to
    `ReturnMethodRequirementTable` at load, so the three guards that make the
    table safe (every row requires `RMA`, no row names `UNKNOWN`, no row means
    unmapped rather than "requires nothing") are enforced by the one model that
    owns them.
    """

    method: NonBlank
    requires: tuple[NonBlank, ...] = Field(min_length=1)


class ReturnPolicyConfiguration(StrictConfigModel):
    photo_required_reason_codes: tuple[NonBlank, ...]
    supported_product_presence: tuple[NonBlank, ...] = Field(min_length=1)
    normalized_return_methods: tuple[NonBlank, ...] = Field(min_length=1)
    #: What each return method needs before the return is business-complete.
    #: Keyed by the catalogue directly above, and released with it, so an
    #: operator adding `OFFSITE_HEAVY_PICKUP` declares what it requires in the
    #: same release that declares the method exists.
    #:
    #: No Python default, for the same reason `bol_tendering_instruction_types`
    #: has none: a fallback would be the same table in a second place, reachable
    #: exactly when the operator has not answered. It would also be the *worst*
    #: place to guess -- a wrong row either hangs a return forever on an artifact
    #: nobody will produce, or lets one report itself complete without the
    #: paperwork that proves it happened.
    #:
    #: A method in the catalogue with no row here is **unmapped**, which leaves
    #: the completion profile unresolved and the case awaiting `RETURN_METHOD`.
    #: That is the safe direction and it is deliberately not an error: `UNKNOWN`
    #: is in the catalogue and cannot have a row.
    return_method_requirements: tuple[ReturnMethodRequirementConfiguration, ...] = Field(
        min_length=1
    )
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

    @model_validator(mode="after")
    def validate_return_method_requirements(self) -> ReturnPolicyConfiguration:
        """Refuse a requirement table the projection would refuse anyway.

        Two checks, and only the second is written here. The first -- every row
        requires `RMA`, no row names `UNKNOWN`, no method appears twice, no row
        names a dimension that is not a fulfilment requirement -- is delegated by
        constructing the real `ReturnMethodRequirementTable`, so the guards have
        one home and a release cannot express a table the projection would then
        reject at read time.

        The import is deferred to call time on purpose. `case_projection`
        reaches `return_platform.agents`, whose package `__init__` imports this
        module, so a module-level import here is a cycle. Validation runs long
        after both modules are loaded, which is why this placement is safe and a
        top-level one is not.
        """
        from return_platform.operations.case_projection.completion import (  # noqa: PLC0415
            ReturnMethodRequirementTable,
        )

        try:
            ReturnMethodRequirementTable.model_validate(
                {"rows": [row.model_dump(mode="json") for row in self.return_method_requirements]}
            )
        except ValueError as invalid:
            raise ValueError(
                f"return_policy.return_method_requirements is not a usable table: {invalid}"
            ) from invalid

        catalogue = {method.strip().upper() for method in self.normalized_return_methods}
        unknown = sorted(
            {
                row.method.strip().upper()
                for row in self.return_method_requirements
                if row.method.strip().upper() not in catalogue
            }
        )
        if unknown:
            raise ValueError(
                "return_policy.return_method_requirements names methods that are not in "
                f"normalized_return_methods: {', '.join(unknown)}"
            )
        return self


class SelectionVocabularyConfiguration(StrictConfigModel):
    """The reason and condition catalogues an associate selects a line from.

    Plan sect. 12.4: *"Reason and condition vocabularies come from return
    configuration."* Until this block existed they came from nowhere --
    `SelectedItemRequest` accepted any string up to 128 characters and
    `LineSelection` carried it through verbatim, its docstring saying a check
    written then would have been a check against a literal in the API module.
    This is the answer that makes the check real.

    **A top-level block, not a nested one, and that is the whole reason it
    validates on a live deployment.** `bootstrap_graph_configuration` merges the
    packaged file underneath an active release at *top-level* granularity, so a
    key added inside `return_policy` after a release was cut is dropped by the
    release's own `return_policy` and can never arrive (platform defect D11,
    observed with `copilot.order_discovery_agent_id`). A new top-level key is
    absent from every existing release, so the packaged value wins and reaches
    the deployment on the next publish -- the same reasoning that put
    `return_eligibility_policy` and `copilot` where they are.

    **An empty catalogue means "no catalogue has been published", and that is
    not the same as "reject everything".** A deployment running a release cut
    before this block gets exactly the behaviour it has today: length-bounded
    free text, recorded verbatim. Refusing every selection instead would take a
    branch's returns offline over a configuration key nobody had been asked for.
    The direction matches `business_calendars`, which defaults empty and falls
    back to wall clock rather than inventing a working week.
    """

    #: Why the line is coming back. Constrained to `ReturnReason`, which is the
    #: evaluator's own closed vocabulary: `case_policy_facts` maps a stored
    #: `return_reason` onto it and resolves anything it does not recognise to
    #: `UNKNOWN`, which routes the case to a human. A release free to publish
    #: `DAMAGED_IN_TRANSIT` would therefore look correct, pass validation, and
    #: send every return using it to review with nothing in the audit trail
    #: saying why. Refusing it here is the only place that can say so.
    reasons: tuple[NonBlank, ...] = ()
    #: What state the line is in. **Not** constrained against a code vocabulary,
    #: because there is no item-condition enum to constrain it against -- the
    #: evaluator reads condition as a set of named tri-state facts, not as one
    #: closed value -- and inventing an enum here to validate against would be
    #: the hardcoded catalogue this block exists to remove.
    conditions: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def validate_vocabularies(self) -> SelectionVocabularyConfiguration:
        for name, values in (("reasons", self.reasons), ("conditions", self.conditions)):
            normalized = [value.strip().upper() for value in values]
            duplicated = sorted({value for value in normalized if normalized.count(value) > 1})
            if duplicated:
                raise ValueError(
                    f"selection_vocabulary.{name} lists the same entry twice: "
                    f"{', '.join(duplicated)}"
                )
        known = {member.value for member in ReturnReason} - {ReturnReason.UNKNOWN.value}
        unknown = sorted({value.strip().upper() for value in self.reasons} - known)
        if unknown:
            raise ValueError(
                "selection_vocabulary.reasons names reason(s) the policy evaluator cannot "
                f"read, which would silently route every return using them to review: "
                f"{', '.join(unknown)}. Known reasons: {', '.join(sorted(known))}"
            )
        return self

    def unknown_reasons(self, values: Collection[str]) -> tuple[str, ...]:
        """The submitted reasons this release does not publish, in order.

        Empty when no catalogue is published, because an unpublished catalogue
        refuses nothing -- see the class docstring. Comparison is on the
        stripped, upper-cased token, so a client sending `damaged` is answered
        the same way as one sending `DAMAGED`.
        """
        return self._unknown(self.reasons, values)

    def unknown_conditions(self, values: Collection[str]) -> tuple[str, ...]:
        """The submitted conditions this release does not publish, in order."""
        return self._unknown(self.conditions, values)

    @staticmethod
    def _unknown(catalogue: tuple[str, ...], values: Collection[str]) -> tuple[str, ...]:
        if not catalogue:
            return ()
        published = {entry.strip().upper() for entry in catalogue}
        seen: list[str] = []
        for value in values:
            token = value.strip().upper()
            if token not in published and value not in seen:
                seen.append(value)
        return tuple(seen)


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

    Neither is `item_reservation_ttl_seconds`. It bounds how long a selected
    line's quantity is held out of everyone else's reach, and a hold that
    stretched over a weekend would make Monday's associate wait for Friday's
    abandoned conversation.
    """

    # Bay is advisory and sits in front of every return, so this is dead time
    # on the critical path. Short on purpose; measure before raising it.
    bay_wait_seconds: int = Field(default=120, ge=0, le=86_400)
    #: How long a selected quantity stays held before the hold expires (plan
    #: sect. 12.3). Configuration, not a source constant: the right value is the
    #: length of a counter conversation, which differs by branch and by trade,
    #: and a deployment that discovers its associates need longer must be able
    #: to say so in a release rather than in a deploy.
    #:
    #: Floored well above zero because a TTL shorter than a conversation turn
    #: would expire every hold before the associate finished naming the reason,
    #: and capped at a day because a hold nobody has authorized by then is an
    #: abandoned selection whatever the operator intended.
    item_reservation_ttl_seconds: int = Field(default=1_800, ge=60, le=86_400)
    support_response_wait_seconds: int = Field(default=28_800, ge=60)
    reminder_interval_seconds: int = Field(default=7_200, ge=60)
    max_reminders: int = Field(default=3, ge=0, le=50)
    # What happens when the reminders run out. Without this the case sits
    # forever with nobody told, which is the failure mode a reminder cap
    # creates if it has no terminal branch.
    on_reminders_exhausted: Literal["PARK_FOR_OPERATIONS", "ESCALATE"] = "PARK_FOR_OPERATIONS"
    business_calendar_id: NonBlank = "default"
    timezone: NonBlank = "UTC"


class TemporalReclamationConfiguration(StrictConfigModel):
    """Which stranded Temporal executions housekeeping may terminate.

    `reclaimable_task_queue_prefixes` is a **positive** test and the only thing
    that makes an execution a candidate. It is validated against the queues this
    deployment's workers actually poll (see
    `housekeeping.temporal_executions.TemporalExecutionReclaimer`), and a prefix
    that would match one of them is refused at construction -- so no release can
    express "reap the production queue", however it is written.

    `minimum_age_seconds` is a floor on how long an execution must have been
    running before it is considered stranded. It is NOT what makes reclamation
    safe -- `ReturnCaseWorkflow` legitimately runs for days -- it only keeps a
    suite that is running right now from having its own executions removed from
    under it.
    """

    enabled: bool = True
    #: Every suite that starts an execution names an ephemeral queue: the
    #: `test-return-case-<uuid>`, `test-order-discovery-<uuid>`,
    #: `test-return-concurrency-<uuid>`, `test-return-rejection-<uuid>` and
    #: `reasoning-*-test-<uuid>` queues in `backend/tests`. No deployed worker
    #: polls any of them, which is why an execution left on one can never make
    #: progress again.
    reclaimable_task_queue_prefixes: tuple[NonBlank, ...] = ("test-", "reasoning-")
    minimum_age_seconds: int = Field(default=3_600, ge=300)
    batch_limit: int = Field(default=500, ge=1, le=10_000)


class GraphGenerationReclamationConfiguration(StrictConfigModel):
    """When a RETIRED graph generation's nodes may finally be removed.

    Retirement is status-only by design (C9): every compiled read and write is
    generation-scoped, so a retired generation is unreachable rather than
    deleted. That is safe and it is also why generations accumulate without
    bound -- 212 `GraphGeneration` markers on this deployment -- which is what
    this reclaims.

    `retention_seconds` runs from the moment housekeeping first *observed* the
    generation eligible, not from its `created_at`. A generation that served for
    a month and retired a minute ago has an ancient `created_at`, so a
    creation-based window would delete it immediately and give late readers no
    grace at all.
    """

    enabled: bool = True
    retention_seconds: int = Field(default=86_400, ge=300)
    #: Generations reclaimed per pass. Small: each one is a bounded series of
    #: delete batches, and a pass that tried to clear a backlog in one go would
    #: hold Neo4j for the whole interval.
    batch_limit: int = Field(default=5, ge=1, le=1_000)
    #: Nodes per `DETACH DELETE`. A whole generation in one transaction is how a
    #: cleanup runs the page cache out of memory.
    node_delete_batch_size: int = Field(default=1_000, ge=100, le=50_000)


class ProbeDatabaseReclamationConfiguration(StrictConfigModel):
    """Which SQL Server databases housekeeping may drop.

    Test suites create `return_case_probe`, `return_pool_probe`,
    `return_shipment_probe`, `return_shipment_graph_probe`,
    `return_shipment_concurrency_probe` and never drop them. The suffix is a
    positive test, and the application's own databases are refused against it at
    construction, so no release can name one.
    """

    enabled: bool = True
    name_suffixes: tuple[NonBlank, ...] = ("_probe",)
    minimum_age_seconds: int = Field(default=3_600, ge=300)
    batch_limit: int = Field(default=50, ge=1, le=500)


class OrderLineReservationReclamationConfiguration(StrictConfigModel):
    """How many lapsed order-line holds housekeeping settles per pass.

    **No age window, unlike the three blocks above.** A reservation carries its
    own `expiresAt`, stamped from `return_case.item_reservation_ttl_seconds` as
    it stood when the hold was taken, so the deadline is already an operator
    decision. A second window here would be a second answer to "is this hold
    over", and the only thing it could do is hold quantity out of a branch's
    reach for longer than the TTL the operator published.

    **Enabled everywhere.** The probe-database and Temporal reclaimers are gated
    because they delete infrastructure; this one moves a lapsed hold from
    `ACTIVE` to `EXPIRED` on production data, which is ordinary bookkeeping. It
    is still a switch, because a deployment diagnosing the reservation lifecycle
    needs to be able to stop the sweep without stopping housekeeping.
    """

    enabled: bool = True
    #: Holds settled per pass. Larger than the other batches because each one is
    #: a single conditional update rather than a `DROP DATABASE` or a series of
    #: graph delete batches, and a branch that abandons selections faster than
    #: the sweep clears them would otherwise never catch up.
    batch_limit: int = Field(default=200, ge=1, le=5_000)


class InterceptionExpiryConfiguration(StrictConfigModel):
    """How many lapsed AI interceptions housekeeping settles per pass.

    **No age window, for the same reason the reservation block above has none.**
    An interception carries its own `expires_at`, stamped from the TTL that was
    live when it was opened, so the deadline is already a decision somebody made.
    A second window here could only keep a dead request in the collection longer
    than the TTL it was written with said it would be.

    **Enabled everywhere.** Two of the four blocks above are gated because they
    delete infrastructure; this moves a lapsed hold from `PENDING` to the
    `EXPIRED` it should already have carried, which is ordinary bookkeeping on a
    collection an operator reads. It is still a switch, because a deployment
    diagnosing the interception lifecycle needs to be able to stop the sweep
    without stopping housekeeping -- and stopping it is safe, since
    `InterceptionStore.list_pending` hides a lapsed record whether or not this
    reclaimer ever runs.
    """

    enabled: bool = True
    #: Interceptions settled per pass. Sized like the reservation batch and for
    #: the same reason: each one is a single conditional update, and a
    #: deployment that has been accumulating dead holds for days needs a batch
    #: that can actually drain the backlog over a few intervals.
    batch_limit: int = Field(default=200, ge=1, le=5_000)


class HousekeepingConfiguration(StrictConfigModel):
    """Operational debris reclamation, on a schedule.

    Every window here is configuration rather than a constant because the right
    value differs by deployment: a CI box wants a retention measured in hours, a
    staging environment wants days, and neither should require a code change.

    What is *not* configuration is which resources are reclaimable at all. Those
    rules are structural (see the `housekeeping` package) precisely because a
    configuration mistake must not be able to reach a live return.
    """

    enabled: bool = True
    interval_seconds: int = Field(default=900, ge=30, le=86_400)
    temporal_executions: TemporalReclamationConfiguration = Field(
        default_factory=TemporalReclamationConfiguration
    )
    graph_generations: GraphGenerationReclamationConfiguration = Field(
        default_factory=GraphGenerationReclamationConfiguration
    )
    probe_databases: ProbeDatabaseReclamationConfiguration = Field(
        default_factory=ProbeDatabaseReclamationConfiguration
    )
    #: Defaulted, and the default is the intended behaviour rather than a
    #: placeholder. `bootstrap_graph_configuration` merges the packaged file
    #: under an active release at *top-level* granularity, so a release cut
    #: before this key existed carries a `housekeeping` block that wins whole and
    #: this nested field falls back to the model default. That is safe here
    #: precisely because the default is what an operator would choose: the sweep
    #: is not an operator value, it is bookkeeping the lifecycle owes itself.
    order_line_reservations: OrderLineReservationReclamationConfiguration = Field(
        default_factory=OrderLineReservationReclamationConfiguration
    )
    #: Defaulted for the reason the block above is, and the default is likewise
    #: the intended behaviour rather than a placeholder: a release cut before
    #: this key existed carries a `housekeeping` block that wins whole at
    #: top-level granularity, and falling back to "settle lapsed interceptions"
    #: is what an operator would have chosen anyway.
    ai_interceptions: InterceptionExpiryConfiguration = Field(
        default_factory=InterceptionExpiryConfiguration
    )


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


class CopilotConfiguration(StrictConfigModel):
    """Which registered agent policy the Copilot's conversation turns are routed to.

    The frontend used to carry the answer as the literal `"order_discovery"`
    while the active schema keys the policy `order-discovery-agent`, so every
    turn 422'd with `ORDER_AGENT_OUT_OF_SCOPE`. The mapping belongs here because
    both sides of it are operator-owned: the agent policy is published in a
    schema release, and which agent answers the Copilot is a deployment
    decision.

    It is stated rather than inferred. "The only registered policy" would work
    today and become a silent misroute the moment a second agent is published,
    and it is exactly the kind of hidden convention that produced the literal
    this replaces.

    `None` -- the default, so a release cut before this block still loads -- is
    not a fallback to some other id. It is the honest "this deployment has not
    said", and every reader is expected to fail closed on it: the shell disables
    the composer and names the missing setting rather than guessing.
    """

    order_discovery_agent_id: NonBlank | None = None


class CredentialBindingConfiguration(StrictConfigModel):
    """One named credential whose value never enters graph configuration.

    `profile_key` is the identity: AI route bindings address a credential by it,
    and it is what survives into runtime routing. The value behind it comes from
    the process environment.

    `vault_reference` is now optional, and empty in every configuration this
    repository ships. It is retained for deployments that opt back into Vault
    with `PLATFORM_VAULT_ENABLED=true`, where it is the pointer the resolver
    dereferences at startup. With Vault off it is `None` and nothing reads it --
    which is why it may not be *required*: a release that had to name a Vault
    path in order to parse would make Vault mandatory again through the back
    door.
    """

    profile_key: NonBlank
    vault_reference: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=16,
                max_length=768,
                pattern=(
                    r"^vault://secret/production/[A-Za-z0-9_./-]+"
                    r"#[A-Za-z0-9_-]+(?:\?version=\d+)?$"
                ),
            ),
        ]
        | None
    ) = None
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
            raise ValueError("control-plane data sources require a credential binding")
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
    #: The reason and condition catalogues an associate selects a line from
    #: (plan sect. 12.4). Top-level rather than nested inside `return_policy`,
    #: and defaulted empty so a release cut before it still loads -- both
    #: decisions are argued in `SelectionVocabularyConfiguration`.
    selection_vocabulary: SelectionVocabularyConfiguration = Field(
        default_factory=SelectionVocabularyConfiguration
    )
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
    # Defaulted so an existing release without the block still loads. The
    # defaults are conservative windows, and the reclaimers they drive are
    # structurally unable to reach a live return whatever this block says.
    housekeeping: HousekeepingConfiguration = Field(default_factory=HousekeepingConfiguration)
    integrations: IntegrationConfiguration
    extensions: ExtensionConfiguration
    runtime_integrations: RuntimeIntegrationsConfiguration = Field(
        default_factory=RuntimeIntegrationsConfiguration
    )
    feature_flags: FeatureFlagsConfiguration = Field(default_factory=FeatureFlagsConfiguration)
    # Defaulted so a release cut before the block still loads. The default is
    # empty rather than a guessed agent id -- see `CopilotConfiguration`.
    copilot: CopilotConfiguration = Field(default_factory=CopilotConfiguration)
    #: The deterministic return eligibility rule set (`policy/`), versioned and
    #: released like every other section here.
    #:
    #: Optional for the same reason `copilot` is: a release cut before this block
    #: existed must still load, and `bootstrap_graph_configuration` merges the
    #: packaged file underneath an active release rather than over it. `None` is
    #: not a permissive default -- it is "this deployment has published no policy",
    #: and `validate_return_eligibility_policy` below refuses activation on it.
    #: Nothing may read `None` as "approve" or even as "review"; an absent policy
    #: is an operational failure, not an eligibility outcome.
    return_eligibility_policy: ReturnEligibilityPolicy | None = None

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


def validate_copilot_agent_binding(
    configuration: ReturnPlatformConfiguration,
    known_agent_policy_ids: Collection[str],
) -> str:
    """Resolve the configured Order Discovery agent against the active schema.

    Raises `ValueError` for an unset binding and for a dangling one -- an id
    naming a policy the active schema does not publish. Both are the same defect
    from the associate's seat, because both end the same way: `agent_policies.get`
    returns `None` in `order_discovery_activities` and the turn 422s with
    `ORDER_AGENT_OUT_OF_SCOPE`. Renaming the agent in a schema release without
    renaming the mapping is the realistic way to reintroduce it, which is why
    the reference is checked rather than assumed.

    Takes the policy ids rather than an `ActiveSchema` so the check stays
    importable from the configuration package, which holds no schema types.
    """
    configured = configuration.copilot.order_discovery_agent_id
    if configured is None:
        raise ValueError(
            "copilot.order_discovery_agent_id is not configured, so no Copilot turn can be routed"
        )
    known = set(known_agent_policy_ids)
    if configured not in known:
        raise ValueError(
            f"copilot.order_discovery_agent_id {configured!r} names no agent policy in the active "
            f"schema (published policies: {', '.join(sorted(known)) or 'none'})"
        )
    return configured


def validate_return_eligibility_policy(
    configuration: ReturnPlatformConfiguration,
) -> ReturnEligibilityPolicy:
    """The active eligibility policy, or a refusal to activate.

    Mirrors `validate_copilot_agent_binding`: the field is optional on the model
    so that stored payloads predating it still parse, and the requirement is
    enforced here, at activation, where a refusal is actionable.

    A missing policy must never degrade to `REVIEW_REQUIRED`. That would look
    like the evaluator working -- every return quietly queued for a human -- when
    in fact no rule set was published at all. The two are distinguishable and the
    plan requires them to stay so: malformed or absent policy refuses activation;
    a valid policy with missing facts is what yields `REVIEW_REQUIRED`.
    """
    policy = configuration.return_eligibility_policy
    if policy is None:
        raise ValueError(
            "return_eligibility_policy is not configured, so no return can be evaluated; "
            "publish a policy release rather than allowing every return to fall to review"
        )
    return policy


def build_return_method_requirement_table(
    configuration: ReturnPlatformConfiguration,
) -> ReturnMethodRequirementTable:
    """The operator's requirement table, as the projection consumes it.

    The one conversion from released configuration to
    `resolve_completion(..., requirements=)`. Validated already -- the release
    could not have loaded otherwise -- so this cannot be the place a bad table is
    discovered; it exists so that no caller has to know the wire shape, and so
    that "which table is production running?" has exactly one answer.
    """
    from return_platform.operations.case_projection.completion import (  # noqa: PLC0415
        ReturnMethodRequirementTable,
    )

    return ReturnMethodRequirementTable.model_validate(
        {
            "rows": [
                row.model_dump(mode="json")
                for row in configuration.return_policy.return_method_requirements
            ]
        }
    )


#: The configuration checks `/health/ready` reports and production startup
#: refuses on. Named individually rather than collapsed into one "configuration
#: is bad" flag: the two failures have different operators and different fixes.
ConfigurationCheck = Literal[
    "COPILOT_AGENT_BINDING",
    "RETURN_ELIGIBILITY_POLICY",
]

#: The wire code for each check. `COPILOT_AGENT_CONFIGURATION_INVALID` is the
#: same code the Copilot turn route and `/api/return-history` already 503 with,
#: so an operator reading a failed turn and an operator reading `/health/ready`
#: are reading one fact.
_CONFIGURATION_CHECK_CODES: dict[ConfigurationCheck, str] = {
    "COPILOT_AGENT_BINDING": "COPILOT_AGENT_CONFIGURATION_INVALID",
    "RETURN_ELIGIBILITY_POLICY": "RETURN_ELIGIBILITY_POLICY_MISSING",
}


class ConfigurationHealthFailure(StrictConfigModel):
    """One configuration check that did not pass.

    `RETURN_ELIGIBILITY_POLICY_MISSING` is an **operational** failure and is
    reported here, next to a dangling agent mapping, precisely so it can never be
    mistaken for `REVIEW_REQUIRED`. A review outcome is the evaluator working: a
    published rule set looked at a return and asked for a human. An absent policy
    is no rule set at all, and a platform that answered `REVIEW_REQUIRED` to it
    would queue every return to a human while looking healthy.
    """

    check: ConfigurationCheck
    code: NonBlank
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def evaluate_configuration_health(
    configuration: ReturnPlatformConfiguration,
    known_agent_policy_ids: Collection[str],
) -> tuple[ConfigurationHealthFailure, ...]:
    """Every configuration check, run together, each failure reported distinctly.

    One seam rather than a call site per validator. Both `validate_*` functions
    below raise on the first thing wrong, which is right for a request that is
    about to be refused and wrong for a health report: an operator fixing a
    dangling agent mapping should not have to redeploy to discover the eligibility
    policy is also missing. So they are run independently and their failures are
    collected.

    This is the only place that decides *what* configuration health means. The
    probe on `/health/ready` and the production startup gate both read it, which
    is what keeps the dev and production answers from drifting apart.
    """
    failures: list[ConfigurationHealthFailure] = []
    checks: tuple[tuple[ConfigurationCheck, Callable[[], object]], ...] = (
        (
            "COPILOT_AGENT_BINDING",
            lambda: validate_copilot_agent_binding(configuration, known_agent_policy_ids),
        ),
        (
            "RETURN_ELIGIBILITY_POLICY",
            lambda: validate_return_eligibility_policy(configuration),
        ),
    )
    for check, run in checks:
        try:
            run()
        except ValueError as invalid:
            failures.append(
                ConfigurationHealthFailure(
                    check=check,
                    code=_CONFIGURATION_CHECK_CODES[check],
                    message=str(invalid)[:500],
                )
            )
    return tuple(failures)


class ConfigurationInvalidError(RuntimeError):
    """Production refused to start on an invalid configuration."""

    def __init__(self, failures: Collection[ConfigurationHealthFailure]) -> None:
        self.failures = tuple(failures)
        super().__init__(
            "The active return configuration is invalid and this is production: "
            + "; ".join(f"{failure.code}: {failure.message}" for failure in self.failures)
        )


def require_healthy_configuration(
    configuration: ReturnPlatformConfiguration,
    known_agent_policy_ids: Collection[str],
    *,
    environment: str,
) -> tuple[ConfigurationHealthFailure, ...]:
    """Refuse to start in production; report the failures anywhere else.

    The split plan sect. 5.4 asks for, and it follows the precedent
    `Settings.validate_relationships` sets for development-default secrets:
    production is the
    environment where a missing prerequisite is a startup failure rather than a
    degraded mode, because there is nobody at the keyboard to read a warning and
    the alternative is serving returns nobody can route.

    It is not *in* `Settings.validate_relationships` because Settings holds
    neither the released return configuration nor the active schema's agent
    policies -- both arrive long after settings are constructed, inside the
    lifespan -- so the check has to run where they exist. What is copied exactly
    is the rule: `environment == "production"` refuses, everything else degrades
    visibly.

    Dev and CI get the failures back instead of an exception. The caller records
    them, `/health/ready` reports the configuration probe unhealthy, and the turn
    route 503s with `COPILOT_AGENT_CONFIGURATION_INVALID` -- an environment where
    a developer is mid-change must be able to boot and be *told*, not refuse to
    boot at all.
    """
    failures = evaluate_configuration_health(configuration, known_agent_policy_ids)
    if failures and environment == PRODUCTION_ENVIRONMENT:
        raise ConfigurationInvalidError(failures)
    return failures


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
