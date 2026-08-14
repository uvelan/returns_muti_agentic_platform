# Google Stitch Master Prompt — Returns Platform V2

Copy everything below into Google Stitch as one master prompt.

---

Design a complete, production-ready responsive web application for **Returns Platform V2**, an enterprise return-management and configurable agent platform.

The application has two connected experiences:

1. **Returns Copilot** used only by an **Associate, Sales Representative, or Customer Care representative** while helping a customer.
2. **Configuration Studio** used by administrators, data stewards, source owners, security owners, and architects.

Do not describe the application itself as customer-facing. Do not create a consumer shopping experience. The operational interface is an internal tool used by trained representatives.

## Visual direction

Follow the existing product theme precisely:

- Enterprise, trustworthy, calm, data-dense, and accessible.
- Desktop-first, fully responsive for tablet and mobile.
- Font: Inter or a close modern sans serif.
- Dark sidebar: `#00201D`.
- Primary teal: `#00685F`.
- Active/accent teal: `#008378`.
- App background: `#F5FAF8`.
- Primary text: `#171D1C`.
- Secondary text: `#3D4947`.
- Muted text: `#6D7A77`.
- Borders: `#BCC9C6`.
- Selected surfaces: `#E5F5F1`.
- White cards with subtle borders and restrained shadows.
- Corners between 8px and 16px; avoid excessive pill shapes.
- Use Lucide-style line icons.
- Use red only for destructive actions/errors, amber for warnings or human decisions, blue for in-progress states, and green for validated/complete states.
- Meet WCAG AA contrast, visible keyboard focus, semantic form labels, and 44px minimum touch targets.
- Avoid gradients, decorative illustrations, glassmorphism, oversized empty hero areas, and generic AI sparkle imagery.

## Global application shell

Create one coherent shell with:

- Collapsible dark left navigation grouped into **Returns**, **Configuration**, **Governance**, and **Operations**.
- Sticky top bar with breadcrumbs, environment badge, active configuration release, notifications, help, and user/role menu.
- A command/search field for finding an order, configuration module, source, schema, or sync run.
- Page titles, concise descriptions, contextual primary actions, loading skeletons, empty states, error states, success toasts, confirmation dialogs, and unsaved-change warnings.
- Desktop layouts up to 1440px, tablet adaptations, and mobile drawer navigation.

## Core business rules visible in the UI

- Canonical order ID: `fullOrderId = ACCOUNT_OR_LOGON * ORDERNUMBER`.
- Canonical line ID: `fullOrderLineId = fullOrderId * IMMUTABLE_LINE_NUMBER`.
- A full order can contain multiple lines. Full sync takes one validated `fullOrderId` and hydrates every authoritative line.
- Partial sync starts from a strong anchor, resolves zero, one, or multiple order IDs, and loads only a bounded discovery projection until the representative selects an order.
- Strong anchors are tracking number, invoice number, delivery ticket number, and customer PO number. Account/logon plus order number is also a direct canonical lookup.
- Never present web order number, Trilogie number, source transaction ID, or raw order number as globally unique by itself.
- Agents are independent and communicate only through immutable, versioned context. Show context version, source, freshness, authorization, and confidence where useful; do not expose chain-of-thought.
- Do not persist every source field into the graph. Show only the minimum data required to complete the return process, with on-demand synchronization for missing information.

## Generate these Returns Copilot screens

### 1. Return workspace home

- Representative greeting, role, branch/location, and recent sessions.
- Large **Start a return** action.
- Search/resume active return sessions.
- Cards for drafts, waiting for customer information, sync failures, and recently completed returns.

### 2. New return / Order Discovery conversation

- Three-column desktop layout: conversation on the left, structured discovery progress in the center, order context on the right.
- Natural language input with examples such as tracking number, invoice number, delivery ticket, PO number, or account plus order number.
- The agent asks only the smallest relevant clarification based on information already supplied.
- Show extracted anchors as editable chips with source and validation state.
- Show a progress rail: Identify order → Select order → Sync full order → Select lines → Analyze → Complete return.
- Provide clear states for listening, resolving anchor, partial sync, multiple candidates, no result, stale source, unauthorized result, retry, and human escalation.

### 3. Order candidate results

- Ranked order cards/table with customer name, masked account, order number, order date, branch, PO, fulfillment type, matching anchor, confidence, and freshness.
- Explain the match using concise evidence such as “Tracking number matched shipment.”
- Multiple orders must require explicit representative selection.
- Provide refine-search and ask-customer actions without revealing unauthorized orders.

