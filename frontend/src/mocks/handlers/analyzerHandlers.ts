import { HttpResponse, http } from "msw";

import type {
  AgentRecommendation,
  AnalyzerBootstrap,
  AnalyzerSource,
  GraphSchema,
  SyncRun,
} from "../../contracts/graphAnalyzer";

const sourceObjects = [
  {
    id: "source_sales:orders",
    name: "orders",
    kind: "collection" as const,
    path: ["sales", "orders"],
    selectable: true,
    estimatedRows: 18420,
    fields: [
      { name: "orderId", dataType: "string", nullable: false, identifier: true, indexed: true },
      { name: "customerId", dataType: "string", nullable: false, identifier: false, indexed: true },
      { name: "status", dataType: "string", nullable: false, identifier: false, indexed: false },
      { name: "total", dataType: "decimal", nullable: false, identifier: false, indexed: false },
    ],
    children: [],
  },
  {
    id: "source_sales:customers",
    name: "customers",
    kind: "collection" as const,
    path: ["sales", "customers"],
    selectable: true,
    estimatedRows: 6240,
    fields: [
      { name: "customerId", dataType: "string", nullable: false, identifier: true, indexed: true },
      { name: "displayName", dataType: "string", nullable: false, identifier: false, indexed: false },
      { name: "email", dataType: "string", nullable: true, identifier: false, indexed: false },
    ],
    children: [],
  },
];

let sources: AnalyzerSource[] = [
  {
    id: "source_sales",
    name: "Sales MongoDB",
    engine: "MONGODB",
    port: 27017,
    status: "CONNECTED",
    host: "sales.internal",
    database: "sales",
    username: "analyzer_reader",
    lastValidatedAt: "2026-08-17T15:10:00Z",
    objectCount: sourceObjects.length,
    objects: sourceObjects,
  },
  {
    id: "source_fulfillment",
    name: "Fulfillment PostgreSQL",
    engine: "POSTGRESQL",
    port: 5432,
    status: "CONNECTED",
    host: "fulfillment.internal",
    database: "fulfillment",
    username: "schema_reader",
    lastValidatedAt: "2026-08-17T15:08:00Z",
    objectCount: 1,
    objects: [
      {
        id: "source_fulfillment:shipments",
        name: "shipments",
        kind: "table",
        path: ["fulfillment", "public", "shipments"],
        selectable: true,
        estimatedRows: 12280,
        fields: [
          { name: "shipment_id", dataType: "uuid", nullable: false, identifier: true, indexed: true },
          { name: "order_id", dataType: "varchar", nullable: false, identifier: false, indexed: true },
          { name: "carrier", dataType: "varchar", nullable: true, identifier: false, indexed: false },
        ],
        children: [],
      },
    ],
  },
];

const entity = (id: string, name: string, x: number, y: number, sourceObjectId: string, fields: readonly string[]) => ({
  id,
  name,
  description: `${name} projected into the system-owned graph.`,
  x,
  y,
  properties: fields.map((field, index) => ({
    id: `${id}:${field}`,
    name: field,
    dataType: "string",
    required: index === 0,
    identifier: index === 0,
    indexed: index < 2,
    sourceObjectId,
    sourceField: field,
  })),
  constraints: [`UNIQUE ${fields[0]}`],
  change: "ADDED" as const,
});

let proposedSchema: GraphSchema = {
  id: "schema-proposal-7",
  version: 7,
  status: "READY",
  updatedAt: "2026-08-17T15:14:00Z",
  entities: [
    entity("order", "Order", 120, 100, "source_sales:orders", ["orderId", "customerId", "status", "total"]),
    entity("customer", "Customer", 520, 70, "source_sales:customers", ["customerId", "displayName", "email"]),
    entity("shipment", "Shipment", 520, 330, "source_fulfillment:shipments", ["shipment_id", "order_id", "carrier"]),
  ],
  relationships: [
    {
      id: "customer-placed-order",
      name: "PLACED",
      fromEntityId: "customer",
      toEntityId: "order",
      direction: "OUTBOUND",
      properties: [],
      sourceObjectId: "source_sales:orders",
      change: "ADDED",
    },
    {
      id: "order-has-shipment",
      name: "HAS_SHIPMENT",
      fromEntityId: "order",
      toEntityId: "shipment",
      direction: "OUTBOUND",
      properties: [],
      sourceObjectId: "source_fulfillment:shipments",
      change: "ADDED",
    },
  ],
};

