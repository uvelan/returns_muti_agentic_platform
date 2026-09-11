# Config audit shared brief (read first)

Repo: K:/Projects/Ret/returns_muti_agentic_platform (Windows; use forward slashes; Git Bash).
Python: ALWAYS backend/.venv/Scripts/python.exe (bare `python` is the Windows Store shim). PYTHONPATH=backend/src.
Never run scripts/reset_all.* (drops all databases). Do not modify production code; this phase is READ-ONLY analysis.

Known architecture claims (verify, do not trust):
- backend/config/manifest.yaml lists manifest modules -> configuration/application/loader.py -> compatibility.py -> RuntimeSnapshot.
- ai_gateway.yaml and returns/production.yaml are "singleton compatibility files" loaded by explicit name.
- Precedence claimed: BOOTSTRAP_ENV -> BASELINE (files) -> ACTIVE_RELEASE (Neo4j configuration graph). Runtime reads the RELEASED release from Neo4j (configuration/graph_repository.py, runtime_loader.py, process_adoption.py).
- backend/scripts/bootstrap_graph_configuration.py republishes a release at every launcher start (prior finding F-0084: bumps head revision when active release is not one it produced).
- Config API: backend/src/return_platform/configuration/api/ (GET /api/config/releases, POST releases {from_active}, PATCH domains (RFC7396), promote VALIDATED/RELEASED with expected_head_revision, GET /api/config/runtime, /api/config/adoption).
- Frontend config UI: frontend/src/domains/config/ (ConfigurationPage, AgentsSection, SupportTemplateSection, DocumentEditor, JsonView); also AiControlCenterPage, SyncControlPage, GraphAnalyzerWorkspace, schema releases (PUT /api/schema-releases/active/document).
- Settings: backend/src/return_platform/configuration/settings.py (pydantic, PLATFORM_* env); .env.example at repo root; compose.yaml overrides.
- Other files: schema_registry.yaml, data_assets.yaml, dependency_simulation.yaml, data_platform/*, seed/*, live_validation/*, dynamic_knowledge/*, reasoning.yaml, backend/assets.yaml, policies/* (README says unloaded).

Output rules:
- Write to evidence/config_audit/<your-part>.md EARLY (after first findings) and REWRITE after each step; end with a "STATUS: COMPLETE|PARTIAL" line.
- Every claim cites file:line. Say UNVERIFIED explicitly where you could not verify.
- Config IDs: use prefix given in your task (e.g. CFG-A-001). One key per row; do not merge unrelated keys.
- Do not accept `settings.x` read as "used"; trace to the business logic that consumes the value. Mark READ_BUT_NO_EFFECT where applicable.
- Be terse in the report; tables over prose.
