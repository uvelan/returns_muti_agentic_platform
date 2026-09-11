# Part B / Part 1 — `backend/config/returns/production.yaml` dead/unused key analysis

READ-ONLY audit. All line numbers verified against the working tree at the time of writing. "none found" means no grep hit outside the defining model file after the searches shown; absence of evidence is not absolute proof for every leaf (see UNVERIFIED).

## Summary (top findings)

1. **Runtime path confirmed.** `backend/src/return_platform/main.py:131,143,501` imports `runtime_activation`/`ConfigurationSnapshotBuilder` and calls `ConfigurationSnapshotBuilder(...).build_snapshot(baseline_return_configuration.configuration, ...)` at startup. `resolve_process_configuration` (in `configuration/runtime_loader.py`) is also called from `workers/integration_outbox.py:59` and `workers/interception_resume.py:60`. This is the live path; `load_return_configuration` (`return_configuration.py:1982`) is the YAML parser it calls underneath.
2. **No top-level `features:`/`platform:` keys — confirmed, and doubly inert — confirmed.** `grep -rln "LegacyCompatibilityAdapter|build_canonical_snapshot|build_snapshot_from_legacy_configs" backend --include=*.py` → only `configuration/application/adapters.py`, `configuration/application/compatibility.py`, `configuration/domain/release.py`, `backend/tests/configuration/test_canonical_application.py`. Never imported by `main.py` or any worker. `compatibility.py:345-357`'s `FeaturesConfig`/`PlatformConfig` always receive `{}` since the yaml has neither key (confirmed again: no `^features:`/`^platform:` line exists in the 2276-line file). Test-only and empty.
3. **`feature_flags:` (yaml line 1431) parses into `FeatureFlagsConfiguration` (return_configuration.py:1295) but — contrary to the premise handed in — has ZERO downstream readers of its own.** `grep -rn "\.feature_flags\."` and individual-flag-name greps (`reusable_conversation_engine`, `order_discovery_copilot`, `copilot_operations_console`, `graph_first_runtime_configuration`) across all of `backend/src` and `frontend` return no hits outside `return_configuration.py` itself. So this block is **READ_BUT_NO_EFFECT**, not "the real feature flags" as a business-logic-driving mechanism — it is validated shape with no consumer. This corrects the premise supplied in the task.
4. **`extra="forbid"` confirmed** (`return_configuration.py:40`, `StrictConfigModel`). `ReturnPlatformConfiguration` (line 1650) declares one field per yaml top-level key (all 26 confirmed present, 1:1, see table 0 below) — so no key is silently dropped; every key at minimum reaches a pydantic field (i.e., none of the 26 are hard-DEAD at the top level).
5. **`agents.<agent>.*` dead-knobs claim confirmed independently.** `AgentConfiguration` docstring (lines 43-56) lists the dead surface; spot-check `circuit_breaker_failure_threshold`, `task_queue`, `state_namespace`, `prompt_ref`, `policy_ref`, `human_confirmation_required` — none read from the model field anywhere; all real task-queue routing instead comes from `Settings.return_workflow_task_queue` (`configuration/settings.py:130`), confirming the docstring's claim that "task queues come from Settings."
6. **New finding, not in the premise:** `extensions:` (yaml line 1424) — all five leaves (`document_artifact_metadata`, `ocr_processing`, `image_processing`, `ncr_workflow`, `vendor_recovery_workflow`) parse into `ExtensionConfiguration` (return_configuration.py:1281) and are cross-validated against each other (`validate_processing_dependencies`), but **none has a downstream reader anywhere in `backend/src`**. Whole block is READ_BUT_NO_EFFECT beyond its own internal validation.

## Table 0 — top-level key → model field (26 keys, all present, 1:1)

