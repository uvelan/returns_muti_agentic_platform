import { HttpResponse, delay, http } from "msw";

/**
 * Mocks for the four canonical domains, so `npm run dev:mock` can render them.
 *
 * Without these the mock app boots into the legacy `/v1` shell and every
 * canonical screen shows "You do not have access", because `/api/principal`
 * 404s and the capability hook therefore grants nothing. That made the four
 * screens the plan's whole Wave E is about the only ones a developer could not
 * look at without standing up Mongo, Neo4j and Temporal.
 *
 * **These grant every capability on purpose.** The point is to see the screens,
 * and per-capability behaviour is covered by unit tests that stub `can()`
 * directly, which is a more precise instrument than editing a fixture. Nothing
 * here weakens anything: the backend re-checks every capability, and this file
 * is excluded from the production bundle by the mock-mode gate in `main.tsx`
 * (`scripts/check-bundle.js` fails the build if a mock artifact leaks).
 */

const ALL_CAPABILITIES = [
  "returns.session.read",
  "returns.session.write",
  "returns.support.act",
  "returns.logistics.act",
  "returns.warehouse.act",
  "returns.audit.read",
  "config.runtime.read",
  "config.release.read",
  "config.release.promote",
  "config.source.read",
  "config.source.write",
  "config.source.rebind",
  "graph_schema.draft.read",
  "graph_schema.draft.write",
  "graph_schema.generation.activate",
  "governance.proposal.read",
  "governance.proposal.write",
  "governance.proposal.approve",
  "governance.proposal.activate",
  "ai.request.read",
  "ai.metrics.read",
  "ai.interception.read",
  "ai.interception.act",
  "ai.replay.read",
  "ai.route.write",
];

/**
 * The full `meta`, not just `request_id`.
 *
 * `apiClient` validates every field before it will hand back `data`, so a
 * partial meta makes a 200 look like an empty response -- which is how the
 * first version of this file produced "You do not have access" from a handler
 * that was in fact answering correctly.
 */
function envelope<T>(data: T, requestId: string) {
  return {
    data,
    meta: {
      schema_version: "1.0",
      request_id: `mock-${requestId}`,
      generated_at: new Date().toISOString(),
      freshness: "LIVE",
      partial: false,
      warnings: [],
    },
  };
}

const SYNC_RUN_FULL = {
  id: "sync-mock-full",
  mode: "FULL",
  status: "COMPLETED",
  schemaVersion: "2026.08.04",
  sourceCounts: { source_sales: 1240, source_products: 380 },
  nodeWrites: 3120,
  relationshipWrites: 2740,
  constraintsApplied: ["uq_salesorder_account_id_sales_order_number"],
  configurationDigest: "7c9d67a64d2b2372",
  errorCode: null,
  startedBy: "mock-operator",
  startedAt: "2026-08-11T06:00:00Z",
  completedAt: "2026-08-11T06:04:12Z",
  graphGenerationId: null,
  requestDigest: null,
  requestedBy: null,
};

const SYNC_RUN_ON_DEMAND = {
  id: "sync-mock-ondemand",
  mode: "ON_DEMAND",
  status: "COMPLETED",
  schemaVersion: "2026.08.04",
  sourceCounts: { source_sales: 1 },
  nodeWrites: 5,
  relationshipWrites: 4,
  constraintsApplied: [],
  configurationDigest: "7c9d67a64d2b2372",
  errorCode: null,
  startedBy: "order-discovery-agent",
  startedAt: "2026-08-11T09:41:02Z",
  completedAt: "2026-08-11T09:41:03Z",
  graphGenerationId: "legacy-live",
  requestDigest: "0f3a91c4",
  requestedBy: {
    agentId: "order-discovery-agent",
    conversationId: "conv-mock-1",
    clientTurnId: "turn-4",
    entityId: "sales_order",
    strongAnchorId: "exact_order_key",
    anchorFieldIds: ["order_key"],
  },
};

