import { apiClient } from "./client";

/**
 * How the graph got its data, and how to make it get it again.
 *
 * The backend has recorded every sync run since it was written and served that
 * record to nobody -- the Data Console routers that once read it were unmounted
 * in Wave F1 with the frontend that called them. So "did the graph sync" was an
 * unanswerable question in the console, right next to a copilot whose answers
 * depend entirely on the answer.
 *
 * Two kinds of run come back from one list. A scheduled one covers every
 * participating source. A targeted one covers a single record an agent pulled
 * in mid-conversation because the graph did not have it yet, and carries the
 * conversation that caused it. They are not separated because an operator
 * looking at an unexpected node should not have to guess which mechanism wrote
 * it.
 */

/** Which sources a run covered. `ON_DEMAND` is one record, fetched for an agent. */
export type SyncRunMode = "FULL" | "SOURCE_MONGODB" | "SQLSERVER" | "ON_DEMAND";

/**
 * `STALLED` is what a run whose process died becomes.
 *
 * The ledger row is written RUNNING and only an exception propagating ever
 * moved it, so a worker that was killed mid-rebuild left a row claiming to be
 * in progress indefinitely -- one had been RUNNING for fifteen hours with zero
 * node writes. A housekeeping pass now terminalizes unconfirmed runs.
 *
 * Separate from FAILED because the operator response differs: FAILED means the
 * sync ran and did not work, STALLED means nobody knows.
 */
export type SyncRunStatus = "RUNNING" | "COMPLETED" | "FAILED" | "STALLED";

/**
 * Which *records* a run read, as opposed to which sources it covered.
 *
 * A full run rescans every record in every participating source. An incremental
 * one resumes each source from the cursor its last run left behind. The two are
 * indistinguishable from a run's status -- both complete green -- so the run has
 * to say which it was, or nobody can tell an incremental sync from one that is
 * quietly rescanning production on every schedule tick.
 */
export type SyncRecordScope = "FULL" | "INCREMENTAL";

/**
 * The agent turn a targeted run was performed for.
 *
 * Anchor *field ids*, never anchor values: the anchor is an order number or a
 * customer identifier, and a run list an operator browses and exports is not
 * somewhere to accumulate those.
 */
export type SyncRunRequester = {
  agentId: string;
  conversationId: string;
  clientTurnId: string;
  entityId: string;
  strongAnchorId: string;
  anchorFieldIds: string[];
};

export type SyncRun = {
  id: string;
  mode: string;
  status: SyncRunStatus;
  schemaVersion: string;
  sourceCounts: Record<string, number>;
  nodeWrites: number;
  relationshipWrites: number;
  constraintsApplied: string[];
  configurationDigest: string;
  errorCode: string | null;
  startedBy: string;
  startedAt: string;
  completedAt: string | null;
  /** Targeted runs only -- a scheduled run writes into the one legacy generation. */
  graphGenerationId: string | null;
  requestDigest: string | null;
  requestedBy: SyncRunRequester | null;
  /** Optional: runs recorded before incremental sync existed carry neither field. */
  recordScope?: SyncRecordScope;
  /**
   * Participating sources an incremental run could not resume, because they
   * declare no cursor field. They did not sync, and the run still says
   * COMPLETED -- which is why this is shown rather than logged.
   */
  skippedSources?: string[];
};

export type StartSyncInput = {
  mode: Exclude<SyncRunMode, "ON_DEMAND">;
  maxRecordsPerAsset?: number;
  applySchema?: boolean;
  /** Resume each source from its last cursor instead of rescanning it. */
  incremental?: boolean;
};

export const graphSyncApi = {
  async listRuns(mode?: string): Promise<SyncRun[]> {
    const query = mode === undefined || mode === "" ? "" : `&mode=${encodeURIComponent(mode)}`;
    const response = await apiClient<SyncRun[]>(`/api/graph-sync/runs?limit=50${query}`);
    return response.data ?? [];
  },

  async readRun(runId: string): Promise<SyncRun> {
    const response = await apiClient<SyncRun>(
      `/api/graph-sync/runs/${encodeURIComponent(runId)}`,
    );
    if (!response.data) throw new Error("That sync run could not be read.");
    return response.data;
  },

  /**
   * Run a sync now. Resolves when the run has finished, not when it has started
   * -- the backend awaits it, so the returned run is already terminal.
   */
  async startRun(input: StartSyncInput): Promise<SyncRun> {
    const response = await apiClient<SyncRun>("/api/graph-sync/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    if (!response.data) throw new Error("The sync started but reported no run.");
    return response.data;
  },
};