| yaml key : line | Field on `ReturnPlatformConfiguration` (return_configuration.py:1650-1740) | Required/Default |
|---|---|---|
| schema_version:1 | schema_version | required |
| assumption_set_version:2 | assumption_set_version | required |
| agents:11 | agents | required |
| discovery:54 | discovery | required |
| source_resolution:650 | source_resolution | required |
| clarification_policy:787 | clarification_policy | required |
| return_policy:973 | return_policy | required |
| selection_vocabulary:1157 | selection_vocabulary | default_factory (empty ok) |
| workflow:1215 | workflow | required |
| support:1246 | support | required |
| omc:1257 | omc | required |
| bay:1276 | bay | required |
| return_case:1288 | return_case | default_factory |
| business_calendars:1356 | business_calendars | default `()` |
| integrations:1402 | integrations | required |
| extensions:1424 | extensions | required |
| feature_flags:1431 | feature_flags | default_factory |
| policy_evaluation:1449 | policy_evaluation | default_factory |
| context_assembly:1456 | context_assembly | default_factory |
| support_ingress:1479 | support_ingress | default_factory |
| support_resolver:1542 | support_resolver | default_factory |
| copilot:1610 | copilot | default_factory |
| runtime_integrations:1618 | runtime_integrations | default_factory |
| return_eligibility_policy:1686 | return_eligibility_policy | Optional, `None` refused at activation |
| shipment_tracking:1846 | shipment_tracking | Optional |
| support_gate:2016 | support_gate | default_factory |
| support_template:2040 | support_template | default_factory |