const SESSION = {
  id: "ret-mock-1",
  correlationId: "corr-mock-1",
  customerReference: "CUST-4417",
  orderReference: "ORD-88123",
  itemReferences: ["LINE-1", "LINE-2"],
  productReferences: ["SKU-99"],
  processingWarehouseReference: "WH-02",
  reasonCode: "DAMAGED",
  returnQuantity: 2,
  packageCount: 1,
  shippingPathExpectation: "BOL",
  orderSource: "TRILOGIE",
  channel: "ASSOCIATE",
  status: "RUNNING",
  currentStage: "PHYSICAL_RETURN",
  progressPercentage: 45,
  returnReference: "RMA-5512",
  supportTicketReference: null,
  supportStatus: null,
  approvedReturnMethod: "CARRIER_PICKUP",
  customerResolutionStatus: "PENDING",
  physicalReturnStatus: "IN_PROGRESS",
  warehouseStatus: "PENDING",
  vendorRecoveryStatus: "NOT_REQUIRED",
  caseClosureStatus: "OPEN",
  trackingReference: "1Z999AA10123456784",
  bayReference: null,
  aiRequestId: null,
  failureCode: null,
  failureMessage: null,
  notes: null,
  version: 4,
  createdAt: "2026-08-09T09:00:00Z",
  updatedAt: "2026-08-10T11:30:00Z",
};

/**
 * `configuration/api/sources.py::_definitions()`, as it is actually served.
 *
 * **No credential appears here, and that is not an omission to be tidied up.**
 * `SourceItem` / `SourceDetail` have no field one could travel in, and
 * `/api/config` scrubs every response through `redact_secret_values`. A fixture
 * carrying a password would make the mock app the only place in the system
 * where a credential reaches a browser.
 */
const SOURCES = [
  {
    id: "source-mongodb",
    name: "Source MongoDB",
    engine: "MONGODB",
    environment: "LOCAL",
    ownership: "AUTHORITATIVE",
    health: "HEALTHY",
    capability: "READ_ONLY",
    lastInventoryTime: "2026-08-11T09:00:00Z",
    connectionIdentity: "mongodb/source_db",
    inventoryTotals: { assets: 2, records: null },
    lastMetadataRefresh: "2026-08-11T09:00:00Z",
    dependencyWarnings: [] as string[],
    assets: [
      {
        assetId: "source_sales",
        name: "source_db.salesInv",
        kind: "COLLECTION",
        ownership: "SOURCE",
        authoritative: true,
        writableInSandbox: false,
      },
      {
        assetId: "source_products",
        name: "source_db.products",
        kind: "COLLECTION",
        ownership: "SOURCE",
        authoritative: true,
        writableInSandbox: false,
      },
    ],
  },
  {
    id: "platform-mongodb",
    name: "Platform MongoDB",
    engine: "PLATFORM",
    environment: "LOCAL",
    ownership: "INTERNAL",
    health: "HEALTHY",
    capability: "WRITABLE",
    lastInventoryTime: "2026-08-11T09:00:00Z",
    connectionIdentity: "mongodb/return_platform",
    inventoryTotals: { assets: 0, records: null },
    lastMetadataRefresh: "2026-08-11T09:00:00Z",
    dependencyWarnings: [] as string[],
    assets: [],
  },
  {
    id: "sqlserver",
    name: "Return Business State SQL Server",
    engine: "SQL_SERVER",
    environment: "LOCAL",
    ownership: "AUTHORITATIVE",
    health: "DEGRADED",
    capability: "READ_ONLY",
    lastInventoryTime: "2026-08-11T08:58:00Z",
    connectionIdentity: "sqlserver/ReturnBusinessState",
    inventoryTotals: { assets: 1, records: null },
    lastMetadataRefresh: "2026-08-11T08:58:00Z",
    // The probe's own `safe_message` -- written to be safe to display, and the
    // only thing on the screen that says why a source is degraded.
    dependencyWarnings: ["Login timeout expired while opening a pooled connection."],
    assets: [
      {
        assetId: "sql_bay_assignment",
        name: "dbo.BayAssignment",
        kind: "TABLE",
        ownership: "SOURCE",
        authoritative: true,
        writableInSandbox: false,
      },
    ],
  },
  {
    id: "neo4j",
    name: "Neo4j Graph Projection",
    engine: "NEO4J",
    environment: "LOCAL",
    ownership: "DERIVED",
    health: "HEALTHY",
    capability: "READ_ONLY",
    lastInventoryTime: "2026-08-11T09:00:00Z",
    connectionIdentity: "neo4j/neo4j",
    // Its assets are the graph's nodes and relationships, which the registry
    // counts separately and the source has none of its own -- so the totals and
    // the empty list disagree on purpose, exactly as the handler serves them.
    inventoryTotals: { assets: 14, records: null },
    lastMetadataRefresh: "2026-08-11T09:00:00Z",
    dependencyWarnings: [] as string[],
    assets: [],
  },
];

/**
 * `SourceItem` is `SourceDetail` minus the detail, so the list response is
 * projected from the same fixture rather than written twice -- which is how a
 * mock ends up reporting one health on the list and another on the detail.
 */
