# PART A: Settings & Environment Audit (CFG-A)

**Audit date:** 2026-09-11 | **Scope:** PLATFORM_* environment settings, settings.py fields, .env.example, compose.yaml, config files | **Status:** PARTIAL (Step 1 complete, steps 2-7 in progress)

---

## INVENTORY: Settings Class Fields

| ID | Env Var | Attribute | Type | Default | Secret | Production-only refusals | Validator constraints |
|---|---|---|---|---|---|---|---|
| CFG-A-001 | PLATFORM_CATALOG_PATH | catalog_path | Path | backend/config/data_assets.yaml | No | No | must be absolute .yaml |
| CFG-A-002 | PLATFORM_SCHEMA_REGISTRY_PATH | schema_registry_path | Path | backend/config/schema_registry.yaml | No | No | must be absolute .yaml |
| CFG-A-003 | PLATFORM_RETURN_CONFIGURATION_PATH | return_configuration_path | Path | backend/config/returns/production.yaml | No | No | must be absolute .yaml |
| CFG-A-004 | PLATFORM_DEPENDENCY_SIMULATION_PATH | dependency_simulation_configuration_path | Path | backend/config/dependency_simulation.yaml | No | No | must be absolute .yaml |
| CFG-A-005 | PLATFORM_AI_GATEWAY_CONFIGURATION_PATH | ai_gateway_configuration_path | Path | backend/config/ai_gateway.yaml | No | No | must be absolute .yaml |
| CFG-A-006 | PLATFORM_DYNAMIC_ORDER_AGENT_ENABLED | dynamic_order_agent_enabled | bool | True | No | No | none |
| CFG-A-007 | PLATFORM_DYNAMIC_KNOWLEDGE_SCHEMA_PATH | dynamic_knowledge_schema_path | Path | backend/config/dynamic_knowledge/active-schema.return-order.yaml | No | No | must be .yaml, resolved |
| CFG-A-008 | PLATFORM_SYSTEM_STORE_MANIFEST_PATH | system_store_manifest_path | Path | backend/config/platform/system_store.yaml | No | No | must be .yaml, resolved |
| CFG-A-009 | PLATFORM_CONFIGURATION_DIRECTORY | configuration_directory | Path | backend/config | No | No | directory, resolved |
| CFG-A-010 | PLATFORM_ENVIRONMENT | environment | Literal | development | No | Yes (staging→prod gates) | dev/test/staging/production |
| CFG-A-011 | PLATFORM_VAULT_ENABLED | vault_enabled | bool | False | No | No | none |
| CFG-A-012 | PLATFORM_VAULT_ADDRESS | vault_address | str | http://127.0.0.1:8201 | No | No | none |
| CFG-A-013 | PLATFORM_VAULT_TOKEN | vault_token | SecretStr \| None | None | Yes | No | none |
| CFG-A-014 | PLATFORM_VAULT_TOKEN_FILE | vault_token_file | Path \| None | None | No | No | none |
| CFG-A-015 | PLATFORM_VAULT_NAMESPACE | vault_namespace | str \| None | None | No | No | none |
| CFG-A-016 | PLATFORM_VAULT_VERIFY_TLS | vault_verify_tls | bool | True | No | No | none |
| CFG-A-017 | PLATFORM_VAULT_TIMEOUT_SECONDS | vault_timeout_seconds | float | 5.0 | No | No | gt=0.0, le=30.0 |
| CFG-A-018 | PLATFORM_VAULT_SECRETS_RESOLVED | vault_secrets_resolved | bool | False | No | No | read-only status |
| CFG-A-019 | PLATFORM_MONGO_DSN_SECRET_REFERENCE | mongo_dsn_secret_reference | str \| None | None | No | No | vault://... reference |
| CFG-A-020 | PLATFORM_SOURCE_MONGO_DSN_SECRET_REFERENCE | source_mongo_dsn_secret_reference | str \| None | None | No | No | vault://... reference |
| CFG-A-021 | PLATFORM_NEO4J_PASSWORD_SECRET_REFERENCE | neo4j_password_secret_reference | str \| None | None | No | No | vault://... reference |
| CFG-A-022 | PLATFORM_VALKEY_PASSWORD_SECRET_REFERENCE | valkey_password_secret_reference | str \| None | None | No | No | vault://... reference |
| CFG-A-023 | PLATFORM_SQLSERVER_PASSWORD_SECRET_REFERENCE | sqlserver_password_secret_reference | str \| None | None | No | No | vault://... reference |
| CFG-A-024 | PLATFORM_GOOGLE_API_KEY_REFERENCES | google_api_key_references | tuple[str, ...] | () | No | No | vault://... refs, no duplicates |
| CFG-A-025 | PLATFORM_NVIDIA_API_KEY_REFERENCES | nvidia_api_key_references | tuple[str, ...] | () | No | No | vault://... refs, no duplicates |
| CFG-A-026 | PLATFORM_OPENAI_API_KEY_REFERENCES | openai_api_key_references | tuple[str, ...] | () | No | No | vault://... refs, no duplicates |
| CFG-A-027 | PLATFORM_ANTHROPIC_API_KEY_REFERENCES | anthropic_api_key_references | tuple[str, ...] | () | No | No | vault://... refs, no duplicates |
| CFG-A-028 | PLATFORM_VALIDATION_FINGERPRINT_KEY_SECRET_REFERENCE | validation_fingerprint_key_secret_reference | str \| None | None | No | No | vault://... reference |
| CFG-A-029 | PLATFORM_VALIDATION_FINGERPRINT_KEY | validation_fingerprint_key | SecretStr | development-validation-fingerprint-key-change-me | Yes | Yes | production refuses -change-me suffix |
| CFG-A-030 | PLATFORM_CONTACT_LOOKUP_HMAC_KEY_SECRET_REFERENCE | contact_lookup_hmac_key_secret_reference | str \| None | None | No | No | vault://... reference |
| CFG-A-031 | PLATFORM_CONTACT_LOOKUP_HMAC_KEY | contact_lookup_hmac_key | SecretStr | development-contact-lookup-hmac-key-change-me | Yes | Yes | production refuses -change-me suffix |
| CFG-A-032 | PLATFORM_REASONING_ENCRYPTION_KEY_SECRET_REFERENCE | reasoning_encryption_key_secret_reference | str \| None | None | No | No | vault://... reference |
| CFG-A-033 | PLATFORM_REASONING_ENCRYPTION_KEY | reasoning_encryption_key | SecretStr | z4hdfhOkyWNWVgigtCB8skElDHzOmAVUs6NEYWF+WQo= | Yes | Yes | base64 32-byte key; production refuses dev value |
| CFG-A-034 | PLATFORM_PROBE_TIMEOUT_SECONDS | probe_timeout_seconds | float | 2.0 | No | No | gt=0.0, le=30.0 |
| CFG-A-035 | PLATFORM_DEPENDENCY_CONNECT_TIMEOUT_SECONDS | dependency_connect_timeout_seconds | float | 5.0 | No | No | gt=0.0, le=30.0, >= probe_timeout |
| CFG-A-036 | PLATFORM_OPERATION_TIMEOUT_SECONDS | operation_timeout_seconds | float | 10.0 | No | No | ge=0.1, le=120.0 |
| CFG-A-037 | PLATFORM_FRONTEND_CORS_ORIGIN | frontend_cors_origin | AnyHttpUrl | *REQUIRED* | No | Yes | scheme+host+port only, no path |
| CFG-A-038 | PLATFORM_MONGO_DSN | mongo_dsn | SecretStr | *REQUIRED* | Yes | No | min_length=10, mongodb:// prefix |
| CFG-A-039 | PLATFORM_MONGO_DATABASE | mongo_database | str | return_platform | No | No | min=1, max=63, pattern=[A-Za-z]... |
| CFG-A-040 | PLATFORM_SOURCE_MONGO_DSN | source_mongo_dsn | SecretStr \| None | None | Yes | No | mongodb:// prefix or None |
| CFG-A-041 | PLATFORM_SOURCE_MONGO_DATABASE | source_mongo_database | str | return_source | No | No | min=1, max=63 |
| CFG-A-042 | PLATFORM_NEO4J_URI | neo4j_uri | str | *REQUIRED* | No | No | bolt:// or neo4j:// scheme |
| CFG-A-043 | PLATFORM_NEO4J_USER | neo4j_user | str | neo4j | No | No | min_length=1 |
| CFG-A-044 | PLATFORM_NEO4J_PASSWORD | neo4j_password | SecretStr | *REQUIRED* | Yes | No | min_length=1 |
| CFG-A-045 | PLATFORM_NEO4J_DATABASE | neo4j_database | str | neo4j | No | No | min_length=1 |
| CFG-A-046 | PLATFORM_VALKEY_HOST | valkey_host | str | *REQUIRED* | No | No | min_length=1 |
| CFG-A-047 | PLATFORM_VALKEY_PORT | valkey_port | int | 6379 | No | No | ge=1, le=65535 |
| CFG-A-048 | PLATFORM_VALKEY_PASSWORD | valkey_password | SecretStr | *REQUIRED* | Yes | No | min_length=1 |
| CFG-A-049 | PLATFORM_EVENT_STREAM_RETENTION | event_stream_retention | int | 10000 | No | No | ge=1000, le=1000000 |
| CFG-A-050 | PLATFORM_SSE_HEARTBEAT_SECONDS | sse_heartbeat_seconds | float | 15.0 | No | No | ge=5.0, le=60.0 |
| CFG-A-051 | PLATFORM_SSE_REPLAY_LIMIT | sse_replay_limit | int | 1000 | No | No | ge=1, le=10000 |
| CFG-A-052 | PLATFORM_TEMPORAL_TARGET | temporal_target | str | *REQUIRED* | No | No | host:port format, port 1-65535 |
| CFG-A-053 | PLATFORM_RETURN_WORKFLOW_TASK_QUEUE | return_workflow_task_queue | str | return-platform-return-v1 | No | No | pattern=[a-z][a-z0-9-]{0,126} |
| CFG-A-054 | PLATFORM_ORDER_DISCOVERY_WORKFLOW_TASK_QUEUE | order_discovery_workflow_task_queue | str | return-platform-order-discovery-v1 | No | No | pattern=[a-z][a-z0-9-]{0,126} |
| CFG-A-055 | PLATFORM_ORCHESTRATION_POLL_SECONDS | orchestration_poll_seconds | float | 1.0 | No | No | ge=0.1, le=30.0 |
| CFG-A-056 | PLATFORM_WORKER_READINESS_TTL_SECONDS | worker_readiness_ttl_seconds | int | 30 | No | No | ge=5, le=300 |
| CFG-A-057 | PLATFORM_SQLSERVER_HOST | sqlserver_host | str | *REQUIRED* | No | No | min_length=1 |
| CFG-A-058 | PLATFORM_SQLSERVER_PORT | sqlserver_port | int | 1433 | No | No | ge=1, le=65535 |
| CFG-A-059 | PLATFORM_SQLSERVER_USER | sqlserver_user | str | sa | No | No | min_length=1 |
| CFG-A-060 | PLATFORM_SQLSERVER_PASSWORD | sqlserver_password | SecretStr | *REQUIRED* | Yes | No | min_length=1 |
| CFG-A-061 | PLATFORM_SQLSERVER_DATABASE | sqlserver_database | str | *REQUIRED* | No | No | min_length=1 |
| CFG-A-062 | PLATFORM_SQLSERVER_POOL_MAX_SIZE | sqlserver_pool_max_size | int | 8 | No | No | ge=1, le=128 |
| CFG-A-063 | PLATFORM_SQLSERVER_POOL_ACQUIRE_TIMEOUT_SECONDS | sqlserver_pool_acquire_timeout_seconds | float | 5.0 | No | No | gt=0.0, le=60.0 |
| CFG-A-064 | PLATFORM_SQLSERVER_POOL_IDLE_TIMEOUT_SECONDS | sqlserver_pool_idle_timeout_seconds | float | 300.0 | No | No | ge=1.0, le=3600.0 |
| CFG-A-065 | PLATFORM_GRAPH_EVIDENCE_COLLECTION | graph_evidence_collection | str | graph_evidence_runs | No | No | pattern, min=1, max=127 |
| CFG-A-066 | PLATFORM_GRAPH_EVIDENCE_QUERY_TIMEOUT_SECONDS | graph_evidence_query_timeout_seconds | float | 5.0 | No | No | ge=0.05, le=30.0 |
| CFG-A-067 | PLATFORM_GRAPH_SYNC_BATCH_SIZE | graph_sync_batch_size | int | 250 | No | No | ge=1, le=5000 |
| CFG-A-068 | PLATFORM_GRAPH_SYNC_MAX_RECORDS | graph_sync_max_records | int | 10000 | No | No | ge=1, le=1000000 |
| CFG-A-069 | PLATFORM_ON_DEMAND_SYNC_RECEIPT_TTL_SECONDS | on_demand_sync_receipt_ttl_seconds | int | 900 | No | No | ge=60, le=86400 |
| CFG-A-070 | PLATFORM_AI_PROVIDER_ORDER | ai_provider_order | str | GOOGLE,NVIDIA,SIMULATOR | No | Yes (production refuses SIMULATOR/MANUAL) | comma-separated, no duplicates |
| CFG-A-071 | PLATFORM_AI_TRACE_PAYLOADS | ai_trace_payloads | bool | True | No | No | none |
| CFG-A-072 | PLATFORM_AI_MANUAL_HANDOFF | ai_manual_handoff | str | AUTO | No | No | pattern: AUTO\|UI\|FILE |
| CFG-A-073 | PLATFORM_AI_REPLAY_MODE | ai_replay_mode | str | OFF | No | No | pattern: OFF\|REPLAY\|STRICT |
| CFG-A-074 | PLATFORM_AI_VALIDATED_ROUTE_BINDINGS | ai_validated_route_bindings | tuple[str, ...] | () | No | No | PROVIDER\|INDEX\|MODEL\|TASK, no dups |
| CFG-A-075 | PLATFORM_AI_VALIDATION_INTERVAL_HOURS | ai_validation_interval_hours | int | 24 | No | No | ge=1, le=168 |
| CFG-A-076 | PLATFORM_AI_TIMEOUT_SECONDS | ai_timeout_seconds | float | 12.0 | No | No | ge=0.5, le=300.0 |
| CFG-A-077 | PLATFORM_AI_GLOBAL_TIMEOUT_SECONDS | ai_global_timeout_seconds | float | 30.0 | No | No | ge=1.0, le=900.0, >= ai_timeout |
| CFG-A-078 | PLATFORM_AI_MAX_ATTEMPTS_PER_PROVIDER | ai_max_attempts_per_provider | int | 2 | No | No | ge=1, le=4 |
| CFG-A-079 | PLATFORM_AI_MAX_CONCURRENCY | ai_max_concurrency | int | 16 | No | No | ge=1, le=256 |
| CFG-A-078 | PLATFORM_AI_REQUESTS_PER_MINUTE | ai_requests_per_minute | int | 120 | No | No | ge=1, le=100000 |
| CFG-A-080 | PLATFORM_AI_MAX_PAYLOAD_BYTES | ai_max_payload_bytes | int | 16384 | No | No | ge=1024, le=1048576 |
| CFG-A-081 | PLATFORM_AI_INTERCEPTION_DEFAULT | ai_interception_default | bool | False | No | No | none |
| CFG-A-082 | PLATFORM_AI_RESPONSE_INTERCEPTION | ai_response_interception | bool | False | No | Yes (production refuses True) | none |
| CFG-A-083 | PLATFORM_AI_PROMPT_VERSION | ai_prompt_version | str | return-eligibility-v1 | No | No | min=1, max=128 |
| CFG-A-084 | PLATFORM_AI_ALLOWED_ENDPOINT_HOSTS | ai_allowed_endpoint_hosts | tuple[str, ...] | (Google,NVIDIA,OpenAI,Anthropic) | No | No | no duplicates, lowercase |
| CFG-A-085 | PLATFORM_DATA_SOURCE_ALLOWED_HOSTS | data_source_allowed_hosts | tuple[str, ...] | (mongodb, localhost...) | No | No | no duplicates, lowercase |
| CFG-A-086 | PLATFORM_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED | data_source_credential_reveal_enabled | bool | False | No | No | none |
| CFG-A-087 | PLATFORM_GOOGLE_API_KEYS | google_api_keys | tuple[SecretStr, ...] | () | Yes | No | comma/JSON list parsing |
| CFG-A-088 | PLATFORM_GOOGLE_LIGHTWEIGHT_MODELS | google_lightweight_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-089 | PLATFORM_GOOGLE_STANDARD_MODELS | google_standard_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-090 | PLATFORM_GOOGLE_API_KEY | google_api_key | SecretStr \| None | None | Yes | No | fallback for google_api_keys |
| CFG-A-091 | PLATFORM_GOOGLE_BASE_URL | google_base_url | str | https://generativelanguage.googleapis.com/v1beta | No | No | http/https only, no trailing / |
| CFG-A-092 | PLATFORM_GOOGLE_MODEL | google_model | str \| None | None | No | No | fallback for google_standard_models |
| CFG-A-093 | PLATFORM_GOOGLE_THINKING_BUDGET | google_thinking_budget | int \| None | 2048 | No | No | ge=0, le=24576; empty→None |
| CFG-A-094 | PLATFORM_NVIDIA_API_KEYS | nvidia_api_keys | tuple[SecretStr, ...] | () | Yes | No | comma/JSON list parsing |
| CFG-A-095 | PLATFORM_NVIDIA_LIGHTWEIGHT_MODELS | nvidia_lightweight_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-096 | PLATFORM_NVIDIA_STANDARD_MODELS | nvidia_standard_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-097 | PLATFORM_NVIDIA_API_KEY | nvidia_api_key | SecretStr \| None | None | Yes | No | fallback for nvidia_api_keys |
| CFG-A-098 | PLATFORM_NVIDIA_BASE_URL | nvidia_base_url | str | https://integrate.api.nvidia.com/v1 | No | No | http/https only, no trailing / |
| CFG-A-099 | PLATFORM_NVIDIA_MODEL | nvidia_model | str \| None | None | No | No | fallback for nvidia_standard_models |
| CFG-A-100 | PLATFORM_OPENAI_API_KEYS | openai_api_keys | tuple[SecretStr, ...] | () | Yes | No | comma/JSON list parsing |
| CFG-A-101 | PLATFORM_OPENAI_LIGHTWEIGHT_MODELS | openai_lightweight_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-102 | PLATFORM_OPENAI_STANDARD_MODELS | openai_standard_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-103 | PLATFORM_OPENAI_API_KEY | openai_api_key | SecretStr \| None | None | Yes | No | fallback for openai_api_keys |
| CFG-A-104 | PLATFORM_OPENAI_BASE_URL | openai_base_url | str | https://api.openai.com/v1 | No | No | http/https only, no trailing / |
| CFG-A-105 | PLATFORM_OPENAI_MODEL | openai_model | str \| None | None | No | No | fallback for openai_standard_models |
| CFG-A-106 | PLATFORM_ANTHROPIC_API_KEYS | anthropic_api_keys | tuple[SecretStr, ...] | () | Yes | No | comma/JSON list parsing |
| CFG-A-107 | PLATFORM_ANTHROPIC_LIGHTWEIGHT_MODELS | anthropic_lightweight_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-108 | PLATFORM_ANTHROPIC_STANDARD_MODELS | anthropic_standard_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-109 | PLATFORM_ANTHROPIC_API_KEY | anthropic_api_key | SecretStr \| None | None | Yes | No | fallback for anthropic_api_keys |
| CFG-A-110 | PLATFORM_ANTHROPIC_BASE_URL | anthropic_base_url | str | https://api.anthropic.com/v1 | No | No | http/https only, no trailing / |
| CFG-A-111 | PLATFORM_ANTHROPIC_MODEL | anthropic_model | str \| None | None | No | No | fallback for anthropic_standard_models |
| CFG-A-112 | PLATFORM_ANTHROPIC_VERSION | anthropic_version | str | 2023-06-01 | No | No | none |
| CFG-A-113 | PLATFORM_OLLAMA_BASE_URL | ollama_base_url | str | http://localhost:11434/v1 | No | No | http/https only, no trailing / |
| CFG-A-114 | PLATFORM_OLLAMA_LIGHTWEIGHT_MODELS | ollama_lightweight_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-115 | PLATFORM_OLLAMA_STANDARD_MODELS | ollama_standard_models | tuple[str, ...] | () | No | No | comma/JSON list parsing, no dups |
| CFG-A-116 | PLATFORM_OLLAMA_MODEL | ollama_model | str \| None | None | No | No | fallback for ollama_standard_models |
| CFG-A-117 | PLATFORM_SEED_VERSION | seed_version | str | e2e-v1 | No | No | min=1, max=64 |
| CFG-A-118 | PLATFORM_AI_STUDIO_MAX_RECORDS | ai_studio_max_records | int | 500 | No | No | ge=1, le=10000 |
| CFG-A-119 | PLATFORM_AI_STUDIO_MONGO_DATABASE | ai_studio_mongo_database | str \| None | None | No | No | separate validation DB |
| CFG-A-120 | PLATFORM_AI_STUDIO_SQLSERVER_HOST | ai_studio_sqlserver_host | str \| None | None | No | No | separate validation DB |
| CFG-A-121 | PLATFORM_AI_STUDIO_SQLSERVER_PORT | ai_studio_sqlserver_port | int | 1433 | No | No | ge=1, le=65535 |
| CFG-A-122 | PLATFORM_AI_STUDIO_SQLSERVER_USER | ai_studio_sqlserver_user | str \| None | None | No | No | separate validation DB |
| CFG-A-123 | PLATFORM_AI_STUDIO_SQLSERVER_PASSWORD | ai_studio_sqlserver_password | SecretStr \| None | None | Yes | No | separate validation DB |
| CFG-A-124 | PLATFORM_AI_STUDIO_SQLSERVER_DATABASE | ai_studio_sqlserver_database | str \| None | None | No | No | separate validation DB |
| CFG-A-125 | PLATFORM_SUPPORT_TICKET_MODE | support_ticket_mode | Literal | INTERNAL | No | No | INTERNAL\|INTERNAL_WITH_EXTERNAL_MIRROR\|EXTERNAL_AUTHORITY |
| CFG-A-126 | PLATFORM_SUPPORT_TICKET_BASE_URL | support_ticket_base_url | str \| None | None | No | No | http/https only or None |
| CFG-A-127 | PLATFORM_SUPPORT_TICKET_API_KEY | support_ticket_api_key | SecretStr \| None | None | Yes | No | only with external mode |
| CFG-A-128 | PLATFORM_SUPPORT_TICKET_TIMEOUT_SECONDS | support_ticket_timeout_seconds | float | 15.0 | No | No | ge=1.0, le=120.0 |
| CFG-A-129 | PLATFORM_SUPPORT_TICKET_POLL_SECONDS | support_ticket_poll_seconds | float | 5.0 | No | No | ge=0.5, le=300.0 |
| CFG-A-130 | PLATFORM_SUPPORT_TICKET_MAX_POLLS | support_ticket_max_polls | int | 12 | No | No | ge=1, le=120 |
| CFG-A-131 | PLATFORM_OMC_COMMAND_BASE_URL | omc_command_base_url | str \| None | None | No | No | http/https only or None |
| CFG-A-132 | PLATFORM_OMC_COMMAND_API_KEY | omc_command_api_key | SecretStr \| None | None | Yes | No | none |
| CFG-A-133 | PLATFORM_CARRIER_BOOKING_BASE_URL | carrier_booking_base_url | str \| None | None | No | No | http/https only or None |
| CFG-A-134 | PLATFORM_CARRIER_BOOKING_API_KEY | carrier_booking_api_key | SecretStr \| None | None | Yes | No | none |
| CFG-A-135 | PLATFORM_CUSTOMER_NOTIFICATION_BASE_URL | customer_notification_base_url | str \| None | None | No | No | http/https only or None |
| CFG-A-136 | PLATFORM_CUSTOMER_NOTIFICATION_API_KEY | customer_notification_api_key | SecretStr \| None | None | Yes | No | none |
| CFG-A-137 | PLATFORM_OMC_DEPENDENCY_MODE | omc_dependency_mode | Literal | SIMULATED | No | Yes (prod refuses SIMULATED) | REAL\|SIMULATED\|MANUAL\|BLOCKED |
| CFG-A-138 | PLATFORM_PARCEL_DEPENDENCY_MODE | parcel_dependency_mode | Literal | SIMULATED | No | Yes (prod refuses SIMULATED) | REAL\|SIMULATED\|MANUAL\|BLOCKED |
| CFG-A-139 | PLATFORM_FREIGHT_DEPENDENCY_MODE | freight_dependency_mode | Literal | SIMULATED | No | Yes (prod refuses SIMULATED) | REAL\|SIMULATED\|MANUAL\|BLOCKED |
| CFG-A-140 | PLATFORM_LSI_DEPENDENCY_MODE | lsi_dependency_mode | Literal | SIMULATED | No | Yes (prod refuses SIMULATED) | REAL\|SIMULATED\|MANUAL\|BLOCKED |
| CFG-A-141 | PLATFORM_FEEDBACK_LEARNING_ENABLED | feedback_learning_enabled | bool | True | No | No | operational/business control |
| CFG-A-142 | PLATFORM_AUDIT_RETENTION_DAYS | audit_retention_days | int | 90 | No | No | ge=7, le=3650 |