### 4. Full order synchronization

- Selected canonical `fullOrderId`, synchronization progress, authoritative sources queried, freshness, and retry state.
- Make it obvious that one order sync can produce many order lines.
- Show success, partial-source failure, stale data, conflict, and permission-denied variants.

### 5. Order and line selection

- Order summary header and a selectable line-item table/cards.
- Each line shows immutable line number, item/SKU, description, quantity ordered, fulfilled, previously returned, returnable quantity, price, fulfillment method, and relevant shipment.
- Allow one or multiple line selections and quantities without changing the canonical order ID.
- Include product image placeholders only when useful; favor clear item data.

### 6. Order Analysis

- Selected items, requested quantity, reason, condition, original fulfillment facts, evidence, policy result, and confidence.
- Separate deterministic facts from recommendations.
- Show eligible, ineligible, needs review, missing information, and exception scenarios.
- Provide representative-friendly explanations and the next best question.

### 7. Return method and resolution

- Options such as branch return, pickup, carrier shipment, replacement, credit, or support review, only when allowed.
- Show fees, estimated timing, required packaging, documents, and approval needs.
- Include support-ticket and manager-approval paths.

### 8. Review and submit return

- Complete review of customer/order identity, selected lines, quantities, reasons, policy decisions, return method, expected resolution, and acknowledgements.
- Clearly distinguish editable sections and locked system decisions.
- Primary action: **Submit return** with confirmation dialog and idempotent pending state.

### 9. Return confirmation and tracking

- Return authorization/reference, status, timeline, next actions, printable/downloadable instructions, labels/documents, pickup or branch details, and support contact path.
- Timeline events include submitted, approved, label created, in transit, received, inspected, and completed.

### 10. Representative session history and detail

- Searchable/filterable session table.
- Detail view with context-version timeline, user-visible conversation, agent outcomes, sync events, approvals, and audit references.
- Never display hidden reasoning or credentials.

## Generate these Configuration Studio screens

### 11. Configuration overview

- Health summary for active release, draft changes, invalid modules, dependency conflicts, source health, graph schema, and recent sync failures.
- Quick actions: add source, edit agent, design graph schema, import configuration, create release.

### 12. Data source list

- Search/filter table or cards for MongoDB, PostgreSQL, SQL Server, and Neo4j.
- Name, type, environment, database, access mode, validation status, last validation, owner, and actions.
- Add, edit, validate, inspect schema, disable, and governed delete actions.

### 13. Add/edit data source drawer

- Typed conditional form, never a raw JSON editor.
- Source type, display name, description, host/URI, port, database, schema, username, credential vault reference, SSL/TLS, access mode, owner, tags, and test connection.
- Credentials are masked and described as stored securely; never show them in lists or logs.

### 14. Validate connection

- Step-based diagnostics for network, authentication, authorization, database access, metadata discovery, and latency.
- Show actionable errors, retry, and save-validation-result actions.

### 15. Source Schema Explorer

- Dataset tree for tables, collections, views, APIs, or graph labels.
- Field table with name, type, nullable/required, key/index, sample classification, description, and inclusion state.
- Search, refresh metadata, compare schema versions, and open read-only data preview.

### 16. Read-only data preview

- Table and JSON modes, filtering, pagination, redacted sensitive values, freshness, and a persistent read-only badge.

### 17. Configuration module catalog

- Group modules into Agents, Contexts, Workflows, Policies, Sources, Mappings, Graph, Sync, Integrations, and Platform.
- Cards/table show module ID, owner, version, draft/active/invalid status, dependencies, last editor, and last update.
- Include search, type/status/owner filters, compare, export, and create-draft actions.

### 18. Agent configuration editor

- Dedicated screen for each independent agent: Return Session Orchestrator, Order Discovery, Order Analysis, Return Workflow, Return Fulfillment, Bay Allocation, Learning, and Graph Schema Design.
- Display only settings owned by the selected agent plus read-only references to shared dependencies.
- Typed controls for enabled state, execution mode, accepted input contexts, output context, capabilities, model/prompt policy references, thresholds, timeout/retry, idempotency, approval/escalation, and observability.
- Use tabs: General, Context Contract, Capabilities, Decision Rules, Reliability, Human Review, Observability, Dependencies.
- Support nested objects, lists, maps, reference pickers, conditional fields, inline validation, defaults, descriptions, and reset-to-inherited.

### 19. Shared module editors