function sourceItem(source: (typeof SOURCES)[number]) {
  return {
    id: source.id,
    name: source.name,
    engine: source.engine,
    environment: source.environment,
    ownership: source.ownership,
    health: source.health,
    capability: source.capability,
    lastInventoryTime: source.lastInventoryTime,
  };
}

/** `SourceBindingView`. `connectionRef` is a pointer, never a resolved secret. */
const BINDINGS = [
  {
    dataset: "source_sales",
    sourceAssetId: "salesInv",
    connectorType: "MONGODB",
    connectionRef: "vault://return-platform/sources#salesInv",
    objectRef: { database: "source_db", collection: "salesInv" },
    incrementalCursorField: "updated_at",
    overridden: false,
  },
  {
    dataset: "sql_bay_assignment",
    sourceAssetId: "BayAssignment",
    connectorType: "MSSQL",
    connectionRef: "vault://return-platform/sources#bay-assignment-restored",
    objectRef: { schema: "dbo", table: "BayAssignment" },
    incrementalCursorField: null,
    overridden: true,
  },
];

/**
 * `ProposalDetailView`, one per kind, so the inbox shows what it is for: a
 * schema draft, a configuration edit and a feedback improvement in one queue.
 *
 * `history` and `diff` keep the record's own key names (`occurred_at`), because
 * the router serializes them straight from the Pydantic model rather than
 * through a camelCase view.
 */
const PROPOSALS = [
  {
    proposalId: "prop-mock-1",
    proposalType: "GRAPH_SCHEMA",
    subjectId: "dr-mock-1",
    title: "Add Bay to the return graph",
    status: "REVIEW_PENDING",
    risk: "HIGH",
    affectedKeys: ["entities.bay", "entities.warehouse.legacy_slot"],
    proposedBy: "analyst-2",
    decidedBy: null,
    createdAt: "2026-08-11T09:00:00Z",
    updatedAt: "2026-08-11T09:30:00Z",
    before: { entities: { warehouse: { legacy_slot: "slot_code" } } },
    after: { entities: { warehouse: {}, bay: { label: "Bay" } } },
    diff: [
      { key: "entities.bay", change: "ADDED", before: null, after: { label: "Bay" } },
      {
        key: "entities.warehouse.legacy_slot",
        change: "REMOVED",
        before: "slot_code",
        after: null,
      },
    ],
    evidence: ["snapshot-4f2a", "validation-77"],
    evidenceDigest: "9c1d0ae44d2b2372",
    validationReceipt: "vr-2026-08-11-01",
    decisionNote: null,
    activationReference: null,
    history: [
      { status: "VALIDATED", actor: "analyst-2", occurred_at: "2026-08-11T09:10:00Z", note: null },
      {
        status: "REVIEW_PENDING",
        actor: "analyst-2",
        occurred_at: "2026-08-11T09:30:00Z",
        note: "Ready for review",
      },
    ],
  },
  {
    proposalId: "prop-mock-2",
    proposalType: "CONFIGURATION",
    subjectId: "ORDER_AGENT_REASONING_V1",
    title: "Raise the order agent's candidate ceiling to 25",
    status: "APPROVED",
    risk: "MEDIUM",
    affectedKeys: ["retrieval.max_candidates"],
    proposedBy: "operator-1",
    decidedBy: "mock-operator",
    createdAt: "2026-08-10T14:00:00Z",
    updatedAt: "2026-08-10T15:00:00Z",
    before: { retrieval: { max_candidates: 10 } },
    after: { retrieval: { max_candidates: 25 } },
    diff: [{ key: "retrieval.max_candidates", change: "CHANGED", before: 10, after: 25 }],
    evidence: ["conv-mock-1"],
    evidenceDigest: "3b7d0210f4a9",
    validationReceipt: "reload-2026-08-10-04",
    decisionNote: "Agreed after the Charlotte ambiguity review.",
    activationReference: null,
    history: [
      { status: "VALIDATED", actor: "operator-1", occurred_at: "2026-08-10T14:20:00Z", note: null },
      {
        status: "REVIEW_PENDING",
        actor: "operator-1",
        occurred_at: "2026-08-10T14:30:00Z",
        note: null,
      },
      {
        status: "APPROVED",
        actor: "mock-operator",
        occurred_at: "2026-08-10T15:00:00Z",
        note: "Agreed after the Charlotte ambiguity review.",
      },
    ],
  },
  {
    proposalId: "prop-mock-3",
    proposalType: "IMPROVEMENT",
    subjectId: "fb-mock-8",
    title: "Add 'branch' as a clarification field",
    status: "REVIEW_PENDING",
    risk: "LOW",
    affectedKeys: ["fields.branch"],
    proposedBy: "feedback-learning",
    decidedBy: null,
    createdAt: "2026-08-11T07:00:00Z",
    updatedAt: "2026-08-11T07:00:00Z",
    before: {},
    after: { fields: { branch: "account_id" } },
    diff: [{ key: "fields.branch", change: "ADDED", before: null, after: "account_id" }],
    evidence: ["conv-mock-1", "conv-mock-4"],
    evidenceDigest: "7c9d67a6",
    // No receipt: the screen must say so rather than leave a blank row, because
    // a proposal nothing has certified is exactly what a reviewer must notice.
    validationReceipt: null,
    decisionNote: null,
    activationReference: null,
    history: [
      {
        status: "REVIEW_PENDING",
        actor: "feedback-learning",
        occurred_at: "2026-08-11T07:00:00Z",
        note: null,
      },
    ],
  },
];