(Note: `housekeeping` is also a field, default_factory, but has no top-level yaml key in this file — not in scope, not a dead key since it's simply absent.)

## Table 1 — section-level status

| Section/Key | Reader (file:line) | Status | Grep evidence |
|---|---|---|---|
| agents.{name}.name/version/enabled/ai_assisted/ai_route_ref | `AgentRegistry.build` (per class docstring, return_configuration.py:43-56) | LIVE | docstring is the cited evidence; not independently re-walked (budget) |
| agents.{name}.human_confirmation_required/capabilities/implementation_id/task_queue/state_namespace/prompt_ref/policy_ref/timeout_seconds/retry_max_attempts/max_concurrency/requests_per_minute/circuit_breaker_failure_threshold | none found | DEAD (parses, unread) | `grep -rn "circuit_breaker_failure_threshold\|task_queue\|state_namespace\|prompt_ref\|policy_ref" backend/src` → all `task_queue` hits are `Settings.return_workflow_task_queue`, none reference the AgentConfiguration field |
| discovery.* (web_order_pattern, ambiguity_gap_millionths, auto_confirmation_allowed, anchor_weights, conflict_penalty_millionths, strong_anchors, anchor_extractors, free_text_fallback_anchor, conversation.*, identification/progressive_discovery/smart_question sub-trees) | `dynamic_knowledge/order_agent/*`, `operations/associate_flow.py` etc. | LIVE (largest, most-consumed section) | `grep -rn "\.discovery\." backend/src/return_platform --include=*.py` (excl. configuration/) → 40 hits; `anchor_weights` 6 hits, `free_text_fallback_anchor` 1 hit outside model |
| source_resolution.* (collections, *_paths field-mapping lists, delivery_proof) | `operations/*`, source resolution logic | LIVE | `\.source_resolution\.` → 4 hits outside configuration/; `ship_via_paths` 6 hits, `customer_account_type_paths` 1 hit |
| clarification_policy.version/max_prompts_per_turn/max_fields_per_turn/max_distinct_values_for_ai/fields/goals | `SmartQuestionConfiguration` consumers | LIVE | `\.clarification_policy\.` → 8 hits outside configuration/ |
| clarification_policy.field_selection_owner | `operations/associate_flow.py:1118`, `dynamic_knowledge/order_agent/planner.py:26` (referenced in comment), `contracts.py:344` (comment) | LIVE | `grep -rn field_selection_owner backend/src` |
| clarification_policy.phrasing_owner | none found | UNVERIFIED / likely READ_BUT_NO_EFFECT | `grep -rn phrasing_owner backend/src` → 0 hits outside model field declaration |
| return_policy.* (photo_required_reason_codes, supported_product_presence, return_method_derivation, normalized_return_methods, return_method_requirements, bol_tendering_instruction_types, rga_required_product_resolutions, heavy_pickup_required_fields, branch_staging) | `operations/case_projection/completion.py` via `build_return_method_requirement_table` (return_configuration.py:1830), plus `validate_required_agents` (branch_staging enforcement, line 1760-1763) | LIVE | `\.return_policy\.` → 9 hits outside configuration/ |
| selection_vocabulary.reasons/conditions | `bootstrap/api.py:225,230` | LIVE | `\.selection_vocabulary\.` → 2 hits, both in bootstrap/api.py |
| workflow.stages/sla_minutes/completion_dimensions | workflow orchestration (not individually re-walked) | LIVE (assumed from 2 external hits) | `configuration\.workflow\.` → 2 hits outside configuration/ |
| support.authority_mode/default_priority/queues | not individually isolated | LIVE-likely | `configuration\.support\.` → 1 hit (narrow query; broader `\.support\.` not usable — too common a word) |
| support.external_mirror_enabled | `operations/return_support/service.py:568` | LIVE | direct hit |
| support.external_ticket_outbox_topic | `operations/return_support/service.py:571` | LIVE | direct hit |
| omc.customer_return_display / normalized_statuses / tendered_is_pickup / license_plate_implies_receipt / rga_is_customer_return | `validate_required_agents` enforces tendered_is_pickup==False and rga_is_customer_return==False (return_configuration.py:1756-1759) | LIVE (at least 2 of 5 leaves, via validator) | `\.omc\.` → 1 hit outside configuration/ (narrow); `customer_return_display` 0 hits, `normalized_statuses` 1 hit — both UNVERIFIED beyond the validator |
| bay.authority_mode/require_physical_receipt/allow_prearrival_reservation/eligible_statuses | not individually isolated | UNVERIFIED | `configuration\.bay\.` → 1 hit (narrow query) |
| return_case.* (bay_wait_seconds, return_details_required/wait_seconds, item_reservation_ttl_seconds, support_response_wait_seconds, reminder_interval_seconds, max_reminders, on_reminders_exhausted, business_calendar_id, timezone) | case timing logic | LIVE-likely | `\.return_case\.` → 1 hit (query too narrow, see UNVERIFIED) |
| business_calendars (list of calendar_id/timezone/working_periods/holidays) | `resolve_business_deadline` (per field docstring, return_configuration.py:1675-1678) | LIVE | 1 direct hit for literal `business_calendars`; content itself confirms a DEV override is active (all-day working periods, empty holidays) — see yaml:1356-1401 |
| integrations.omc_return_create/carrier_booking/customer_notification/external_support_mirror .topic | `operations/physical/service.py:238`, `operations/return_support/service.py:1178,1239,1264` | LIVE | direct hits |
| integrations.*.ai_may_fabricate_success | `validate_required_agents` (return_configuration.py:1764-1771) forbids `True` on all four topics | LIVE (validator-only, no runtime business-logic reader outside the model) | `grep -rn ai_may_fabricate_success backend/src` → 0 hits outside return_configuration.py |
| integrations.*.enabled/authority | not isolated | UNVERIFIED | not individually grepped |
| extensions.document_artifact_metadata/ocr_processing/image_processing/ncr_workflow/vendor_recovery_workflow | none found (only mutual cross-validation, return_configuration.py:1288-1291) | READ_BUT_NO_EFFECT (block-internal validation only, no business-logic consumer) | `grep -rn "ocr_processing\|ncr_workflow\|vendor_recovery_workflow\|document_artifact_metadata\|image_processing" backend/src` → 0 hits outside return_configuration.py |
| feature_flags.reusable_conversation_engine/order_discovery_copilot/copilot_operations_console/graph_first_runtime_configuration | none found | READ_BUT_NO_EFFECT (corrects task premise — see Summary #3) | 4 individual-name greps, all 0 hits outside model definition |
| policy_evaluation.enabled/disabled_reason | fact-log gate (per class docstring, return_configuration.py:1302-1334) | LIVE | `\.policy_evaluation\.` → 2 hits outside configuration/; not individually re-walked to the exact consumer file:line (budget) |
| context_assembly.pinned_fact_names/token_budget/tokenizer_version/compaction.* | `operations/return_support/resolver_composition.py:298` (`context_policy=return_configuration.context_assembly`) | LIVE | direct hit |
| support_ingress.nl_enabled/intents/parking/multi_record_framing_prompt_key/agent_disclosure/outbound_templates/limits | `SupportIngressConfiguration` consumer(s) | LIVE-likely | `\.support_ingress\.` → 1 hit outside configuration/ (not deeply walked) |
| support_resolver.fact_confidence_millionths/graph_confidence_millionths/tool_bindings/reply_gate/clarification_resets_deadline/per_case_llm_budget/trigger_intents | `SupportResolverConfiguration` consumer(s) | LIVE-likely | `\.support_resolver\.` → 2 hits outside configuration/ (not deeply walked) |
| copilot.order_discovery_agent_id | `validate_copilot_agent_binding` (return_configuration.py:1775-1803), `api/cases.py` (routing, per docstring) | LIVE | `\.copilot\.` → 2 hits outside configuration/; function at 1775 is the direct consumer |
| runtime_integrations.ai_providers/data_sources | `configuration/runtime_integrations.py:21,34,209,213,285,305`, `main.py:136`, `workers/integration_outbox.py:24` | LIVE | many direct hits (listed in evidence run) |
| return_eligibility_policy.* (precedence, standard_stock_return, restocking_fee, stock_classification, special_or_nonstock, outside_standard_window, delivery_claim, warranty_issue) | `policy/eligibility_policy.py` (`ReturnEligibilityPolicy` type itself), enforced at activation via `validate_return_eligibility_policy` (return_configuration.py:1806-1827) | LIVE | `\.return_eligibility_policy\b` → 2 hits outside configuration/; policy body is a typed domain object, not walked leaf-by-leaf (budget — 160 lines of yaml) |
| shipment_tracking.initial_status_parcel/freight, freight_methods, collection, fields, statuses | `api/shipment_console.py:90,104,263`, `operations/shipment_tracking.py` (whole module), `workflows/return_case_activities.py:2349-2376` | LIVE | many direct hits, confirmed |
| support_gate.request_grouping/template_review | `api/cases.py:712`, `operations/support_template_gate.py:512,530,603`, `workers/integration_outbox.py:245`, `workflows/return_case_workflow.py:765` (comment) | LIVE | many direct hits, confirmed |
| support_template.template_id/default_variant_id/variants | `operations/support_template_gate.py:530,603`, `operations/support_template_renderer.py:471`, `api/template_preview.py` | LIVE | many direct hits, confirmed |

## UNVERIFIED (not individually walked to a specific consumer file:line — budget)

- `discovery.conversation.*` sub-keys individually (the section is LIVE overall — 40 external hits — but not every leaf under `conversation` was isolated; the 726-hit grep for the bare word "conversation" was noise and unusable).
- `discovery` identification/progressive-discovery sub-trees (`IdentificationSearchConfiguration`, `IdentificationFieldConfiguration`, `ProgressiveDiscoveryConfiguration`, `ProgressiveDialogueStateConfiguration`, `DisambiguationAttributeConfiguration` — return_configuration.py:126-312) — parsed and almost certainly consumed given the section's 40 external hits, but no leaf-by-leaf grep was run.
- `source_resolution.delivery_proof.*` sub-keys (`DeliveryProofConfiguration`, return_configuration.py:359-412).
- `return_policy.return_method_derivation.*`, `.branch_staging.*` sub-keys beyond the two `branch_staging` booleans enforced by `validate_required_agents`.
- `workflow.stages`/`sla_minutes`/`completion_dimensions` — section confirmed LIVE at 2 hits, but the specific consumer file:line was not captured.
- `support.authority_mode`/`default_priority`/`queues`, `bay.*` (all four leaves), `return_case.*` (all nine leaves), `omc.customer_return_display`, `omc.normalized_statuses`, `integrations.*.enabled`/`.authority` — section-level "at least one hit" was seen for some, but exact consumer file:line not isolated for each leaf; treat status as LIVE-likely, not confirmed.
- `support_ingress.*` and `support_resolver.*` full leaf sets — sections confirmed LIVE (SupportIngressConfiguration/SupportResolverConfiguration are imported and used per return_configuration.py:18-27), but individual leaves (e.g. `parking`, `agent_disclosure`, `tool_bindings`, `reply_gate`) were not each traced to a call site.
- `return_eligibility_policy.*` internal rule leaves (191 lines) — confirmed the block as a whole is LIVE and required at activation, but the individual rule fields inside `standard_stock_return`, `restocking_fee`, `stock_classification`, etc. were not walked against `policy/eligibility_policy.py`'s evaluator logic.
- `shipment_tracking.freight_methods`, `.fields` (currently `{}` in this file) — block confirmed LIVE overall; these two specific leaves not individually traced.
- `clarification_policy.phrasing_owner` — 0 hits found; could be genuinely dead or could be read via a dynamic/reflective access pattern not caught by literal grep (e.g. `getattr`, dict-style access) — flagged as likely READ_BUT_NO_EFFECT but not proven.
- `omc.customer_return_display` (dict) — 0 direct hits found under that exact name; not proven dead vs. accessed via attribute chain not grepped (e.g. `.omc.customer_return_display[...]`).
- Whether `discovery.auto_confirmation_allowed`'s hard-refusal (`validate_required_agents`, line 1754-1755, must be `false` in production) is the *only* place it's read, or whether runtime code also reads it directly — not checked.