- Consistent typed editor screens for workflows, policies, source definitions, canonical mappings, graph mappings, sync profiles, integrations, and platform settings.
- Clearly show owner, impact, consumers, dependencies, and affected agents.

### 20. Graph schema catalog and detail

- List schema versions with draft, quarantined, approved, active, retired, and migration-required states.
- Detail has overview, node types, relationship types, properties, constraints, indexes, projection profiles, query capabilities, source mappings, and validation issues.
- Use an understandable schema diagram plus accessible table alternative.

### 21. Graph Schema Design Agent workspace

- Split layout: source/schema evidence and existing graph on the left, context-aware configuration chat in the center, live proposal/change set on the right.
- The agent dynamically identifies only unresolved configuration gaps from selected sources, actual structures, current schema, requested capability, and validation results.
- No hardcoded questionnaire and no repeated questions already answered by metadata.
- Questions are directed to the correct admin/owner and explain why the answer is required.
- Suggested answers are editable and never auto-approved.

### 22. Graph schema proposal review

- Visual and textual diff for nodes, relationships, properties, constraints, indexes, mappings, and projection rules.
- Each change shows rationale, evidence, affected module, risk classification, migration impact, and unresolved blockers.
- Actions: request changes, save draft, validate, send for approval. The agent cannot activate or migrate.

### 23. Redesign existing graph schema

- Choose active baseline, create a new draft version, select redesign goals, inspect impact, and preview migration/backfill.
- Never mutate the active version in place.
- Show additive, breaking, security-sensitive, and destructive change classifications.

### 24. Configuration release builder

- Build an atomic release manifest from immutable module versions and checksums.
- Dependency graph, compatibility results, affected agents/workflows, validation gates, approval status, and release notes.
- Actions: validate, compare with active, submit for approval, schedule activation, activate, and rollback subject to permissions.

### 25. Release comparison and approval

- Side-by-side and unified diff modes.
- Filter by module and severity.
- Reviewer comments, required approvals, evidence, test results, and explicit approve/reject controls.

### 26. Import/export center

- Export selected modules, schema, or a full release as YAML/JSON with manifest and checksums.
- Import through upload, parse, quarantine, validate, map dependencies, review field-level errors, and create drafts.
- Never activate directly from upload.
- Include round-trip status and downloadable validation report.

### 27. Synchronization operations

- Tabs for partial order sync, full order sync, administrative backfill, and run history.
- Partial sync accepts an approved strong anchor and may resolve multiple `fullOrderId` candidates.
- Full sync accepts exactly one validated `fullOrderId` and shows all synchronized lines.
- Display source calls, records read, graph writes, freshness, duration, idempotency key, errors, retries, and audit reference.

### 28. Audit and governance

- Searchable immutable audit timeline for configuration edits, validation, approvals, activation, rollback, import/export, schema proposals, and sync operations.
- Filters for actor, role, module, release, action, date, and result.
- Detail drawer shows before/after references and correlation IDs without credentials or sensitive payloads.

## Interaction and state requirements

For every major screen, create realistic populated, empty, loading, validation-error, permission-denied, and service-failure states. Use representative sample data based on Ferguson-style distribution orders, but do not copy a public retail checkout design.

Keep operational tasks conversational but always pair conversation with structured, reviewable state. Keep configuration tasks form-driven and governed; raw YAML/JSON may appear only in preview, compare, import, or export views.

Use drawers for focused create/edit flows, dialogs for confirmation, full pages for complex review, and sticky action bars for long configuration forms. Every destructive or production-impacting action must show impact and require explicit confirmation.

## Stitch output request

Generate:

1. A reusable design system and application shell.
2. All 28 screens as consistent high-fidelity desktop designs.
3. Responsive tablet and mobile variants for the Return Discovery conversation, candidate selection, order line selection, Data Sources, Agent Configuration Editor, and Graph Schema Design workspace.
4. Connected prototype flows for:
   - strong anchor → candidates → selected order → full sync → line selection → analysis → submit → tracking;
   - add source → validate → inspect schema → preview data;
   - configure agent → validate → release builder → approval;
   - graph schema chat → proposal → review → approval → release;
   - import → quarantine → fix validation errors → save draft.
5. Component variants for buttons, inputs, selects, comboboxes, nested key/value editors, status badges, tables, cards, timelines, chat messages, code preview, schema diagram, diff viewer, validation summary, drawers, dialogs, toasts, and skeletons.

Use concise, domain-specific copy throughout. The result should look like one implementable enterprise React application—not a collection of unrelated concept screens.