/**
 * `ProposalSummaryView` -- deliberately without `before`/`after`.
 *
 * The real router splits these for a reason worth preserving in the mock: a
 * proposal's documents are unbounded (a graph schema is the whole shape), so
 * listing the inbox must not pay for a payload only the detail pane reads.
 */
function proposalSummary(proposal: (typeof PROPOSALS)[number]) {
  return {
    proposalId: proposal.proposalId,
    proposalType: proposal.proposalType,
    subjectId: proposal.subjectId,
    title: proposal.title,
    status: proposal.status,
    risk: proposal.risk,
    affectedKeys: proposal.affectedKeys,
    proposedBy: proposal.proposedBy,
    decidedBy: proposal.decidedBy,
    createdAt: proposal.createdAt,
    updatedAt: proposal.updatedAt,
  };
}

export const canonicalHandlers = [
  /**
   * The shell's bootstrap payload, on the canonical versionless surface since
   * the `/api/v1` original moved into `bootstrap/`.
   *
   * Kept here rather than deleted: it lived in the Data Console handlers until
   * Wave F4 removed them, which is how deleting the legacy *frontend* briefly
   * took down the canonical one. The provider no longer blocks first paint, so
   * a missing handler would now be a silent gap instead of a visible one --
   * which is a better app and a worse test signal.
   */
  http.get("/api/runtime-config", async () => {
    await delay(50);
    return HttpResponse.json(
      envelope(
        {
          releaseId: "mock-baseline",
          environment: "development",
          apiBasePath: "/api",
          features: { orderDiscoveryCopilot: true },
          capabilities: {
            availableSourceTypes: ["MONGODB", "NEO4J", "SQLSERVER"],
            availableModelProviders: ["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC"],
          },
        },
        "runtime-config",
      ),
    );
  }),

  /**
   * One discovery turn. Scripted, not simulated: the first message asks a
   * clarifying question, the second returns candidates with evidence, so the
   * copilot's two reportable pipeline stages are both reachable in mock mode
   * without a model or a graph.
   */
  http.post("/api/v2/order-agent/conversations/:id/turns", async ({ request, params }) => {
    await delay(600);
    const body = (await request.json()) as { message: string; expected_conversation_version: number };
    const version = body.expected_conversation_version + 1;
    const asked = body.expected_conversation_version > 0;
    // A third turn narrows to one order, and that order is SESSION's, so the
    // later milestones have a real return session to report on. Two candidates
    // on turn two is the ambiguous case and must stay unresolved.
    const narrowed = body.expected_conversation_version > 1;

    return HttpResponse.json(
      envelope(
        {
          conversation_id: String(params.id),
          conversation_version: version,
          client_turn_id: `mock-turn-${String(version)}`,
          graph_generation_id: "gen-0f3c9a11-mock",
          model_provider: "MOCK",
          model_name: "scripted",
          pending_clarification_thread_id: asked ? null : "thread-mock-1",
          response: {
            status: asked ? "RESOLVED" : "NEEDS_INPUT",
            business_capability: "order_discovery",
            suggestions: asked ? [] : ["Show next", "Search by SKU instead"],
            requested_input: asked ? null : "Which branch was the order placed at?",
            statements: asked
              ? [
                  {
                    statement_id: "s-2",
                    statement_type: "GRAPH_FACT",
                    text: "Found 2 orders for ATLAS MECHANICAL SERVICES in CHARLOTTE.",
                    evidence_refs: ["qe-1"],
                  },
                ]
              : [
                  {
                    statement_id: "s-0",
                    statement_type: "USER_PROVIDED_FACT",
                    text: body.message,
                  },
                  {
                    statement_id: "s-1",
                    statement_type: "CLARIFICATION_QUESTION",
                    text: "Which branch was the order placed at?",
                  },
                ],
          },
          query_evidence: asked
            ? [
                {
                  query_execution_id: "qe-1",
                  schema_version: "return-order-v2",
                  graph_generation_id: "gen-0f3c9a11-mock",
                  result_checksum: "mock",
                  result: {
                    candidates: narrowed
                      ? [
                          {
                            candidate_id: "CHARLOTTE*ORD-88123",
                            data: {
                              sales_order_number: SESSION.orderReference,
                              account_id: "CHARLOTTE",
                              customer_name: "ATLAS MECHANICAL SERVICES",
                              order_status: "CALLCSR",
                            },
                          },
                        ]
                      : [
                          {
                            candidate_id: "CHARLOTTE*CQ363350",
                            data: {
                              sales_order_number: "CQ363350",
                              account_id: "CHARLOTTE",
                              customer_name: "ATLAS MECHANICAL SERVICES",
                              order_status: "CALLCSR",
                            },
                          },
                          {
                            candidate_id: "LAKEWOOD*CT275260",
                            data: {
                              sales_order_number: "CT275260",
                              account_id: "LAKEWOOD",
                              customer_name: "ACED HEATING & COOLING",
                              order_status: "INVOICED",
                            },
                          },
                        ],
                  },
                },
              ]
            : [],
        },
        "order-agent-turn",
      ),
    );
  }),

  http.get("/api/principal", async () => {
    await delay(50);
    return HttpResponse.json(
      envelope(
        { subject: "mock-operator", roles: ["CONSOLE_ADMIN"], capabilities: ALL_CAPABILITIES },
        "principal",
      ),
    );
  }),

  // --- returns ---------------------------------------------------------------

  // Earlier returns, from the graph. Answers with one case carrying one issued
  // RMA, its items and a staged parcel, because the panel's whole job is to
  // distinguish "nothing came back before" from "this line is already on an RMA
  // sitting in a bay" -- and an empty fixture would only exercise the first.
  http.get("/api/return-history", async ({ request }) => {
    await delay(80);
    const query = new URL(request.url).searchParams;
    return HttpResponse.json(
      envelope(
        {
          orderReference: query.get("orderReference"),
          accountId: query.get("accountId"),
          customerId: query.get("customerId"),
          cases: [
            {
              caseId: "case-mock-1",
              status: "AWAITING_SUPPORT",
              confirmedOrderReference: "CW273354",
              createdAt: "2026-07-02T10:15:00Z",
              returnRecords: [
                {
                  returnRecordId: "rec-mock-1",
                  returnReference: "RMA-1001",
                  status: "ISSUED",
                  returnLocation: "DC-7",
                  trackingReference: "1Z999AA10123456784",
                  items: [
                    {
                      returnItemId: "item-mock-1",
                      orderLineReference: "L1",
                      productReference: "3180140",
                      quantity: 1,
                      reason: "Damaged on arrival",
                    },
                  ],
                },
              ],
              unassignedItems: [
                {
                  returnItemId: "item-mock-2",
                  orderLineReference: "L2",
                  productReference: "3180141",
                  quantity: 2,
                  reason: null,
                },
              ],
              placements: [
                {
                  handlingUnitId: "sess-mock-1:HU:1",
                  handlingUnitType: "PACKAGE",
                  physicalStatus: "WAREHOUSE_STAGED",
                  warehouseId: "1969",
                  bayId: "BAY-04",
                  trackingNumber: "1Z999AA10123456784",
                },
              ],
            },
          ],
        },
        "return-history",
      ),
    );
  }),

  http.get("/api/returns", async () => {
    await delay(80);
    return HttpResponse.json(envelope([SESSION], "returns-list"));
  }),
  http.get("/api/returns/:sessionId", async () => {
    await delay(80);
    return HttpResponse.json(envelope(SESSION, "return"));
  }),
  http.get("/api/returns/:sessionId/timeline", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope(
        [
          {
            id: "evt-1",
            streamId: SESSION.id,
            sequence: 1,
            eventType: "DISCOVERY_CONFIRMED",
            actorType: "USER",
            actorId: "associate-7",
            payload: {},
            occurredAt: "2026-08-09T09:05:00Z",
            publishedAt: null,
          },
          {
            id: "evt-2",
            streamId: SESSION.id,
            sequence: 2,
            eventType: "BOL_TENDERED",
            actorType: "SYSTEM",
            actorId: "return-platform-service",
            payload: {},
            occurredAt: "2026-08-10T11:00:00Z",
            publishedAt: null,
          },
        ],
        "timeline",
      ),
    );
  }),
  http.get("/api/returns/:sessionId/support", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope(
        {
          case: {
            caseType: "FLOW_FAILURE",
            status: "OPEN",
            priority: "HIGH",
            slaBreached: false,
          },
          workItem: {
            subject: "Customer chasing collection date",
            status: "IN_PROGRESS",
            queue: "SUPPORT",
          },
        },
        "support",
      ),
    );
  }),
  http.get("/api/returns/:sessionId/conversation", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope(
        {
          id: "conv-mock-1",
          returnSessionId: SESSION.id,
          messages: [
            { id: "m1", role: "associate", content: "Customer says two of the four arrived cracked." },
            { id: "m2", role: "assistant", content: "Found order ORD-88123 with four line items." },
          ],
        },
        "conversation",
      ),
    );
  }),
  http.post("/api/returns/:sessionId/events", async () => {
    await delay(120);
    // A refusal, deliberately: the interesting path to look at is the one that
    // shows the state machine's reason, and a mock that always succeeds would
    // never exercise it.
    return HttpResponse.json(
      { detail: "BOL_TENDERED is already recorded for this return." },
      { status: 409 },
    );
  }),

  // --- config ----------------------------------------------------------------

  http.get("/api/config/runtime", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope(
        {
          release_id: "rel-mock-1",
          checksum_sha256: "9f2c1a",
          source: "GRAPH",
          configuration: { integrations: { omc: { enabled: true } } },
        },
        "runtime",
      ),
    );
  }),
  http.get("/api/config/releases", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope(
        [
          {
            release_id: "rel-mock-1",
            status: "RELEASED",
            checksum: "9f2c1a",
            created_at: "2026-08-01T09:00:00Z",
          },
          {
            release_id: "rel-mock-2",
            status: "VALIDATED",
            checksum: "3b7d02",
            created_at: "2026-08-09T09:00:00Z",
          },
        ],
        "releases",
      ),
    );
  }),
  http.get("/api/config/releases/:releaseId", async ({ params }) => {
    await delay(80);
    const releaseId = String(params.releaseId);
    return HttpResponse.json(
      envelope(
        {
          release_id: releaseId,
          status: releaseId === "rel-mock-1" ? "RELEASED" : "VALIDATED",
          checksum: releaseId === "rel-mock-1" ? "9f2c1a" : "3b7d02",
          created_at: "2026-08-09T09:00:00Z",
          domains: { return_platform: { workflow: { version: "2.1" } } },
        },
        "release",
      ),
    );
  }),
  http.post("/api/config/releases/:releaseId/promote", async () => {
    await delay(150);
    return HttpResponse.json(
      { detail: "expected_head_revision is required to publish a configuration release" },
      { status: 422 },
    );
  }),
  // --- data sources (UI-02) ---------------------------------------------------
  //
  // `SourceItem` / `SourceDetail`, field for field. The previous fixture here
  // invented `{sourceId, kind, status}`, which no backend model has ever
  // served -- it went unnoticed because the old configuration tab rendered the
  // payload as raw JSON and could not be wrong about a field it never named.
  // A degraded source is included on purpose: a fixture where everything is
  // healthy exercises none of what the screen is for.

  http.get("/api/config/sources", async () => {
    await delay(80);
    return HttpResponse.json(envelope(SOURCES.map(sourceItem), "sources"));
  }),
  http.get("/api/config/sources/:sourceId", async ({ params }) => {
    await delay(80);
    const source = SOURCES.find((candidate) => candidate.id === String(params.sourceId));
    if (source === undefined) {
      return HttpResponse.json({ detail: "Source not found." }, { status: 404 });
    }
    return HttpResponse.json(envelope(source, "source"));
  }),

  http.get("/api/source-bindings", async () => {
    await delay(80);
    return HttpResponse.json(envelope(BINDINGS, "source-bindings"));
  }),
  http.put("/api/source-bindings/:dataset", async ({ params, request }) => {
    await delay(120);
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      envelope({ ...body, dataset: String(params.dataset), overridden: true }, "rebind"),
    );
  }),
  http.delete("/api/source-bindings/:dataset", async () => {
    await delay(120);
    return HttpResponse.json(envelope({ removed: true }, "clear-binding"));
  }),

  // --- approvals (UI-01) ------------------------------------------------------

  http.get("/api/proposals", async ({ request }) => {
    await delay(80);
    const query = new URL(request.url).searchParams;
    const status = query.get("status");
    const type = query.get("type");
    const rows = PROPOSALS.filter(
      (proposal) =>
        (status === null || proposal.status === status) &&
        (type === null || proposal.proposalType === type),
    ).map(proposalSummary);
    return HttpResponse.json(envelope(rows, "proposals"));
  }),
  http.get("/api/proposals/:proposalId", async ({ params }) => {
    await delay(80);
    const proposal = PROPOSALS.find((row) => row.proposalId === String(params.proposalId));
    if (proposal === undefined) {
      return HttpResponse.json(
        { detail: { code: "UNKNOWN_PROPOSAL", message: "no proposal." } },
        { status: 404 },
      );
    }
    return HttpResponse.json(envelope(proposal, "proposal"));
  }),
  http.post("/api/proposals/:proposalId/approve", async ({ params, request }) => {
    await delay(150);
    const body = (await request.json()) as { note: string | null };
    const proposal = PROPOSALS.find((row) => row.proposalId === String(params.proposalId));
    return HttpResponse.json(
      envelope(
        {
          ...(proposal ?? PROPOSALS[0]),
          status: "APPROVED",
          decidedBy: "mock-operator",
          decisionNote: body.note,
        },
        "approve",
      ),
    );
  }),
  http.post("/api/proposals/:proposalId/reject", async () => {
    await delay(150);
    // A refusal, deliberately: the path worth looking at is the one that shows
    // the kernel's own reason, and a mock that always succeeds never reaches it.
    return HttpResponse.json(
      {
        detail: {
          code: "INVALID_TRANSITION",
          message: "proposal prop-mock-1 is APPROVED; REJECTED is not reachable from there.",
        },
      },
      { status: 409 },
    );
  }),
  http.post("/api/proposals/:proposalId/activate", async ({ params }) => {
    await delay(200);
    const proposal = PROPOSALS.find((row) => row.proposalId === String(params.proposalId));
    return HttpResponse.json(
      envelope(
        { ...(proposal ?? PROPOSALS[0]), status: "ACTIVATED", activationReference: "rel-mock-3" },
        "activate",
      ),
    );
  }),
  http.get("/api/config/audit", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope(
        [{ action: "PROMOTE_RELEASE", target: "rel-mock-1", actor: "mock-operator" }],
        "audit",
      ),
    );
  }),

  // --- source sync (S6) ------------------------------------------------------
  //
  // Both kinds of run, because the whole point of the screen is that they share
  // one history: a scheduled sweep and a single record an agent pulled in
  // mid-conversation. A fixture with only the first would render a screen that
  // looks finished and hides half of what it is for.

  http.get("/api/graph-sync/runs", async ({ request }) => {
    await delay(80);
    const mode = new URL(request.url).searchParams.get("mode");
    const runs = [SYNC_RUN_ON_DEMAND, SYNC_RUN_FULL].filter(
      (run) => mode === null || run.mode === mode,
    );
    return HttpResponse.json(envelope(runs, "sync-runs"));
  }),
  http.get("/api/graph-sync/runs/:runId", async ({ params }) => {
    await delay(80);
    const run = params.runId === SYNC_RUN_FULL.id ? SYNC_RUN_FULL : SYNC_RUN_ON_DEMAND;
    return HttpResponse.json(envelope(run, "sync-run"));
  }),
  http.post("/api/graph-sync/runs", async () => {
    // Slow on purpose: the real endpoint awaits the sync, and a mock that
    // answered instantly would hide the pending state the screen renders.
    await delay(1200);
    return HttpResponse.json(envelope(SYNC_RUN_FULL, "sync-run-started"));
  }),

  // --- graph schema ----------------------------------------------------------

  http.get("/api/graph-schema/analyses", async () => {
    await delay(80);
    return HttpResponse.json([
      {
        analysis_id: "an-mock-1",
        status: "NEEDS_HUMAN_REVIEW",
        source_refs: ["mongo_main"],
        created_by: "mock-operator",
        created_at: "2026-08-14T09:00:00Z",
        updated_at: "2026-08-14T09:05:00Z",
        version: 1,
        snapshot_id: "snapshot-mock-1",
        draft_id: "dr-mock-1",
        failure_reason: null,
      },
    ]);
  }),
  http.get("/api/graph-schema/drafts/:draftId", async () => {
    await delay(80);
    return HttpResponse.json({
      draft_id: "dr-mock-1",
      analysis_id: "an-mock-1",
      status: "DRAFT",
      current_revision: 2,
      version: 1,
      validation_result_id: null,
      entity_count: 2,
      relationship_count: 1,
    });
  }),
  http.get("/api/graph-schema/drafts/:draftId/shape", async () => {
    await delay(80);
    return HttpResponse.json({
      entities: {
        Order: {
          label: "Order",
          source_dataset: "orders",
          properties: {
            order_id: { type: "STRING", source_field: "order_id", transformation: "NONE" },
            placed_at: { type: "DATETIME", source_field: "created", transformation: "TO_UTC" },
            total: { type: "FLOAT", source_field: "line_items.amount", transformation: "SUM" },
          },
          identifier_properties: ["order_id"],
          ownership: "SOURCE",
          sync_mode: "INCREMENTAL",
        },
        Customer: {
          label: "Customer",
          source_dataset: "customers",
          properties: {
            customer_id: { type: "STRING", source_field: "cust_id", transformation: "NONE" },
            name: { type: "STRING", source_field: "full_name", transformation: "NONE" },
          },
          identifier_properties: ["customer_id"],
          ownership: "SOURCE",
          sync_mode: "FULL",
        },
      },
      relationships: [
        {
          relationship_type: "PLACED_BY",
          from_label: "Order",
          to_label: "Customer",
          cardinality: "MANY_TO_ONE",
        },
      ],
      graph_indexes: [{ label: "Order", properties: ["order_id"] }],
      graph_constraints: [
        { label: "Order", property_name: "order_id", unique: true, required: true },
      ],
    });
  }),
  http.get("/api/graph-schema/drafts/:draftId/revisions", async () => {
    await delay(80);
    return HttpResponse.json([]);
  }),
  http.get("/api/graph-schema/analyses/:analysisId/clarifications", async () => {
    await delay(80);
    return HttpResponse.json([]);
  }),

  // --- ai --------------------------------------------------------------------

  http.get("/api/ai/routes", () => HttpResponse.json(envelope([], "routes"))),
  http.get("/api/ai/tasks", () => HttpResponse.json(envelope([], "tasks"))),
  http.get("/api/ai/metrics", () => HttpResponse.json(envelope([], "metrics"))),
  http.get("/api/ai/metrics/summary", () =>
    // The whole `AIUsageSummaryView`, not a convenient subset. The Breakdown
    // component calls `Object.entries` on the four maps, so omitting them
    // crashes the console through the error boundary -- which is what a
    // half-written fixture here did, and it looked like a page bug.
    HttpResponse.json(
      envelope(
        {
          attempts: 0,
          successes: 0,
          failures: 0,
          fallbacks: 0,
          blockedBySafety: 0,
          inputTokens: 0,
          outputTokens: 0,
          totalTokens: 0,
          estimatedCostMicros: 0,
          pricingCurrency: null,
          unpricedAttempts: 0,
          cachedInputTokens: 0,
          byProvider: {},
          byModel: {},
          byTask: {},
          byTier: {},
        },
        "summary",
      ),
    ),
  ),
  http.get("/api/ai/interceptions", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope(
        [
          {
            interceptionId: "int-mock-1",
            taskId: "GRAPH_SCHEMA_PROPOSAL_V1",
            status: "PENDING",
            createdAt: "2026-08-10T10:00:00Z",
            expiresAt: "2026-08-10T18:00:00Z",
            answeredBy: null,
          },
        ],
        "interceptions",
      ),
    );
  }),
  http.get("/api/ai/interceptions/:id/request", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope({ prompt: "Propose a graph schema for the orders dataset." }, "request"),
    );
  }),
  http.post("/api/ai/interceptions/:id/answer", async () => {
    await delay(120);
    return HttpResponse.json(
      envelope(
        {
          interceptionId: "int-mock-1",
          status: "ANSWERED",
          answeredBy: "mock-operator",
          answeredAt: "2026-08-10T12:00:00Z",
        },
        "answer",
      ),
    );
  }),
  http.post("/api/ai/interceptions/:id/cancel", async () => {
    await delay(120);
    return HttpResponse.json(
      envelope({ interceptionId: "int-mock-1", status: "CANCELLED" }, "cancel"),
    );
  }),
];
