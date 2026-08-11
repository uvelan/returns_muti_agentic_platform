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
  "graph_schema.draft.read",
  "graph_schema.draft.write",
  "graph_schema.generation.activate",
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
  http.get("/api/config/sources", async () => {
    await delay(80);
    return HttpResponse.json(
      envelope(
        [
          { sourceId: "mongo_main", kind: "MONGODB", status: "HEALTHY" },
          { sourceId: "trilogie_sql", kind: "SQLSERVER", status: "DEGRADED" },
        ],
        "sources",
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

  // --- graph schema ----------------------------------------------------------

  http.get("/api/graph-schema/analyses", async () => {
    await delay(80);
    return HttpResponse.json([
      {
        analysis_id: "an-mock-1",
        status: "PROPOSED",
        draft_id: "dr-mock-1",
        source_refs: ["mongo_main"],
      },
    ]);
  }),
  http.get("/api/graph-schema/drafts/:draftId", async () => {
    await delay(80);
    return HttpResponse.json({
      draft_id: "dr-mock-1",
      status: "DRAFT",
      current_revision: 2,
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
            total: { type: "FLOAT", source_field: null, transformation: "SUM" },
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
          estimatedCostMicrousd: 0,
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
