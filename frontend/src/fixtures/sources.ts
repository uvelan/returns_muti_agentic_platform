import { type SourceDetail, type SourceItem } from "../contracts/sources";

export const FIXTURE_SOURCES: SourceItem[] = [
  {
    id: "src-sql-omc",
    name: "OMC SQL Server",
    engine: "SQL_SERVER",
    environment: "PRODUCTION",
    ownership: "AUTHORITATIVE",
    health: "HEALTHY",
    capability: "READ_ONLY",
    lastInventoryTime: new Date(Date.now() - 3600000).toISOString(),
  },
  {
    id: "src-mongo-returns",
    name: "Returns MongoDB",
    engine: "MONGODB",
    environment: "PRODUCTION",
    ownership: "AUTHORITATIVE",
    health: "HEALTHY",
    capability: "READ_ONLY",
    lastInventoryTime: new Date(Date.now() - 7200000).toISOString(),
  },
  {
    id: "src-neo4j-graph",
    name: "Unified Returns Graph",
    engine: "NEO4J",
    environment: "PRODUCTION",
    ownership: "DERIVED",
    health: "HEALTHY",
    capability: "READ_ONLY",
    lastInventoryTime: new Date(Date.now() - 1800000).toISOString(),
  },
  {
    id: "src-mongo-platform",
    name: "Platform System State",
    engine: "PLATFORM",
    environment: "PRODUCTION",
    ownership: "INTERNAL",
    health: "HEALTHY",
    capability: "READ_ONLY",
    lastInventoryTime: new Date(Date.now() - 900000).toISOString(),
  },
  {
    id: "src-sandbox-1",
    name: "Test Scenario Workspace",
    engine: "PLATFORM",
    environment: "SANDBOX",
    ownership: "SYNTHETIC",
    health: "HEALTHY",
    capability: "WRITABLE",
    lastInventoryTime: new Date().toISOString(),
  }
];

export const FIXTURE_SOURCE_DETAILS: Record<string, SourceDetail> = {
  "src-sql-omc": {
    ...FIXTURE_SOURCES[0],
    connectionIdentity: "omc-prod-db-cluster-01",
    inventoryTotals: { assets: 142, records: 15420000 },
    lastMetadataRefresh: new Date(Date.now() - 3600000).toISOString(),
    dependencyWarnings: [],
    assets: [],
  },
  "src-mongo-returns": {
    ...FIXTURE_SOURCES[1],
    connectionIdentity: "ret-mongo-replica-set",
    inventoryTotals: { assets: 24, records: 830000 },
    lastMetadataRefresh: new Date(Date.now() - 7200000).toISOString(),
    dependencyWarnings: [],
    assets: [],
  },
  "src-neo4j-graph": {
    ...FIXTURE_SOURCES[2],
    connectionIdentity: "neo4j-aura-prod",
    inventoryTotals: { assets: 12, records: 4500000 },
    lastMetadataRefresh: new Date(Date.now() - 1800000).toISOString(),
    dependencyWarnings: ["Graph sync is 2 hours behind source updates"],
    assets: [],
  },
  "src-mongo-platform": {
    ...FIXTURE_SOURCES[3],
    connectionIdentity: "platform-internal-db",
    inventoryTotals: { assets: 15, records: 120000 },
    lastMetadataRefresh: new Date(Date.now() - 900000).toISOString(),
    dependencyWarnings: [],
    assets: [],
  },
  "src-sandbox-1": {
    ...FIXTURE_SOURCES[4],
    connectionIdentity: "sandbox-tenant-12a",
    inventoryTotals: { assets: 4, records: 150 },
    lastMetadataRefresh: new Date().toISOString(),
    dependencyWarnings: ["This is a synthetic sandbox workspace"],
    assets: [],
  }
};
