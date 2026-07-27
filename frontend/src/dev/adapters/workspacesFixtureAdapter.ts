import type { WorkspaceMutationPort, RecordMutationPayload } from "../../api/ports/workspacesPort";
import type { Workspace, SandboxRecord } from "../../contracts/workspaces";
import type { APIResponse } from "../../contracts/api";

const MOCK_WORKSPACES: Workspace[] = [
  {
    id: "ws-isolated-1",
    name: "Sales Q3 Pipeline Workspace",
    description: "Temporary workspace for modeling Q3 adjustments.",
    isSandbox: true,
    owner: "alice@example.com",
    createdAt: "2026-07-22T10:00:00Z",
    updatedAt: "2026-07-22T10:00:00Z",
    version: 1,
    recordCount: 15
  },
  {
    id: "ws-isolated-2",
    name: "Compliance Drafts",
    description: "Review of compliance exceptions.",
    isSandbox: true,
    owner: "bob@example.com",
    createdAt: "2026-07-21T09:00:00Z",
    updatedAt: "2026-07-21T09:00:00Z",
    version: 1,
    recordCount: 42
  }
];

const MOCK_RECORDS: Record<string, SandboxRecord | undefined> = {
  "rec-1": {
    id: "rec-1",
    data: {
      accountName: "Acme Corp",
      projectedRevenue: 150000,
      status: "DRAFT"
    },
    createdAt: "2026-07-22T10:05:00Z",
    updatedAt: "2026-07-22T10:15:00Z",
    version: 1,
    validationStatus: "WARNING",
    issues: [{ message: "Projected revenue exceeds historical average", field: "projectedRevenue" }]
  }
};

function makeMeta() {
  return {
    schema_version: "1.0",
    request_id: `req-ws-${String(Date.now())}`,
    generated_at: new Date().toISOString(),
    freshness: "LIVE" as const,
    partial: false,
    warnings: []
  };
}

export function createFixtureWorkspaceAdapter(): WorkspaceMutationPort {
  return {
    async listWorkspaces(_options?: { signal?: AbortSignal }): Promise<APIResponse<Workspace[]>> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return { data: [...MOCK_WORKSPACES], meta: makeMeta(), page: null };
    },

    async getWorkspace(workspaceId: string, _options?: { signal?: AbortSignal }): Promise<Workspace> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const ws = MOCK_WORKSPACES.find(w => w.id === workspaceId);
      if (!ws) {
        throw new Error(`Workspace ${workspaceId} not found`);
      }
      return ws;
    },

    async createWorkspace(payload: { name: string; description: string; schemaId?: string }, _options?: { signal?: AbortSignal }): Promise<Workspace> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const newWs: Workspace = {
        id: `ws-mock-${String(Date.now())}`,
        name: payload.name,
        description: payload.description,
        isSandbox: true,
        owner: "currentUser@example.com",
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        version: 1,
        recordCount: 0
      };
      MOCK_WORKSPACES.push(newWs);
      return newWs;
    },

    async deleteWorkspace(workspaceId: string, _expectedVersion: number, _options?: { signal?: AbortSignal }): Promise<void> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const idx = MOCK_WORKSPACES.findIndex(w => w.id === workspaceId);
      if (idx !== -1) MOCK_WORKSPACES.splice(idx, 1);
    },

    async listRecords(_workspaceId: string, _options?: { signal?: AbortSignal }): Promise<APIResponse<SandboxRecord[]>> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return { data: Object.values(MOCK_RECORDS).filter((r): r is SandboxRecord => r !== undefined), meta: makeMeta(), page: null };
    },

    async createRecord(_workspaceId: string, payload: RecordMutationPayload, _options?: { signal?: AbortSignal }): Promise<SandboxRecord> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const newRec: SandboxRecord = {
        id: `rec-mock-${String(Date.now())}`,
        data: payload.data,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        version: 1,
        validationStatus: "VALID",
        issues: []
      };
      MOCK_RECORDS[newRec.id] = newRec;
      return newRec;
    },

    async getRecord(_workspaceId: string, recordId: string, _options?: { signal?: AbortSignal }): Promise<SandboxRecord> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const record = MOCK_RECORDS[recordId];
      if (!record) throw new Error("Record not found");
      return record;
    },

    async updateRecord(_workspaceId: string, recordId: string, _expectedVersion: number, payload: RecordMutationPayload, _options?: { signal?: AbortSignal }): Promise<SandboxRecord> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const existing = MOCK_RECORDS[recordId];
      if (!existing) throw new Error("Record not found");
      const updated: SandboxRecord = {
        ...existing,
        data: payload.data,
        updatedAt: new Date().toISOString(),
        version: existing.version + 1,
        validationStatus: "VALID",
        issues: []
      };
      MOCK_RECORDS[recordId] = updated;
      return updated;
    },

    async deleteRecord(_workspaceId: string, recordId: string, _expectedVersion: number, _options?: { signal?: AbortSignal }): Promise<void> {
      await new Promise(resolve => setTimeout(resolve, 400));
      if (MOCK_RECORDS[recordId]) {
        MOCK_RECORDS[recordId] = undefined;
      }
    }
  };
}