const existingSchema: GraphSchema = {
  ...proposedSchema,
  id: "schema-active-6",
  version: 6,
  status: "FINALIZED",
  entities: proposedSchema.entities.slice(0, 2).map((item) => ({ ...item, change: "UNCHANGED" })),
  relationships: proposedSchema.relationships.slice(0, 1).map((item) => ({ ...item, change: "UNCHANGED" })),
};

let syncHistory: SyncRun[] = [
  {
    id: "sync-104",
    mode: "PARTIAL",
    status: "COMPLETED",
    scope: ["source_sales:orders"],
    currentSource: null,
    currentObject: null,
    currentActivity: "Completed",
    itemsRead: 420,
    itemsProcessed: 420,
    nodesWritten: 418,
    relationshipsWritten: 396,
    failedItems: 0,
    startedAt: "2026-08-17T14:20:00Z",
    completedAt: "2026-08-17T14:20:18Z",
    error: null,
  },
];

function bootstrap(): AnalyzerBootstrap {
  return {
    sources,
    existingSchema,
    proposedSchema,
    validation: { status: "VALID", checkedAt: "2026-08-17T15:14:00Z", issues: [] },
    activeAnalysis: null,
    activeSync: null,
    syncHistory,
  };
}

const ok = (data: unknown) => HttpResponse.json({ data, meta: { requestId: "mock-analyzer" } });