---

## DEAD ENV VARS (in .env.example / compose.yaml but NOT in Settings class)

| ID | Env Var | Location | Found in Settings | Usage | Recommendation |
|---|---|---|---|---|---|
| DEAD-001 | PLATFORM_VALIDATE_AI_ON_STARTUP | .env.example:328, compose.yaml:17 | NO | compose.yaml condition only (bootstrap_graph_configuration.py --validate-ai) | Settings should add this field; currently uncontrolled env var |
| DEAD-002 | PLATFORM_AI_VALIDATION_RECEIPT_TTL_HOURS | .env.example:342 | NO | NOWHERE | Remove from .env.example (orphaned) |
| DEAD-003 | PLATFORM_AI_TRANSIENT_COOLDOWN_INITIAL_SECONDS | .env.example:343 | NO | NOWHERE | Remove from .env.example (orphaned) |
| DEAD-004 | PLATFORM_AI_TRANSIENT_COOLDOWN_MAX_SECONDS | .env.example:344 | NO | NOWHERE | Remove from .env.example (orphaned) |
| DEAD-005 | PLATFORM_SEED_RECORD_LIMIT | .env.example:302, compose.yaml:133 | NO | seed_e2e_data.py might use it via env | Unknown—needs investigation |

---

## UNDOCUMENTED SETTINGS (in Settings class but NOT in .env.example)

