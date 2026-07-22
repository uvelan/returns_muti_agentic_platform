import { describe, expect, it, vi } from "vitest";

import { APIError } from "./client";
import {
  getFullGraphEvidence,
  getGraphEvidenceByDocumentId,
  getGraphEvidenceByReportDigest,
  getGraphEvidenceBySyncRunId,
  getLatestGraphEvidence,
  listGraphEvidence,
} from "./graphEvidence";

const documentId = "CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7";
const syncRunId = "d084d10c-5bdf-4002-befb-8ccb9948f9e7";
const reportDigest = "75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8";
const requestId = "11111111-1111-4111-8111-111111111111";

const summary = {
  schema_version: "1.0",
  evidence_type: "CUSTOMER_GRAPH_SANDBOX_RUN",
  document_id: documentId,
  report_digest: reportDigest,
  document_digest: "6".repeat(64),
  sync_run_id: syncRunId,
  executed_at: "2026-07-22T11:13:47.868047Z",
  executed_at_epoch_microseconds: 1784718827868047,
  source_document_id: "P100",
  source_hash: "a".repeat(64),
  configuration_digest: "b".repeat(64),
  execution_plan_digest: "c".repeat(64),
  command_batch_digest: "d".repeat(64),
  evidence_classification: "SANDBOX_VALIDATED",
  expected_customer_count: 1,
  expected_customer_account_count: 2,
  expected_relationship_count: 2,
  idempotent: true,
};
const meta = {
  schema_version: "1.0",
  request_id: requestId,
  generated_at: "2026-07-22T13:10:26.930514Z",
  freshness: "LIVE",
  partial: false,
  warnings: [],
};

function response(payload: unknown, status = 200, contentType = "application/json") {
  return new Response(typeof payload === "string" ? payload : JSON.stringify(payload), {
    status,
    headers: { "Content-Type": contentType, "X-Correlation-ID": requestId },
  });
}

function fetchMock() {
  const mock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("graph evidence API", () => {
  it("constructs the bounded first-page list URL", async () => {
    const mock = fetchMock().mockResolvedValue(response({ data: [summary], page: { next_cursor: null, has_more: false, page_size: 25 }, meta }));
    await listGraphEvidence();
    expect(mock).toHaveBeenCalledWith("/data-console/v1/graph-evidence?page_size=25", expect.objectContaining({ method: "GET" }));
  });

  it("adds an encoded seek cursor", async () => {
    const mock = fetchMock().mockResolvedValue(response({ data: [summary], page: { next_cursor: null, has_more: false, page_size: 25 }, meta }));
    await listGraphEvidence("cursor+/=");
    expect(mock.mock.calls[0]?.[0]).toBe("/data-console/v1/graph-evidence?page_size=25&cursor=cursor%2B%2F%3D");
  });

  it.each([
    ["document", () => getGraphEvidenceByDocumentId(documentId), `/documents/${encodeURIComponent(documentId)}`],
    ["sync run", () => getGraphEvidenceBySyncRunId(syncRunId), `/sync-runs/${syncRunId}`],
    ["report digest", () => getGraphEvidenceByReportDigest(reportDigest), `/reports/${reportDigest}`],
  ])("constructs the exact %s lookup URL", async (_label, operation, suffix) => {
    const mock = fetchMock().mockResolvedValue(response({ data: summary, page: null, meta }));
    await operation();
    expect(mock.mock.calls[0]?.[0]).toBe(`/data-console/v1/graph-evidence${suffix}`);
  });

  it("strictly parses a valid latest response", async () => {
    fetchMock().mockResolvedValue(response({ data: summary, page: null, meta }));
    await expect(getLatestGraphEvidence()).resolves.toMatchObject({ data: { evidence_classification: "SANDBOX_VALIDATED" } });
  });

  it("rejects an unsupported classification", async () => {
    fetchMock().mockResolvedValue(response({ data: { ...summary, evidence_classification: "PRODUCTION_VALIDATED" }, page: null, meta }));
    await expect(getLatestGraphEvidence()).rejects.toMatchObject({ status: 502, correlationId: requestId });
  });

  it("safely rejects a non-JSON success response", async () => {
    fetchMock().mockResolvedValue(response("upstream html", 200, "text/html"));
    await expect(getLatestGraphEvidence()).rejects.toThrow("malformed JSON");
  });

  it("safely handles a malformed error response", async () => {
    fetchMock().mockResolvedValue(response({ unexpected: true }, 503));
    await expect(getLatestGraphEvidence()).rejects.toMatchObject({ status: 503, correlationId: requestId });
  });

  it("maps a browser timeout", async () => {
    fetchMock().mockRejectedValue(new DOMException("timed out", "TimeoutError"));
    await expect(getLatestGraphEvidence()).rejects.toThrow("timed out");
  });

  it("maps viewer denial on full evidence without exposing payload", async () => {
    fetchMock().mockResolvedValue(response({ data: null, page: null, meta: { ...meta, partial: true, warnings: [{ source: "graph-evidence", code: "FORBIDDEN", message: "Not authorized." }] } }, 403));
    const error = await getFullGraphEvidence(documentId).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(APIError);
    expect(error).toMatchObject({ status: 403, message: "Not authorized." });
  });
});