/** Read a string field from an untyped request body, falling back when absent. */
function text(body: Record<string, unknown>, key: string, fallback: string): string {
  const value = body[key];
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

/** Read a numeric field from an untyped request body. */
function count(body: Record<string, unknown>, key: string, fallback: number): number {
  const value = body[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function engineOf(body: Record<string, unknown>): AnalyzerSource["engine"] {
  const value = body.engine;
  return value === "MONGODB" || value === "POSTGRESQL" || value === "SQLSERVER" || value === "NEO4J"
    ? value
    : "MONGODB";
}

export const analyzerHandlers = [
  http.get("/api/graph-analyzer/v1/bootstrap", () => ok(bootstrap())),
  http.get("/api/graph-analyzer/v1/schemas", () => ok({ existing: existingSchema, proposed: proposedSchema })),
  http.get("/api/graph-analyzer/v1/sources/:sourceId/preview", () => ok({
    columns: ["orderId", "customerId", "status", "total"],
    rows: [
      { orderId: "ORD-10482", customerId: "CUS-901", status: "SHIPPED", total: 184.6 },
      { orderId: "ORD-10483", customerId: "CUS-144", status: "PROCESSING", total: 72.15 },
    ],
    page: 1,
    pageSize: 25,
    total: 18420,
    graph: null,
  })),
  http.post("/api/graph-analyzer/v1/sources/test", () => ok({ status: "CONNECTED", message: "Read-only connection validated." })),
  http.post("/api/graph-analyzer/v1/sources", async ({ request }) => {
    const input = await request.json() as Record<string, unknown>;
    const created: AnalyzerSource = {
      id: `source-${String(sources.length + 1)}`,
      name: text(input, "name", "New source"),
      engine: engineOf(input),
      port: count(input, "port", 0),
      status: "NOT_VALIDATED",
      host: text(input, "host", ""),
      database: text(input, "database", ""),
      username: typeof input.username === "string" ? input.username : null,
      lastValidatedAt: null,
      objectCount: 0,
      objects: [],
    };
    sources = [...sources, created];
    return ok(created);
  }),
  http.put("/api/graph-analyzer/v1/sources/:sourceId", async ({ params, request }) => {
    const input = await request.json() as Record<string, unknown>;
    const current = sources.find((source) => source.id === params.sourceId) ?? sources[0];
    const updated = { ...current, name: text(input, "name", current.name), host: text(input, "host", current.host), database: text(input, "database", current.database) };
    sources = sources.map((source) => source.id === updated.id ? updated : source);
    return ok(updated);
  }),
  http.delete("/api/graph-analyzer/v1/sources/:sourceId", ({ params }) => {
    sources = sources.filter((source) => source.id !== params.sourceId);
    return new HttpResponse(null, { status: 204 });
  }),
  http.post("/api/graph-analyzer/v1/sources/:sourceId/validate", ({ params }) => {
    const current = sources.find((source) => source.id === params.sourceId) ?? sources[0];
    const updated = { ...current, status: "CONNECTED" as const, lastValidatedAt: new Date().toISOString() };
    sources = sources.map((source) => source.id === updated.id ? updated : source);
    return ok(updated);
  }),
  http.post("/api/graph-analyzer/v1/sources/:sourceId/metadata", ({ params }) => ok(sources.find((source) => source.id === params.sourceId) ?? sources[0])),
  http.post("/api/graph-analyzer/v1/analyses", async ({ request }) => {
    const input = await request.json() as { selectedObjectIds?: string[] };
    return ok({ id: "analysis-32", status: "COMPLETED", stage: "COMPLETE", selectedObjectIds: input.selectedObjectIds ?? [], startedAt: new Date().toISOString(), completedAt: new Date().toISOString(), warningCount: 0 });
  }),
  http.get("/api/graph-analyzer/v1/analyses/:runId", ({ params }) => ok({ id: params.runId, status: "COMPLETED", stage: "COMPLETE", selectedObjectIds: [], startedAt: new Date().toISOString(), completedAt: new Date().toISOString(), warningCount: 0 })),
  http.put("/api/graph-analyzer/v1/schemas/proposed/entities/:entityId", async ({ request }) => {
    const updated = await request.json() as GraphSchema["entities"][number];
    proposedSchema = { ...proposedSchema, version: proposedSchema.version + 1, entities: proposedSchema.entities.map((item) => item.id === updated.id ? updated : item) };
    return ok(proposedSchema);
  }),
  http.put("/api/graph-analyzer/v1/schemas/proposed/relationships/:relationshipId", async ({ request }) => {
    const updated = await request.json() as GraphSchema["relationships"][number];
    proposedSchema = { ...proposedSchema, version: proposedSchema.version + 1, relationships: proposedSchema.relationships.map((item) => item.id === updated.id ? updated : item) };
    return ok(proposedSchema);
  }),
  http.post("/api/graph-analyzer/v1/schemas/proposed/validate", () => ok({ status: "VALID", checkedAt: new Date().toISOString(), issues: [] })),
  http.post("/api/graph-analyzer/v1/schemas/proposed/finalize", () => {
    proposedSchema = { ...proposedSchema, status: "FINALIZED", version: proposedSchema.version + 1 };
    return ok(proposedSchema);
  }),
  http.post("/api/graph-analyzer/v1/agent/messages", async ({ request }) => {
    const input = await request.json() as { message?: string };
    const recommendation: AgentRecommendation | null = input.message?.toLowerCase().includes("index") ? {
      id: "recommendation-8",
      summary: "Index Order.customerId in the system graph",
      rationale: "This supports cross-entity lookup without changing any source database.",
      target: "SYSTEM_GRAPH",
      status: "PENDING",
      operations: [{ kind: "SET_PROPERTY_INDEX", entityId: "order", propertyId: "order:customerId" }],
    } : null;
    return ok({ message: { id: crypto.randomUUID(), role: "AGENT", content: recommendation?.rationale ?? "The proposal is grounded in the selected read-only source metadata.", createdAt: new Date().toISOString() }, recommendation });
  }),
  http.post("/api/graph-analyzer/v1/agent/recommendations/:recommendationId", async ({ request }) => {
    const input = await request.json() as { decision?: "APPLY" | "REJECT" };
    const recommendation: AgentRecommendation = { id: "recommendation-8", summary: "Index Order.customerId in the system graph", rationale: "Reviewed by the operator.", target: "SYSTEM_GRAPH", status: input.decision === "APPLY" ? "APPLIED" : "REJECTED", operations: [] };
    return ok({ recommendation, proposedSchema });
  }),
  http.post("/api/graph-analyzer/v1/sync/runs", async ({ request }) => {
    const input = await request.json() as { mode?: "FULL" | "PARTIAL"; scope?: string[] };
    const run: SyncRun = { id: `sync-${String(105 + syncHistory.length)}`, mode: input.mode ?? "FULL", status: "COMPLETED", scope: input.scope ?? [], currentSource: null, currentObject: null, currentActivity: "Completed", itemsRead: 920, itemsProcessed: 920, nodesWritten: 908, relationshipsWritten: 861, failedItems: 0, startedAt: new Date().toISOString(), completedAt: new Date().toISOString(), error: null };
    syncHistory = [run, ...syncHistory];
    return ok(run);
  }),
  http.get("/api/graph-analyzer/v1/sync/runs/:runId", ({ params }) => ok(syncHistory.find((run) => run.id === params.runId) ?? syncHistory[0])),
];