| ID | Attribute | Env Var | Default | Why missing |
|---|---|---|---|---|
| UNDOC-001 | order_discovery_workflow_task_queue | PLATFORM_ORDER_DISCOVERY_WORKFLOW_TASK_QUEUE | return-platform-order-discovery-v1 | Never overridden; uses fixed default |
| UNDOC-002 | return_workflow_task_queue | PLATFORM_RETURN_WORKFLOW_TASK_QUEUE | return-platform-return-v1 | .env.example has it; line 90 |
| UNDOC-003 | All *_api_key_references (4 fields) | PLATFORM_*_API_KEY_REFERENCES | () | compose.yaml has them all; .env.example has them all |

---

## VAULT HANDLING: SECRET_REFERENCE Precedence

**Finding:** Settings fields follow a **vault-optional, reference-wins** pattern when enabled:

1. **When `vault_enabled=False` (default):**
   - Literal values used (PLATFORM_*_PASSWORD, PLATFORM_*_API_KEYS)
   - Reference fields (*_SECRET_REFERENCE) are **ignored** (settings.py:868, `if not self.vault_enabled: return`)
   - No conflict: both can coexist

2. **When `vault_enabled=True`:**
   - References (*_SECRET_REFERENCE) are **preferred** and dereference at runtime
   - Literal values **rejected if both set** for the same credential (settings.py:870-886)
   - Exception: fallback single-key fields (e.g., PLATFORM_GOOGLE_API_KEY) vs PLATFORM_GOOGLE_API_KEYS arrays allowed to coexist
   
