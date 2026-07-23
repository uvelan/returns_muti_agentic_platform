import { type BrowserAsset, type BrowserRecord } from "../contracts/browser";

export const FIXTURE_BROWSER_ASSETS: BrowserAsset[] = [
  {
    assetId: "sales-orders",
    sourceId: "src-sql-omc",
    engine: "SQL_SERVER",
    name: "SalesOrders",
    ownership: "AUTHORITATIVE",
    capability: "READ_ONLY",
    recordCount: 4500000,
    schemaVersion: "1.0",
  },
  {
    assetId: "returns",
    sourceId: "src-mongo-returns",
    engine: "MONGODB",
    name: "Returns",
    ownership: "AUTHORITATIVE",
    capability: "READ_ONLY",
    recordCount: 120000,
    schemaVersion: "2.1",
  },
  {
    assetId: "Order",
    sourceId: "src-neo4j-graph",
    engine: "NEO4J",
    name: "Order",
    ownership: "DERIVED",
    capability: "READ_ONLY",
    recordCount: 4500000,
    schemaVersion: "1.0",
  },
  {
    assetId: "sandbox-orders",
    sourceId: "src-sandbox-1",
    engine: "PLATFORM",
    name: "SandboxOrders",
    ownership: "SYNTHETIC",
    capability: "WRITABLE",
    recordCount: 50,
    schemaVersion: "1.0",
  }
];

export const FIXTURE_BROWSER_RECORDS: Record<string, BrowserRecord[]> = {
  "src-sql-omc:sales-orders": [
    {
      kind: "SQL_ROW",
      identity: { id: "SO-1001", assetId: "sales-orders", engine: "SQL_SERVER" },
      data: {
        OrderNumber: "SO-1001",
        CustomerNumber: "C-500",
        TotalAmount: 145.50,
        OrderDate: "2026-07-20T10:00:00Z",
        Status: "SHIPPED",
        SecretHash: "********"
      },
      fields: {
        OrderNumber: { type: "STRING", redacted: false },
        CustomerNumber: { type: "STRING", redacted: false },
        TotalAmount: { type: "NUMBER", redacted: false },
        OrderDate: { type: "DATETIME", redacted: false },
        Status: { type: "STRING", redacted: false },
        SecretHash: { type: "REDACTED", redacted: true }
      }
    }
  ],
  "src-mongo-returns:returns": [
    {
      kind: "MONGO_DOCUMENT",
      identity: { id: "RET-9001", assetId: "returns", engine: "MONGODB" },
      data: {
        _id: "RET-9001",
        orderId: "SO-1001",
        reason: "Defective",
        items: [
          { sku: "SKU-A", qty: 1 }
        ],
        createdAt: "2026-07-22T08:30:00Z"
      },
      redactedPaths: []
    }
  ],
  "src-neo4j-graph:Order": [
    {
      kind: "NEO4J_NODE",
      identity: { id: "n-1001", assetId: "Order", engine: "NEO4J" },
      labels: ["Order"],
      properties: {
        orderId: "SO-1001",
        total: 145.50
      },
      propertyTypes: {
        orderId: { type: "STRING", redacted: false },
        total: { type: "NUMBER", redacted: false }
      }
    }
  ],
  "src-sandbox-1:sandbox-orders": [
    {
      kind: "SQL_ROW", // Represented as row for simplicity
      identity: { id: "TEST-SO-1", assetId: "sandbox-orders", engine: "PLATFORM" },
      data: {
        OrderNumber: "TEST-SO-1",
        Status: "PENDING"
      },
      fields: {
        OrderNumber: { type: "STRING", redacted: false },
        Status: { type: "STRING", redacted: false }
      }
    }
  ]
};

