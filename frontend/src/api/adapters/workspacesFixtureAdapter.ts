import type { WorkspaceMutationPort } from "../ports/workspacesPort";
import type { Workspace, SandboxRecord } from "../../contracts/workspaces";
import type { APIResponse } from "../../contracts/api";

const MOCK_WORKSPACES: Workspace[] = [
  {
    id: "ws-sandbox-1",
    name: "Sales Q3 Pipeline Sandbox",
    description: "Temporary workspace for modeling Q3 adjustments.",
    isSandbox: true,
    owner: "alice@example.com",
    createdAt: "2026-07-22T10:00:00Z",
    recordCount: 15
  },
  {
    id: "ws-sandbox-2",
    name: "Compliance Drafts",
    description: "Review of compliance exceptions.",
    isSandbox: true,
    owner: "bob@example.com",
    createdAt: "2026-07-21T09:00:00Z",
    recordCount: 42
  }
];

const MOCK_RECORDS: Record<string, SandboxRecord> = {
  "rec-1": {
    id: "rec-1",
    data: {
      accountName: "Acme Corp",
      projectedRevenue: 150000,
      status: "DRAFT"
    },
    createdAt: "2026-07-22T10:05:00Z",
    updatedAt: "2026-07-22T10:15:00Z",
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
    async listWorkspaces(): Promise<APIResponse<Workspace[]>> {
      await new Promise(resolve => setTimeout(resolve, 300));
      return { data: [...MOCK_WORKSPACES], meta: makeMeta(), page: null };
    },

    async getWorkspace(workspaceId: string): Promise<Workspace> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const ws = MOCK_WORKSPACES.find(w => w.id === workspaceId);
      if (!ws) {
        throw new Error(`Workspace ${workspaceId} not found`);
      }
      return ws;
    },

    async createWorkspace(payload: { name: string; description: string; schemaId?: string }): Promise<Workspace> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const newWs: Workspace = {
        id: `ws-mock-${String(Date.now())}`,
        name: payload.name,
        description: payload.description,
        isSandbox: true,
        owner: "currentUser@example.com",
        createdAt: new Date().toISOString(),
        recordCount: 0
      };
      MOCK_WORKSPACES.push(newWs);
      return newWs;
    },

    async deleteWorkspace(workspaceId: string): Promise<void> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const idx = MOCK_WORKSPACES.findIndex(w => w.id === workspaceId);
      MOCK_WORKSPACES.splice(idx, 1);
    },

    async getRecord(_workspaceId: string, recordId: string): Promise<SandboxRecord> {
      await new Promise(resolve => setTimeout(resolve, 300));
      const record = MOCK_RECORDS[recordId];
      return record;
    },

    async updateRecord(_workspaceId: string, recordId: string, payload: { data: Record<string, unknown> }): Promise<SandboxRecord> {
      await new Promise(resolve => setTimeout(resolve, 400));
      const existing = MOCK_RECORDS[recordId];
      const updated: SandboxRecord = {
        ...existing,
        data: payload.data,
        updatedAt: new Date().toISOString(),
        validationStatus: "VALID",
        issues: []
      };
      MOCK_RECORDS[recordId] = updated;
      return updated;
    }
  };
}