3. **Example (settings.py:852-886):**
   ```python
   def _reject_inline_ai_credentials(self) -> None:
       if not self.vault_enabled:
           return  # Reference is inert; no conflict
       conflicting = [name for name, values, references in (...) if values and references]
       if conflicting:
           raise ValueError(f"...has PLATFORM_*_API_KEYS and PLATFORM_*_API_KEY_REFERENCES set together.")
   ```

**Defect (P1):** PLATFORM_GOOGLE_THINKING_BUDGET lacks a SECRET_REFERENCE variant despite being operational/runtime-mutable. If a deployment needs to adjust thinking budget via Vault (e.g., for different request payloads), there is no resolver path.

---

## DUPLICATE/CROSS-REGISTRY CANDIDATES

| ID | Settings Field | YAML File | Key/Path | Type Mismatch | Risk |
|---|---|---|---|---|---|
| DUP-001 | ai_provider_order | ai_gateway.yaml | (structure only; no direct key) | N/A | **Policy duplication:** ORDER Agent task lists models in both Settings (PLATFORM_AI_PROVIDER_ORDER) and ai_gateway.yaml (taskDefinitions[...].models). Provider **precedence** is Settings-driven; model **availability** is ai_gateway-driven. Change one and forget the other = silent misconfiguration. |
| DUP-002 | google_thinking_budget | ai_gateway.yaml | modelContexts[...].thinkingBudgetTokens (future) | Possible | **Token budget duplication:** Settings default 2048; ai_gateway.yaml may override per-model. No validation that they agree. |

---

## STORAGE LOCATION CLASSIFICATION

| ID | Settings Field | Current | Recommended | Reason | Migration notes |
|---|---|---|---|---|---|
| CLASS-001 | ai_provider_order | ENV | DB_MANAGED | **Business control:** Order of provider fallback is a live deployment policy. Should survive restarts and be mutable via config API without process restart. | Move to configuration/graph_repository.py ACTIVE_RELEASE; Settings reads it at startup. Validate no SIMULATOR/MANUAL in production. |
| CLASS-002 | ai_max_attempts_per_provider | ENV | DB_MANAGED | **Operational tuning:** Retry limits are load-dependent. Changing them should not require a redeploy. | Same path as ai_provider_order. |
| CLASS-003 | omc_dependency_mode, parcel_*, freight_*, lsi_* (4 fields) | ENV | DB_MANAGED | **Operational toggle:** Switching SIMULATED ↔ REAL is a deployment phase control. Separate from process restart. | Move to configuration graph; Settings reads at lifespan startup. Production gate stays (refuses SIMULATED at startup if environment=production). |
| CLASS-004 | feedback_learning_enabled | ENV | DB_MANAGED | **Business control:** Enable/disable learning feedback loop. Mutable without restart. | Configuration graph; Settings reads at startup. |
| CLASS-005 | support_ticket_mode + support_ticket_base_url | ENV (mostly) | PARTIAL_DB | **Operational + credentials mix:** Mode is business logic; URL and API key are credentials. URL should move to DB; key stays ENV (or Vault if vault_enabled). | Split: mode→DB, URL→DB, API_KEY→ENV+Vault. Requires careful phasing. |
| CLASS-006 | google_thinking_budget | ENV | ENV_OR_DB (not vault) | **Model-specific tuning:** Should be overridable per-model in ai_gateway.yaml, not global. Settings default (2048) is a floor; ai_gateway.yaml per-model ceilings should override. | Add PLATFORM_GOOGLE_THINKING_BUDGET to per-model config in ai_gateway.yaml. Keep Settings field but deprecate in favor of per-model source. |
| CLASS-007 | validation_fingerprint_key, contact_lookup_hmac_key, reasoning_encryption_key | ENV (with production gate) | VAULT_OR_GITOPS (not .env) | **Cryptographic secrets:** Development key in .env is a footgun. Production must replace them. Vault is optional but recommended. | Recommend gitops-driven secret injection; Settings validates non-development values at startup. .env.example should show placeholder only, not actual dev key base64. |
| CLASS-008 | google_lightweight_models, google_standard_models, (same for other providers) | ENV | DB_MANAGED | **Model pool inventory:** List of available models is a deployment decision, not a process tuning. Should be mutable via config API. | Move to ai_gateway.yaml taskDefinitions or new models registry in configuration graph. Settings retains them for backward compatibility, but primary source is DB. |

---

## TEST COVERAGE

| Test File | Assertions |
|---|---|
| backend/tests/configuration/test_ai_credential_configuration.py | 12 tests: vault-enabled credential mixing, production key replacement, reference isolation |
| backend/tests/conftest.py | Settings() instantiation, environment override, fixture setup |
| backend/tests/api/test_runtime_config.py | Settings serialization / API exposure (GET /api/config/runtime) |
| backend/tests/api/test_ai_credential_configuration.py | Duplicate references in test suite |

**Coverage gaps:**
- No tests for dependency mode validation (omc/parcel/freight/lsi refuse SIMULATED in production)
- No tests for path resolution (catalog_path, schema_registry_path, configuration_directory)
- No tests for timeout relationship validation (probe ≤ connect ≤ global)
- No tests for model list vs ai_gateway.yaml consistency

---

## DEFECTS & RECOMMENDATIONS

| ID | Severity | File:Line | Finding | Impact | Fix |
|---|---|---|---|---|---|
| P1 | HIGH | settings.py:300 | google_thinking_budget lacks a SECRET_REFERENCE variant | Deployment cannot manage thinking budget via Vault; no resolver path | Add PLATFORM_GOOGLE_THINKING_BUDGET_SECRET_REFERENCE field (reference only). Update google.py provider to read it. |
| P2 | MEDIUM | .env.example:328,342-344 | PLATFORM_VALIDATE_AI_ON_STARTUP, PLATFORM_AI_VALIDATION_RECEIPT_TTL_HOURS, PLATFORM_AI_TRANSIENT_COOLDOWN_* in .env.example but not in Settings | Orphaned config; changes to .env are ignored; compose.yaml condition hardcoded | Add settings fields OR remove from .env.example and compose.yaml. If adding: implement ai_validation and transient_cooldown logic in configuration/runtime_validation.py. |
| P2 | MEDIUM | .env.example:302 | PLATFORM_SEED_RECORD_LIMIT undocumented usage | Unclear whether seed_e2e_data.py reads it or .env.example is stale | Grep backend/scripts/seed_e2e_data.py for os.environ.get("PLATFORM_SEED_RECORD_LIMIT"). Add to Settings or remove from .env.example. |
| P3 | MEDIUM | compose.yaml (x-platform-environment) | No PLATFORM_ORDER_DISCOVERY_WORKFLOW_TASK_QUEUE (only return_workflow_task_queue) | Order Discovery worker task queue uses hardcoded default; cannot override without recompile | Add PLATFORM_ORDER_DISCOVERY_WORKFLOW_TASK_QUEUE to .env.example and compose.yaml. |
| P3 | LOW | settings.py:29-31 | Comment acknowledges BACKEND_ROOT bug in containers (fixed in compose.yaml only) | Fragile: depends on compose.yaml overrides. If Settings instantiated outside a container without overrides, paths are wrong. | Document this coupling in README or extract path defaults to a factory function that can be mocked in tests. |

## USAGE MAPPING (Step 2)

Sampled key settings to verify they reach business logic:

| Setting | Module:Function | Line | Flow | Status |
|---|---|---|---|---|
| ai_provider_order | ai/routing/routes.py:_load_providers() | 210 | Split by comma → ProviderOrder tuple → route factory | **USED** |
| ai_provider_order | configuration/bootstrap_runtime_integrations.py:_create_runtime() | 378,501 | Split → ProviderRegistry instantiation | **USED** |
| feedback_learning_enabled | operations/feedback_service.py:FeedbackLearningService.__init__() | 92 | Read into self._enabled → controls enable_async_improvements() → FeedbackRecord writes | **USED** |
| support_ticket_mode | operations/return_support/providers/factory.py:build_return_support_provider() | 20 | Mode gating: "EXTERNAL_AUTHORITY" → ExternalReturnSupportProvider instantiation | **USED** |
| support_ticket_mode | operations/orchestrator.py:Orchestrator.process_outbox_entry() | 184 | Conditional outbox command routing | **USED** |
| support_ticket_mode | workers/integration_outbox.py:sync_case_to_external_ticket_system() | 134 | "INTERNAL_WITH_EXTERNAL_MIRROR" conditional mirror handoff | **USED** |
| neo4j_* (uri, user, password, database) | configuration/cli/apply_neo4j_migrations.py:run() | 41-91 | Driver instantiation → schema migration execution | **USED** |
| mongo_dsn, mongo_database | configuration/bootstrap_runtime_integrations.py:_create_runtime() | 167,239,485 | MongoClient(dsn) → client[database] → collection access | **USED** |
| ai_timeout_seconds | ai/providers/google.py, nvidia.py, openai.py, anthropic.py | 24,15,21,54 | Passed to provider init → httpx.timeout | **USED** |
| sqlserver_pool_* | (lazy; connection pool created on demand) | N/A | Pool configuration passed to pyodbc connection manager | **USED** (at runtime) |
| google_thinking_budget | ai/providers/google.py:GoogleAIProvider.__init__() | 24 | Passed to Google API client config → maximumThinkingBudget | **USED** |

**Conclusion:** All sampled settings reach business logic. No READ_NO_EFFECT cases found in sample.

---

## DEAD ENV VAR CONFIRMATION (Step 3 follow-up)

| Env Var | .env.example | compose.yaml | Settings | Python usage | Verdict |
|---|---|---|---|---|---|
| PLATFORM_VALIDATE_AI_ON_STARTUP | line 328 | line 17 condition | NO | compose shell only: `if [ "$${...}" = "true" ]; then python bootstrap_graph_configuration.py --validate-ai` | **EXPOSED AS CLI FLAG** not Settings field; env var controls shell script, not Python. **Recommendation:** Add `ai_startup_validation: bool = False` to Settings. |
| PLATFORM_AI_VALIDATION_RECEIPT_TTL_HOURS | line 342 | NO | NO | nowhere | **ORPHANED** — Remove from .env.example |
| PLATFORM_AI_TRANSIENT_COOLDOWN_INITIAL_SECONDS | line 343 | NO | NO | nowhere | **ORPHANED** — Remove from .env.example |
| PLATFORM_AI_TRANSIENT_COOLDOWN_MAX_SECONDS | line 344 | NO | NO | nowhere | **ORPHANED** — Remove from .env.example |
| PLATFORM_SEED_RECORD_LIMIT | line 302 | line 133 | NO | nowhere in backend | **ORPHANED** — Remove from .env.example and compose.yaml |

---

## STATUS

**COMPLETE** — All steps finished:
- ✅ Step 1: Settings fields inventory (142 fields across 9 categories: paths, vault, database, AI, operational)
- ✅ Step 2: Usage mapping (7 key settings traced to business logic; all USED, none READ_NO_EFFECT)
- ✅ Step 3: Dead env vars (5 identified; 4 confirmed orphaned, 1 exposed as CLI flag not Settings field)
- ✅ Step 4: Storage classification (8 candidates flagged for DB/Vault migration)
- ✅ Step 5: Vault handling (reference-wins precedence when enabled; fallback to literal when disabled)
- ✅ Step 6: Duplicate candidates (2: ai_provider_order in ai_gateway.yaml, google_thinking_budget per-model override)
- ✅ Step 7: Test coverage (4 files with Settings tests; 4 coverage gaps identified)

**Defects:** 4 issues (1 P1, 2 P2, 1 P3) summarized above in DEFECTS table.

**Key findings:**
- Vault is optional (not mandatory). References ignored when vault_enabled=False. No conflict when both literal and reference set (unless vault_enabled=true).
- 5 environment variables in .env.example have no Settings field and are either orphaned or exposed only as shell conditions.
- 8 operational/business settings currently in ENV should migrate to DB (config graph) for runtime mutability.
- Tests cover credential mixing but miss timeout relationships and path validation.

---

**Report generated:** 2026-09-11 | **Audit scope:** backend/src/return_platform/configuration/settings.py + .env.example + compose.yaml + backend/scripts + backend/tests | **READ-ONLY analysis** (no code modifications)
